"""
Tests related to the catalog half of Pathways.
"""
# pylint: disable=unused-argument
# mypy: disable-error-code="misc"
# (Ignore 'Unexpected attribute "org_code" for model "CatalogPathway"' until
#  https://github.com/typeddjango/django-stubs/issues/1034 is fixed.)

from datetime import datetime, timezone

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import ProtectedError
from django.db.utils import IntegrityError
from freezegun import freeze_time
from organizations.api import ensure_organization  # type: ignore[import]
from organizations.models import Organization  # type: ignore[import]

from openedx_catalog.models import CatalogPathway, PathwayCategory, PathwayEnrollment
from openedx_catalog.models.pathway_category import DEFAULT_PATHWAY_CATEGORY_CODE, DEFAULT_PATHWAY_CATEGORY_NAME
from openedx_content import api as content_api
from openedx_content.models_api import PublishableEntity

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(name="org1")
def _org1() -> None:
    """Create an "Org1" organization for use in these tests"""
    ensure_organization("Org1")


@pytest.fixture(name="org2")
def _org2() -> None:
    """Create an "Org2" organization for use in these tests"""
    ensure_organization("Org2")


@pytest.fixture(name="data_science")
def _data_science(org1) -> CatalogPathway:
    """Create a CatalogPathway for use in these tests"""
    return CatalogPathway.objects.create(org_code="Org1", pathway_code="DataScience")


@pytest.fixture(name="learner")
def _learner():
    """Create a learner for use in these tests"""
    return User.objects.create(username="learner", email="learner@example.com")


@pytest.fixture(name="definition")
def _definition() -> PublishableEntity:
    """
    Create a bare PublishableEntity to stand in for a Pathway definition.

    The catalog can't tell a Pathway entity from any other, so a bare entity exercises the same constraints.
    """
    package = content_api.create_learning_package(package_ref="pathway-tests", title="Pathway tests")
    return content_api.create_publishable_entity(
        package.id, "pathway:DataScience", datetime(2026, 1, 1, tzinfo=timezone.utc), None
    )


# PathwayCategory


def test_default_category_is_shipped() -> None:
    """
    The default category is a database row, not a fallback in code, so that operators can rename it without a code
    change (ADR 0007, decision 2).
    """
    category = PathwayCategory.objects.get(category_code=DEFAULT_PATHWAY_CATEGORY_CODE)
    assert category.name == DEFAULT_PATHWAY_CATEGORY_NAME


def test_category_is_always_provided(org1) -> None:
    """A CatalogPathway created without a category gets the default one."""
    pathway = CatalogPathway.objects.create(org_code="Org1", pathway_code="NoCategory")
    assert pathway.category.category_code == DEFAULT_PATHWAY_CATEGORY_CODE


def test_default_category_can_be_renamed(org1) -> None:
    """
    Renaming the default changes what learners see, and nothing else. The code stays put, so existing pathways keep
    pointing at the same row.
    """
    category = PathwayCategory.objects.get(category_code=DEFAULT_PATHWAY_CATEGORY_CODE)
    category.name = "Program"
    category.save()

    pathway = CatalogPathway.objects.create(org_code="Org1", pathway_code="Renamed")
    assert pathway.category.name == "Program"
    assert pathway.category.category_code == DEFAULT_PATHWAY_CATEGORY_CODE


def test_category_code_unique_ci() -> None:
    """Category codes are case-insensitively unique."""
    PathwayCategory.objects.create(category_code="masters-degree", name="Master's Degree")
    with pytest.raises(IntegrityError), transaction.atomic():
        PathwayCategory.objects.create(category_code="Masters-Degree", name="Duplicate")


def test_category_name_cannot_be_blank() -> None:
    """The learner-facing name is required at the database level."""
    with pytest.raises(IntegrityError), transaction.atomic():
        PathwayCategory.objects.create(category_code="blank-name", name="")


def test_category_in_use_cannot_be_deleted(data_science) -> None:
    """Deleting a category out from under a pathway would leave it without one."""
    with pytest.raises(ProtectedError):
        data_science.category.delete()


def test_category_string_representation() -> None:
    """The string representation of a category is its name."""
    category = PathwayCategory.objects.get(category_code=DEFAULT_PATHWAY_CATEGORY_CODE)
    assert str(category) == DEFAULT_PATHWAY_CATEGORY_NAME

# CatalogPathway


def test_invalid_org() -> None:
    """The Organization must exist in the DB before a CatalogPathway can be created"""
    with pytest.raises(Organization.DoesNotExist):
        CatalogPathway.objects.create(org_code="NewOrg", pathway_code="Whatever")


