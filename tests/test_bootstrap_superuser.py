"""Tests for the initial-superuser bootstrap (accounts.apps._create_default_superuser),
which runs on Django's post_migrate signal, DEBUG=False only."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured

from accounts.apps import _create_default_superuser

User = get_user_model()


def test_noop_when_debug_true(db, settings):
    settings.DEBUG = True
    settings.DEFAULT_SUPERUSER_EMAIL = "admin@example.com"
    settings.DEFAULT_SUPERUSER_PASSWORD = "test-only-password-123"

    _create_default_superuser(sender=None)

    assert not User.objects.filter(is_superuser=True).exists()


def test_noop_when_superuser_already_exists(db, settings):
    settings.DEBUG = False
    settings.DEFAULT_SUPERUSER_EMAIL = "admin@example.com"
    settings.DEFAULT_SUPERUSER_PASSWORD = "test-only-password-123"
    User.objects.create_superuser(email="existing@example.com", first_name="Existing", last_name="Admin", password="x")

    _create_default_superuser(sender=None)

    assert User.objects.filter(is_superuser=True).count() == 1
    assert not User.objects.filter(email="admin@example.com").exists()


def test_noop_when_neither_env_var_set(db, settings):
    settings.DEBUG = False
    settings.DEFAULT_SUPERUSER_EMAIL = ""
    settings.DEFAULT_SUPERUSER_PASSWORD = ""

    _create_default_superuser(sender=None)

    assert not User.objects.filter(is_superuser=True).exists()


def test_raises_when_only_email_set(db, settings):
    settings.DEBUG = False
    settings.DEFAULT_SUPERUSER_EMAIL = "admin@example.com"
    settings.DEFAULT_SUPERUSER_PASSWORD = ""

    with pytest.raises(ImproperlyConfigured):
        _create_default_superuser(sender=None)

    assert not User.objects.filter(is_superuser=True).exists()


def test_raises_when_only_password_set(db, settings):
    settings.DEBUG = False
    settings.DEFAULT_SUPERUSER_EMAIL = ""
    settings.DEFAULT_SUPERUSER_PASSWORD = "test-only-password-123"

    with pytest.raises(ImproperlyConfigured):
        _create_default_superuser(sender=None)

    assert not User.objects.filter(is_superuser=True).exists()


def test_creates_superuser_when_both_set_and_none_exists(db, settings):
    settings.DEBUG = False
    settings.DEFAULT_SUPERUSER_EMAIL = "admin@example.com"
    settings.DEFAULT_SUPERUSER_PASSWORD = "test-only-password-123"
    settings.DEFAULT_SUPERUSER_FIRST_NAME = "Site"
    settings.DEFAULT_SUPERUSER_LAST_NAME = "Admin"

    _create_default_superuser(sender=None)

    user = User.objects.get(email="admin@example.com")
    assert user.is_superuser is True
    assert user.is_staff is True
    assert user.first_name == "Site"
    assert user.last_name == "Admin"
    assert user.check_password("test-only-password-123")
