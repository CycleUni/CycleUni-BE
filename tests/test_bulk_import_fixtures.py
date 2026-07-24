"""Exercises the real admin bulk-import endpoints (adminapi.views.
AdminSchoolBulkImportView / AdminCategoryBulkImportView) against the actual
JSON fixtures authored for the School/Category admin import feature, living in
the sibling SchoolList/ and CategoryList/ projects rather than inside this repo.
"""

import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from accounts.services import issue_tokens
from accounts.models import School
from core.models import Category

User = get_user_model()

SCHOOLS_FIXTURE = Path(__file__).resolve().parent.parent.parent / "SchoolList" / "schools.json"
CATEGORIES_FIXTURE = Path(__file__).resolve().parent.parent.parent / "CategoryList" / "categories.json"


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def staff_header(db):
    user = User.objects.create_user(
        email="bulk-import-staff@example.com", first_name="Bulk", last_name="Staff",
        password="test-only-password-123", is_staff=True,
    )
    user.verified_at = timezone.now()
    user.save(update_fields=["verified_at"])
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


def _load(fixture_path):
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)["items"]


@pytest.mark.skipif(not SCHOOLS_FIXTURE.exists(), reason="SchoolList/schools.json fixture not present")
def test_schools_fixture_preview_reports_all_as_new(api, staff_header, db):
    items = _load(SCHOOLS_FIXTURE)
    resp = api.post(
        "/api/v1/admin/schools/bulk/",
        data=json.dumps({"action": "preview", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["new"]) == len(items)
    assert body["modified"] == []
    assert body["unchanged"] == []
    # Preview must not have written anything
    assert School.objects.count() == 0


@pytest.mark.skipif(not SCHOOLS_FIXTURE.exists(), reason="SchoolList/schools.json fixture not present")
def test_schools_fixture_apply_creates_all_rows_and_is_idempotent(api, staff_header, db):
    items = _load(SCHOOLS_FIXTURE)

    resp = api.post(
        "/api/v1/admin/schools/bulk/",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    assert School.objects.count() == len(items)

    ntu = School.objects.get(email_domain="ntu.edu.tw")
    assert ntu.name == "National Taiwan University"
    assert ntu.translations["zh-TW"]["name"] == "國立臺灣大學"

    # Re-running the exact same import must not create duplicates or errors
    resp2 = api.post(
        "/api/v1/admin/schools/bulk/",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert len(body2["unchanged"]) == len(items)
    assert School.objects.count() == len(items)


@pytest.mark.skipif(not CATEGORIES_FIXTURE.exists(), reason="CategoryList/categories.json fixture not present")
def test_categories_fixture_matches_seed_migration_for_overlapping_slugs(api, staff_header, db):
    """core/migrations/0002_seed_categories.py already seeds 8 of this fixture's
    12 slugs on a fresh database. The fixture intentionally mirrors that
    migration's exact wording, so importing it should report those 8 as
    'unchanged' and only the 4 genuinely-new slugs as 'new'."""
    items = _load(CATEGORIES_FIXTURE)
    seeded_slugs = {
        "management", "engineering", "science", "liberal-arts",
        "medicine", "eecs", "law", "social-sciences",
    }

    resp = api.post(
        "/api/v1/admin/categories/bulk/",
        data=json.dumps({"action": "preview", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    unchanged_slugs = {i["slug"] for i in body["unchanged"]}
    new_slugs = {i["slug"] for i in body["new"]}
    assert seeded_slugs <= unchanged_slugs
    assert new_slugs == {"agriculture", "arts-design", "education", "dentistry"}
    assert body["modified"] == []
    # Preview must not have written the new rows
    assert Category.objects.filter(slug="dentistry").exists() is False


@pytest.mark.skipif(not CATEGORIES_FIXTURE.exists(), reason="CategoryList/categories.json fixture not present")
def test_categories_fixture_apply_creates_new_rows_and_is_idempotent(api, staff_header, db):
    items = _load(CATEGORIES_FIXTURE)

    resp = api.post(
        "/api/v1/admin/categories/bulk/",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    assert Category.objects.count() == len(items)

    dentistry = Category.objects.get(slug="dentistry")
    assert dentistry.title == "牙醫學院"
    assert dentistry.translations["en"]["title"] == "College of Dentistry"

    eecs = Category.objects.get(slug="eecs")
    assert eecs.title == "電資學院"
    assert eecs.translations["en"]["title"] == "College of EECS"

    # Re-running the exact same import must not create duplicates or errors
    resp2 = api.post(
        "/api/v1/admin/categories/bulk/",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert len(body2["unchanged"]) == len(items)
    assert Category.objects.count() == len(items)


@pytest.mark.skipif(not CATEGORIES_FIXTURE.exists(), reason="CategoryList/categories.json fixture not present")
def test_categories_fixture_detects_modification_against_seeded_row(api, staff_header, db):
    """If an admin has hand-edited a seeded category since, re-importing the
    canonical fixture must surface it as 'modified' (with a diff), not
    silently overwrite it or report it as unchanged."""
    stale = Category.objects.get(slug="engineering")
    stale.description = "手動編輯過的舊敘述"
    stale.save(update_fields=["description"])

    items = _load(CATEGORIES_FIXTURE)
    resp = api.post(
        "/api/v1/admin/categories/bulk/",
        data=json.dumps({"action": "preview", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    modified_slugs = {m["new"]["slug"] for m in body["modified"]}
    assert "engineering" in modified_slugs
    # Preview must not have mutated the existing row
    stale.refresh_from_db()
    assert stale.description == "手動編輯過的舊敘述"