def test_pathway_code_unique_per_org_ci(org1, org2) -> None:
    """The pathway_code is case-insensitively unique per org, but not across orgs."""
    CatalogPathway.objects.create(org_code="Org1", pathway_code="DataScience")
    with pytest.raises(IntegrityError), transaction.atomic():
        CatalogPathway.objects.create(org_code="Org1", pathway_code="datascience")
    # A different org may use the same code:
    CatalogPathway.objects.create(org_code="Org2", pathway_code="DataScience")


def test_title_defaults_to_pathway_code(data_science) -> None:
    """A blank title falls back to the code, rather than failing the not-blank constraint."""
    assert data_science.title == "DataScience"


def test_key_str(data_science) -> None:
    """The key is derived from the org and pathway codes."""
    assert data_science.key_str == "catalog-pathway:Org1:DataScience"


def test_catalog_edits_are_free(data_science) -> None:
    """
    Catalog copy is not versioned. Editing it is an ordinary save, with no version to create and no trace left behind.
    """
    data_science.title = "Data Science Professional Program"
    data_science.description = "Learn data science."
    data_science.save()

    reloaded = CatalogPathway.objects.get(pk=data_science.pk)
    assert reloaded.title == "Data Science Professional Program"
    assert reloaded.description == "Learn data science."


def test_modified_tracks_catalog_edits(org1) -> None:
    """
    `modified` moves when the catalog fields change and `created` does not.
    """
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    edited_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    with freeze_time(created_at):
        pathway = CatalogPathway.objects.create(org_code="Org1", pathway_code="Timestamps")
    assert pathway.created == created_at
    assert pathway.modified == created_at

    with freeze_time(edited_at):
        pathway.title = "Renamed"
        pathway.save()

    reloaded = CatalogPathway.objects.get(pk=pathway.pk)
    assert reloaded.created == created_at
    assert reloaded.modified == edited_at


def test_pathway_string_representation(data_science) -> None:
    """Test the string representation of a pathway."""
    data_science.title = "Data Science Professional Program"
    data_science.save()
    data_science.refresh_from_db()
    assert str(data_science) == "Data Science Professional Program (Org1 DataScience)"


def test_content_entity_is_optional(data_science) -> None:
    """A catalog pathway may exist as a placeholder before its definition does."""
    assert data_science.content_entity is None


def test_one_catalog_pathway_per_definition(org1, definition) -> None:
    """A definition serves exactly one catalog entry."""
    CatalogPathway.objects.create(org_code="Org1", pathway_code="First", content_entity=definition)
    with pytest.raises(IntegrityError), transaction.atomic():
        CatalogPathway.objects.create(org_code="Org1", pathway_code="Second", content_entity=definition)


def test_linked_definition_cannot_be_deleted(data_science, definition) -> None:
    """PROTECT: deleting a definition out from under a catalog entry would leave its learners with no requirements."""
    data_science.content_entity = definition
    data_science.save()
    with pytest.raises(ProtectedError), transaction.atomic():
        definition.delete()

    data_science.content_entity = None
    data_science.save()
    definition.delete()  # Unlinked, so this is fine.


# PathwayEnrollment


def test_enrollment_is_unique_per_learner(data_science, learner) -> None:
    """A learner is either enrolled in a pathway or not; there is never a second row."""
    PathwayEnrollment.objects.create(user=learner, catalog_pathway=data_science)
    with pytest.raises(IntegrityError), transaction.atomic():
        PathwayEnrollment.objects.create(user=learner, catalog_pathway=data_science)


def test_enrollment_pins_no_version(data_science, learner) -> None:
    """
    Enrollment ties a learner to the catalog half only. There is deliberately no field pinning a content version,
    because progress is evaluated against whatever is published at the time (ADR 0007, decision 5).
    """
    enrollment = PathwayEnrollment.objects.create(user=learner, catalog_pathway=data_science)
    field_names = {field.name for field in enrollment._meta.get_fields()}
    assert not any("version" in name for name in field_names)


def test_enrollment_is_active_by_default(data_science, learner) -> None:
    """A fresh enrollment is active; deactivating it is how unenrolling is recorded."""
    enrollment = PathwayEnrollment.objects.create(user=learner, catalog_pathway=data_science)
    assert enrollment.is_active


def test_enrollment_string_representation(data_science, learner) -> None:
    """Test the string representation of a pathway enrollment."""
    enrollment = PathwayEnrollment.objects.create(user=learner, catalog_pathway=data_science)
    assert str(enrollment) == f"{learner} in {data_science}"
