"""Tests for the waitlist-notify cron endpoint (cron.views.WaitlistNotifyView),
triggered by an external scheduler (e.g. Vercel Cron) via a Bearer secret."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from catalog.models import Book
from listings.models import Listing
from subscriptions.models import Subscription

User = get_user_model()

CRON_SECRET = "test-only-cron-secret"


@pytest.fixture
def api():
    return Client()


@pytest.fixture(autouse=True)
def cron_secret(settings):
    settings.CRON_SECRET = CRON_SECRET


def cron_auth(token=CRON_SECRET):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.fixture
def waitlister(db):
    return User.objects.create_user(email="waitlister@example.com", first_name="Wait", last_name="Lister", password="x")


@pytest.fixture
def seller(db):
    return User.objects.create_user(email="seller@example.com", first_name="Sell", last_name="Er", password="x")


@pytest.fixture
def book(db):
    return Book.objects.create(isbn13="9781111111111", title="Waitlisted Book", source="manual")


def _subscribe_before_now(user, book_obj):
    sub = Subscription.objects.create(user=user, book=book_obj)
    # created_at has auto_now_add — backdate it so a listing made "now" counts as new
    Subscription.objects.filter(id=sub.id).update(created_at=timezone.now() - timezone.timedelta(days=1))
    sub.refresh_from_db()
    return sub


def test_rejects_missing_auth_header(api, db):
    resp = api.get("/api/cron/waitlist-notify/")
    assert resp.status_code == 403


def test_rejects_wrong_token(api, db):
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth("wrong-token"))
    assert resp.status_code == 403


def test_rejects_when_cron_secret_unset(api, db, settings):
    settings.CRON_SECRET = ""
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.status_code == 403


def test_noop_when_nothing_due(api, db):
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.status_code == 200
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0}


def test_notifies_user_with_new_listing_and_updates_notified_at(api, waitlister, seller, book, mailoutbox):
    sub = _subscribe_before_now(waitlister, book)
    Listing.objects.create(book=book, seller=seller, price=100, condition='new', status='active')

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.status_code == 200
    assert resp.json() == {"notified_users": 1, "notified_subscriptions": 1}
    assert len(mailoutbox) == 1
    assert waitlister.email in mailoutbox[0].to[0]
    assert "Waitlisted Book" in mailoutbox[0].body

    sub.refresh_from_db()
    assert sub.notified_at is not None


def test_does_not_renotify_already_notified_subscription(api, waitlister, seller, book, mailoutbox):
    sub = _subscribe_before_now(waitlister, book)
    Listing.objects.create(book=book, seller=seller, price=100, condition='new', status='active')

    api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert len(mailoutbox) == 1

    # Running again with no new listing since must not re-send
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0}
    assert len(mailoutbox) == 1


def test_renotifies_when_another_new_listing_appears_after_last_notification(api, waitlister, seller, book, mailoutbox):
    sub = _subscribe_before_now(waitlister, book)
    Listing.objects.create(book=book, seller=seller, price=100, condition='new', status='active')
    api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert len(mailoutbox) == 1

    Listing.objects.create(book=book, seller=seller, price=150, condition='like_new', status='active')
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 1, "notified_subscriptions": 1}
    assert len(mailoutbox) == 2


def test_batches_multiple_due_subscriptions_for_same_user_into_one_email(api, waitlister, seller, book, mailoutbox):
    sub1 = _subscribe_before_now(waitlister, book)
    book2 = Book.objects.create(isbn13="9782222222222", title="Second Waitlisted Book", source="manual")
    sub2 = _subscribe_before_now(waitlister, book2)

    Listing.objects.create(book=book, seller=seller, price=100, condition='new', status='active')
    Listing.objects.create(book=book2, seller=seller, price=200, condition='new', status='active')

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 1, "notified_subscriptions": 2}
    assert len(mailoutbox) == 1
    assert "Waitlisted Book" in mailoutbox[0].body
    assert "Second Waitlisted Book" in mailoutbox[0].body


def test_ignores_non_active_listings(api, waitlister, seller, book, mailoutbox):
    _subscribe_before_now(waitlister, book)
    Listing.objects.create(book=book, seller=seller, price=100, condition='new', status='sold')

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0}
    assert len(mailoutbox) == 0


def test_ignores_listings_created_before_subscription(api, waitlister, seller, book, mailoutbox):
    Listing.objects.create(book=book, seller=seller, price=100, condition='new', status='active')
    # Subscribed after the listing already existed — nothing "new" for them
    Subscription.objects.create(user=waitlister, book=book)

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0}
    assert len(mailoutbox) == 0
