"""
Tests related to the openedx_catalog python API
"""

import logging
from datetime import datetime, timezone

import pytest
from django.db import connection
from django.db.models import ProtectedError
from django.test import override_settings
from freezegun import freeze_time
from opaque_keys.edx.keys import CourseKey
from organizations.api import ensure_organization  # type: ignore[import]

from openedx_catalog import api
from openedx_catalog.models_api import CatalogCourse, CourseRun

pytestmark = pytest.mark.django_db

# Fixtures used by these tests:


@pytest.fixture(name="python100")
def _python100():
    """Create a "Python100" catalog course for use in these tests"""
    ensure_organization("Org1")
    # Note: in the future, this could use an API to create the CatalogCourse,
    # but we haven't created the full CRUD API yet.
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100", display_name="Python 100")
    assert cc.url_slug == "Org1:Python100"
    return cc


@pytest.fixture(name="csharp200")
def _csharp200():
    """Create a "CSharp200" catalog course for use in these tests"""
    ensure_organization("Org1")
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="CSharp200", display_name="C# 200")
    assert cc.url_slug == "Org1:CSharp200"
    return cc


@pytest.fixture(name="python100_summer26")
def _python100_summer26(python100: CatalogCourse):
    """Create a "Python100" "Summer 2026" course run for use in these tests"""
    # Note: in the future, this could use an API to create the CourseRun,
    # but we haven't created the full CRUD API yet.
    return CourseRun.objects.create(
        catalog_course=python100,
        run_code="2026summer",
        display_name="Python 100 (Summer ☀️ 2026)",  # A random emoji just to test Unicode support in display_name
    )


@pytest.fixture(name="python100_winter26")
def _python100_winter26(python100: CatalogCourse):
    """Create a "Python100" "Winter 2026" course run for use in these tests"""
    return CourseRun.objects.create(
        catalog_course=python100, run_code="2026winter", display_name="Python 100 (Winter '26)"
    )


# get_catalog_course


def test_get_catalog_course(python100: CatalogCourse, csharp200: CatalogCourse) -> None:
    """
    Test using get_catalog_course to get a course in various ways:
    """
    # Retrieve by ID:
    assert api.get_catalog_course(pk=python100.pk) == python100
    assert api.get_catalog_course(pk=csharp200.pk) == csharp200
    with pytest.raises(CatalogCourse.DoesNotExist):
        api.get_catalog_course(pk=8234758243)

    # Retrieve by URL slug:
    assert api.get_catalog_course(url_slug="Org1:Python100") == python100
    assert api.get_catalog_course(url_slug="Org1:CSharp200") == csharp200
    with pytest.raises(CatalogCourse.DoesNotExist):
        api.get_catalog_course(url_slug="foo:bar")

    # Retrieve by (org_code, course_code)
    assert api.get_catalog_course(org_code="Org1", course_code="Python100") == python100
    assert api.get_catalog_course(org_code="Org1", course_code="CSharp200") == csharp200
    with pytest.raises(CatalogCourse.DoesNotExist):
        api.get_catalog_course(org_code="Org2", course_code="CSharp200")


def test_get_catalog_course_url_slug_case(python100: CatalogCourse) -> None:
    """
    Test that get_catalog_course(url_slug=...) is case-insensitive
    """
    # FIXME: The Organization model's short_code is case sensitive on SQLite but case insensitive on MySQL :/
    # So for now, we only make assertions about the 'course_code' field case, which we can control.
    assert api.get_catalog_course(url_slug="Org1:Python100") == python100  # Correct case
    with pytest.raises(CatalogCourse.DoesNotExist):
        api.get_catalog_course(url_slug="Org1:python100")  # Wrong course code case


