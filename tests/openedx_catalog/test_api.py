"""
Tests related to the openedx_catalog python API
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.test import TestCase, override_settings
from opaque_keys.edx.locator import CourseLocator
from organizations.api import ensure_organization  # type: ignore[import]
from organizations.models import Organization  # type: ignore[import]

from openedx_catalog import api
from openedx_catalog.models_api import CatalogCourse, CourseRun

pytestmark = pytest.mark.django_db


@pytest.fixture(name="python100")
def _python100():
    """Create a "Python100" course for use in these tests"""
    ensure_organization("Org1")
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100")
    assert cc.url_slug == "Org1:Python100"
    return cc


@pytest.fixture(name="csharp200")
def _csharp200():
    """Create a "CSharp200" course for use in these tests"""
    ensure_organization("Org1")
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="CSharp200")
    assert cc.url_slug == "Org1:CSharp200"
    return cc


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
    assert api.get_catalog_course(url_slug="Org1:python100") == python100  # Wrong course code case
    assert api.get_catalog_course(url_slug="Org1:PYTHON100").url_slug == "Org1:Python100"  # Gets normalized


# get_course_run
# sync_course_run_details
# create_course_run_for_modulestore_course_with
# update_catalog_course
