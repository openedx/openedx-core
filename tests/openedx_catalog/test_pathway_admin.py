"""
Tests of the Django admin for the catalog half of Pathways.
"""

import pytest
from django.contrib import admin

from openedx_catalog.admin import PathwayCategoryAdmin
from openedx_catalog.models import PathwayCategory

pytestmark = pytest.mark.django_db


def test_category_list_shows_the_plural_learners_see() -> None:
    """The derived plural when none is set, so operators can spot titles that need an explicit one."""
    category = PathwayCategory.objects.get(category_code="pathway")
    model_admin = PathwayCategoryAdmin(PathwayCategory, admin.site)
    assert model_admin.title_plural_display(category) == "Pathways"

    category.title_plural = "Pathways of Study"
    assert model_admin.title_plural_display(category) == "Pathways of Study"
