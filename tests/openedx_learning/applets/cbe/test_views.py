"""
Tests for CompetencyTaxonomyView's taxonomy_type dispatch, plus a TaxonomyView
assertion tests/openedx_tagging can't make for itself.

Neither view is registered on a URL in openedx-core yet, so these tests call
as_view() directly via APIRequestFactory instead of self.client against a URL.
"""
from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from openedx_learning.applets.cbe.views import CompetencyTaxonomyView
from openedx_learning.models import CompetencyTaxonomy
from openedx_tagging.api import create_taxonomy
from openedx_tagging.models import Taxonomy
from openedx_tagging.rest_api.v1.views import TaxonomyView

pytestmark = pytest.mark.django_db

User = get_user_model()

create_view = CompetencyTaxonomyView.as_view({"post": "create"})
create_import_view = CompetencyTaxonomyView.as_view({"post": "create_import"})
base_taxonomy_create_view = TaxonomyView.as_view({"post": "create"})


def _staff_user():
    return User.objects.create(username="staff", email="staff@example.com", is_staff=True)


def test_perform_create_competency_creates_competency_taxonomy() -> None:
    """
    Posting taxonomy_type="competency" through CompetencyTaxonomyView creates a
    CompetencyTaxonomy row, not just a plain Taxonomy.
    """
    request = APIRequestFactory().post(
        "/fake-competency-taxonomies/",
        {"name": "Nursing Competencies", "export_id": "nursing-competencies", "taxonomy_type": "competency"},
        format="json",
    )
    force_authenticate(request, user=_staff_user())

    response = create_view(request)

    assert response.status_code == status.HTTP_201_CREATED
    assert CompetencyTaxonomy.objects.filter(pk=response.data["id"]).exists()


def test_perform_create_tags_creates_no_competency_row() -> None:
    """
    Posting taxonomy_type="tags" through CompetencyTaxonomyView creates a plain
    Taxonomy, not a CompetencyTaxonomy row.
    """
    request = APIRequestFactory().post(
        "/fake-competency-taxonomies/",
        {"name": "Tags Type Test", "export_id": "tags-type-test", "taxonomy_type": "tags"},
        format="json",
    )
    force_authenticate(request, user=_staff_user())

    response = create_view(request)

    assert response.status_code == status.HTTP_201_CREATED
    assert Taxonomy.objects.filter(name="Tags Type Test").exists()
    assert not CompetencyTaxonomy.objects.filter(name="Tags Type Test").exists()


def test_perform_create_competency_duplicate_export_id_returns_400() -> None:
    """
    A validation failure in the competency branch (duplicate export_id, via
    full_clean()) returns a 400, like the "tags" branch, not an unhandled 500.
    """
    create_taxonomy(name="Existing Taxonomy", export_id="duplicate-export-id")
    request = APIRequestFactory().post(
        "/fake-competency-taxonomies/",
        {"name": "Nursing Competencies", "export_id": "duplicate-export-id", "taxonomy_type": "competency"},
        format="json",
    )
    force_authenticate(request, user=_staff_user())

    response = create_view(request)

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_create_import_tags_creates_no_competency_row() -> None:
    """
    Importing with taxonomy_type="tags" through CompetencyTaxonomyView creates a
    plain Taxonomy, not a CompetencyTaxonomy row.
    """
    file = SimpleUploadedFile(
        "taxonomy.json",
        json.dumps({"tags": [{"id": "tag_1", "value": "Tag 1"}]}).encode(),
        content_type="application/json",
    )
    request = APIRequestFactory().post(
        "/fake-competency-taxonomies/import/",
        {
            "taxonomy_name": "Imported Tags Type Test",
            "taxonomy_export_id": "imported-tags-type-test",
            "taxonomy_type": "tags",
            "file": file,
        },
        format="multipart",
    )
    force_authenticate(request, user=_staff_user())

    response = create_import_view(request)

    assert response.status_code == status.HTTP_201_CREATED
    assert Taxonomy.objects.filter(name="Imported Tags Type Test").exists()
    assert not CompetencyTaxonomy.objects.filter(name="Imported Tags Type Test").exists()


def test_base_taxonomy_view_competency_type_creates_no_competency_row() -> None:
    """
    Posting taxonomy_type="competency" through the base TaxonomyView creates a
    plain Taxonomy and no CompetencyTaxonomy row.
    """
    request = APIRequestFactory().post(
        "/fake-taxonomies/",
        {
            "name": "Base View Competency Type",
            "export_id": "base-view-competency-type",
            "taxonomy_type": "competency",
        },
        format="json",
    )
    force_authenticate(request, user=_staff_user())

    response = base_taxonomy_create_view(request)

    assert response.status_code == status.HTTP_201_CREATED
    assert Taxonomy.objects.filter(name="Base View Competency Type").exists()
    assert not CompetencyTaxonomy.objects.filter(name="Base View Competency Type").exists()