def get_get_all_runs(python100_summer26: CourseRun, python100_winter26: CourseRun) -> None:
    """
    Test getting all runs of a course using `get_catalog_course(...).runs` as
    recommended by the `get_course_run()` docstring.
    """
    cc = api.get_catalog_course(org_code="Org1", course_code="Python100")
    assert cc.runs.order_by("-run") == [
        python100_summer26,
        python100_winter26,
    ]


# update_catalog_course


def test_update_display_name(python100: CatalogCourse, csharp200: CatalogCourse) -> None:
    """Test that we can use the API to update the name of a catalog course"""
    csharp200_old_name = csharp200.display_name
    # Update display_name using a CatalogCourse object:
    api.update_catalog_course(python100, display_name="New name for Python 100")
    assert api.get_catalog_course(pk=python100.pk).display_name == "New name for Python 100"
    assert api.get_catalog_course(pk=csharp200.pk).display_name == csharp200_old_name  # Unchanged
    # Update display name using PK only:
    api.update_catalog_course(csharp200.pk, display_name="New name for C# 200")
    assert api.get_catalog_course(pk=python100.pk).display_name == "New name for Python 100"  # Unchanged
    assert api.get_catalog_course(pk=csharp200.pk).display_name == "New name for C# 200"


def test_update_language(python100: CatalogCourse) -> None:
    """Test that we can use the API to update the language of a catalog course"""
    assert python100.language_short == "en"
    api.update_catalog_course(python100, language_short="fr")
    python100.refresh_from_db()
    assert python100.language_short == "fr"


def test_update_ignore_other_fields(python100: CatalogCourse) -> None:
    """Test that when we pass on object to update_catalog_course, only expected fields are updated"""
    python100.course_code = "CHANGED"
    api.update_catalog_course(python100, language_short="fr", display_name="New Name")
    python100.refresh_from_db()
    assert python100.course_code == "Python100"  # course_code field was not modified by update_catalog_course()
    assert python100.language_short == "fr"
    assert python100.display_name == "New Name"


# delete_catalog_course


def test_delete_catalog_course(python100: CatalogCourse, python100_summer26: CourseRun) -> None:
    """Test that we can delete a CatalogCourse but only if no runs exist"""
    # At first, deletion will fail because of the Summer2026 run:
    with pytest.raises(ProtectedError):
        api.delete_catalog_course(python100)
    python100.refresh_from_db()  # Make sure it's not deleted.
    # Now delete the run, unblocking deletion of the catalog course:
    python100_summer26.delete()  # FIXME: use an API method for this.
    api.delete_catalog_course(python100)
    with pytest.raises(CatalogCourse.DoesNotExist):
        python100.refresh_from_db()  # Make sure it's gone


# get_course_run


def test_get_course_run_nonexistent() -> None:
    """
    Getting a non-existent course run raises CourseRun.DoesNotExist
    """
    with pytest.raises(CourseRun.DoesNotExist):
        api.get_course_run(CourseKey.from_string("course-v1:org_code+course_run+run"))


def test_get_course_run(python100_summer26: CourseRun) -> None:
    """Basic retrieval of a CourseRun using the API"""
    run = api.get_course_run(python100_summer26.course_key)
    assert run == python100_summer26


# sync_course_run_details


def test_sync_course_run_details(python100_summer26) -> None:
    """Test `sync_course_run_details()`"""
    course_key = python100_summer26.course_key
    assert python100_summer26.display_name == "Python 100 (Summer ☀️ 2026)"
    api.sync_course_run_details(course_key, display_name="✅ New name")
    python100_summer26.refresh_from_db()
    assert python100_summer26.display_name == "✅ New name"


# create_course_run_for_modulestore_course_with


