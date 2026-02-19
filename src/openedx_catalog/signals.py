"""
Signal receivers for openedx_catalog
"""
# pylint: disable=unused-argument

import logging

from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save
from django.dispatch import receiver
from organizations.models import Organization  # type: ignore[import]

from .models import CourseRun

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Organization)
def verify_organization_change(sender, instance, **kwargs):
    """
    Check that changes to Organization objects won't create invalid relationships.

    Nothing stops users from changing Organization.short_name entries in the Django admin, but any changes other than
    capitalization fixes will result in totally invalid data, as CatalogCourse will be related to an Organization that
    no longer matches the "org" part of the related CourseRun's course IDs.
    """
    if not instance.pk:
        return  # It's a brand new Organization; we don't care

    prev_org_code = Organization.objects.get(pk=instance.pk).short_name
    new_org_code = instance.short_name

    if new_org_code != prev_org_code:
        # Check if this is going to violate any relationship expectations.
        # Note: we could make the database enforce this by making the CatalogCourse.org relationship use "short_name" as
        # its foreign key ID, but that would also make it extremely difficult to ever "fix" an incorrect Organization's
        # short_name (e.g. change capitalization), because doing so would fail with a foreign key constraint error.

        run_course_ids = CourseRun.objects.filter(catalog_course__org=instance).values_list("course_id", flat=True)
        for course_id in run_course_ids:
            if course_id.org.lower() != new_org_code.lower():
                raise ValidationError(
                    f'Changing the org short_name to "{new_org_code}" will result in CourseRun "{course_id}" having '
                    "the incorrect organization code. "
                )
