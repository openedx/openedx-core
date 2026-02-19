"""
Tests related to the Catalog signal handlers
"""

import re

import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase
from organizations.api import ensure_organization  # type: ignore[import]
from organizations.models import Organization

from openedx_catalog.models import CatalogCourse, CourseRun


class TestCatalogSignals(TestCase):
    """
    Test openedx_catalog signal handlers
    """

    def test_org_integrity(self) -> None:
        """
        Test that Organization.short_name cannot be changed if it would result in invalid CourseRun relationships.

        Note: this is just Django validation; running an `update()` or raw SQL will easily bypass this check.
        """
        org = Organization.objects.get(pk=ensure_organization("Org1")["id"])

        catalog_course = CatalogCourse.objects.create(org=org, course_code="Math100")
        course_run = CourseRun.objects.create(catalog_course=catalog_course, run="A")
        assert str(course_run.course_id) == "course-v1:Org1+Math100+A"

        org.short_name = "foo"
        with pytest.raises(
            ValidationError,
            match=re.escape(
                'Changing the org short_name to "foo" will result in CourseRun "course-v1:Org1+Math100+A" having '
                "the incorrect organization code."
            ),
        ):
            org.save()

        # BUT, changing just the capitalization is allowed:
        org.short_name = "orG1"
        org.save()  # No ValidationError

    def test_org_short_name_change_no_runs(self) -> None:
        """
        Test that Organization.short_name CAN be changed if it won't affect any course runs.
        """
        org = Organization.objects.get(pk=ensure_organization("Org1")["id"])
        CatalogCourse.objects.create(org=org, course_code="Math100")

        org.short_name = "foo"
        org.save()