@override_settings(LANGUAGE_CODE="fr-ca")
def test_create_course_run_for_modulestore_course_with():
    """
    Test that create_course_run_for_modulestore_course_with() can be used to
    keep CourseRun+CatalogCourse in sync with modulestore when a brand new
    course is created.

    This test: neither org nor catalog course nor run previously exist.
    """
    org_code, course_code, run_code = "NewOrg", "Test", "2026"
    course_key = CourseKey.from_string(f"course-v1:{org_code}+{course_code}+{run_code}")

    created_time = datetime(2026, 6, 8, tzinfo=timezone.utc)
    with freeze_time(created_time):
        run = api.create_course_run_for_modulestore_course_with(
            course_key,
            display_name="Introduction aux tests",
            # language_short is not specified - should use the default language (French)
        )
    assert run.catalog_course.org_code == org_code
    assert run.catalog_course.course_code == "Test"
    assert run.catalog_course.language == "fr-ca"
    assert run.catalog_course.language_short == "fr"
    assert run.catalog_course.display_name == "Introduction aux tests"
    assert run.catalog_course.created == created_time
    assert run.display_name == "Introduction aux tests"
    assert run.run_code == run_code
    assert run.created == created_time
    assert run.course_key == course_key


def test_create_course_run_for_modulestore_course_with_existing_org():
    """
    Test create_course_run_for_modulestore_course_with() when org already exists
    but catalog course does not.
    """
    org_code, course_code, run_code = "NewOrg", "Test", "2026"
    course_key = CourseKey.from_string(f"course-v1:{org_code}+{course_code}+{run_code}")

    ensure_organization(org_code)
    run = api.create_course_run_for_modulestore_course_with(
        course_key, display_name="Introducción a las pruebas", language_short="es"
    )
    assert run.catalog_course.org_code == org_code
    assert run.catalog_course.course_code == "Test"
    assert run.catalog_course.language == "es"
    assert run.catalog_course.language_short == "es"
    assert run.catalog_course.display_name == "Introducción a las pruebas"
    assert run.run_code == run_code
    assert run.display_name == "Introducción a las pruebas"
    assert run.course_key == course_key


# FIXME: this test passes on MySQL but not SQLite. We need to update the Organizations code to behave consistently.
@pytest.mark.skipif(connection.vendor == "sqlite", reason="Only passes on MySQL")
def test_create_course_run_for_modulestore_course_with_existing_org_different_capitalization(
    caplog: pytest.LogCaptureFixture,
):
    """
    Test create_course_run_for_modulestore_course_with() when the org already
    exists but with different capitalization
    """
    org_code, course_code, run_code = "NewOrg", "Test", "2026"
    course_key = CourseKey.from_string(f"course-v1:{org_code}+{course_code}+{run_code}")

    existing_org_id = ensure_organization("nEWoRG")["id"]
    run = api.create_course_run_for_modulestore_course_with(
        course_key, display_name="Introducción a las pruebas", language_short="es"
    )

    # Verify that a warning was logged. We actually get two warnings - one from the API and one from model.clean():
    assert caplog.record_tuples == [
        (
            "openedx_catalog.api_impl",
            logging.WARN,
            'The course with ID "course-v1:NewOrg+Test+2026" does not match its Organization.short_name "nEWoRG"',
        ),
        (
            "openedx_catalog.models.course_run",
            logging.WARN,
            'Course run "course-v1:NewOrg+Test+2026" does not match case of its org short_name "nEWoRG"',
        ),
    ]

    assert run.catalog_course.org_id == existing_org_id
    assert run.catalog_course.org_code == "nEWoRG"  # Uses canonical capitalization
    assert run.catalog_course.course_code == "Test"
    assert run.catalog_course.language == "es"
    assert run.catalog_course.language_short == "es"
    assert run.catalog_course.display_name == "Introducción a las pruebas"
    assert run.run_code == run_code
    assert run.display_name == "Introducción a las pruebas"
    assert run.course_key == course_key  # But course ID uses original capitalization


def test_create_course_run_for_modulestore_course_with_existing_cc():
    """
    Test create_course_run_for_modulestore_course_with() when the org and
    catalog catalog course already exist, but no other runs do
    """
    org_code, course_code, run_code = "NewOrg", "Test", "2026"
    course_key = CourseKey.from_string(f"course-v1:{org_code}+{course_code}+{run_code}")

    ensure_organization(org_code)
    # Note we don't have an API for creating catalog courses yet, other than this
    # `create_course_run_for_modulestore_course_with` method auto-creating them, so just use the model:
    CatalogCourse.objects.create(org_code=org_code, course_code=course_code, display_name="Catalog Display Name")
    run = api.create_course_run_for_modulestore_course_with(course_key, display_name="Run Display Name")
    assert run.catalog_course.org_code == org_code
    assert run.catalog_course.course_code == "Test"
    assert run.catalog_course.language_short == "en"  # Default language
    assert run.catalog_course.display_name == "Catalog Display Name"  # Should not have changed just by creating a run
    assert run.run_code == run_code
    assert run.display_name == "Run Display Name"
    assert run.course_key == course_key


def test_create_course_run_for_modulestore_course_with_existing_run():
    """
    Test create_course_run_for_modulestore_course_with() when another run of the
    same catalog course already exists.
    """
    org_code, course_code, run_code = "NewOrg", "Test", "2026"
    course_key = CourseKey.from_string(f"course-v1:{org_code}+{course_code}+{run_code}")
    old_run_code = "2025"
    old_course_key = CourseKey.from_string(f"course-v1:{org_code}+{course_code}+{old_run_code}")

    old_run = api.create_course_run_for_modulestore_course_with(old_course_key, display_name="Previous Run (2025)")
    new_run = api.create_course_run_for_modulestore_course_with(course_key, display_name="New Run (2026)")
    old_run.refresh_from_db()  # Let's make sure it hasn't changed
    assert old_run.display_name == "Previous Run (2025)"
    assert old_run.catalog_course == new_run.catalog_course
    assert old_run.run_code == "2025"
    # When there was only one run, the catalog course would be given the name of that run:
    assert new_run.catalog_course.display_name == "Previous Run (2025)"
    assert new_run.run_code == "2026"
    assert new_run.display_name == "New Run (2026)"
    assert new_run.course_key == course_key


def test_create_course_run_for_modulestore_course_run_that_exists(caplog: pytest.LogCaptureFixture) -> None:
    """
    Test create_course_run_for_modulestore_course_with() when that exact
    CourseRun already exists, e.g. due to a race condition.
    """
    org_code, course_code, run_code = "NewOrg", "Test", "2026"
    course_key = CourseKey.from_string(f"course-v1:{org_code}+{course_code}+{run_code}")

    existing_run = api.create_course_run_for_modulestore_course_with(course_key, display_name="Original Name")
    # Call the API again to create the exact same run that we just created:
    new_run = api.create_course_run_for_modulestore_course_with(course_key, display_name="New Name (ignore)")

    # Verify that a warning was logged:
    assert caplog.record_tuples == [
        (
            "openedx_catalog.api_impl",
            logging.WARN,
            'Expected to create CourseRun for "course-v1:NewOrg+Test+2026" but it already existed.',
        ),
    ]

    existing_run.refresh_from_db()  # Let's make sure it hasn't changed
    assert existing_run == new_run
    assert existing_run.display_name == "Original Name"
    assert new_run.display_name == "Original Name"
    assert new_run.catalog_course.display_name == "Original Name"
    assert new_run.run_code == run_code


# delete_course_run


def test_delete_course_run(
    python100: CatalogCourse,
    python100_summer26: CourseRun,
    python100_winter26: CourseRun,
) -> None:
    """Test that we can delete a CourseRun, passing in the object"""
    api.delete_course_run(python100_summer26.course_key)
    with pytest.raises(CourseRun.DoesNotExist):
        python100_summer26.refresh_from_db()  # Make sure it's gone
    # The catalog course and other run is unaffected:
    python100.refresh_from_db()
    python100_winter26.refresh_from_db()
