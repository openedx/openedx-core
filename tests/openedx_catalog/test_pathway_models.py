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


@pytest.fixture(name="category")
def _category() -> PathwayCategory:
    """The category created by the initial migration, which operators may rename like any other"""
    return PathwayCategory.objects.get(category_code="pathway")


@pytest.fixture(name="data_science")
def _data_science(org1, category) -> CatalogPathway:
    """Create a CatalogPathway for use in these tests"""
    return CatalogPathway.objects.create(org_code="Org1", pathway_code="DataScience", category=category)


@pytest.fixture(name="learner")
def _learner():
    """Create a learner for use in these tests"""
    return User.objects.create(username="learner", email="learner@example.com")


# PathwayCategory


def test_initial_category_is_created() -> None:
    """
    The initial migration creates a "Pathway" category, so that a new instance has one to pick. It's a database row
    rather than a fallback in code, so operators can rename it without a code change (ADR 0007, decision 2).
    """
    assert PathwayCategory.objects.get(category_code="pathway").title == "Pathway"


def test_category_is_required(org1) -> None:
    """There is no default category, so a pathway can't be saved without one."""
    with pytest.raises(IntegrityError), transaction.atomic():
        CatalogPathway.objects.create(org_code="Org1", pathway_code="NoCategory")


def test_category_code_unique_ci() -> None:
    """Category codes are case-insensitively unique."""
    PathwayCategory.objects.create(category_code="masters-degree", title="Master's Degree")
    with pytest.raises(IntegrityError), transaction.atomic():
        PathwayCategory.objects.create(category_code="Masters-Degree", title="Duplicate")


def test_category_title_cannot_be_blank() -> None:
    """The learner-facing title is required at the database level."""
    with pytest.raises(IntegrityError), transaction.atomic():
        PathwayCategory.objects.create(category_code="blank-title", title="")


def test_category_in_use_cannot_be_deleted(data_science) -> None:
    """Deleting a category out from under a pathway would leave it without one."""
    with pytest.raises(ProtectedError):
        data_science.category.delete()


def test_category_string_representation(category) -> None:
    """The string representation of a category is its title."""
    assert str(category) == "Pathway"


def test_title_plural_defaults_to_title_with_an_s(category) -> None:
    """Most titles pluralize by appending "s", so title_plural is optional."""
    assert category.title_plural == ""
    assert category.get_title_plural() == "Pathways"


def test_title_plural_when_appending_an_s_is_wrong() -> None:
    category = PathwayCategory.objects.create(
        category_code="class",
        title="Class",
        title_plural="Classes",
    )
    assert category.get_title_plural() == "Classes"


def test_title_plural_follows_a_renamed_title(category) -> None:
    """Without an explicit plural, renaming the title renames the plural too."""
    category.title = "Program"
    category.save()
    assert category.get_title_plural() == "Programs"


# CatalogPathway


def test_invalid_org() -> None:
    """The Organization must exist in the DB before a CatalogPathway can be created"""
    with pytest.raises(Organization.DoesNotExist):
        CatalogPathway.objects.create(org_code="NewOrg", pathway_code="Whatever")


def test_pathway_code_unique_per_org_ci(org1, org2, category) -> None:
    """The pathway_code is case-insensitively unique per org, but not across orgs."""
    CatalogPathway.objects.create(org_code="Org1", pathway_code="DataScience", category=category)
    with pytest.raises(IntegrityError), transaction.atomic():
        CatalogPathway.objects.create(org_code="Org1", pathway_code="datascience", category=category)
    # A different org may use the same code:
    CatalogPathway.objects.create(org_code="Org2", pathway_code="DataScience", category=category)


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


def test_modified_tracks_catalog_edits(org1, category) -> None:
    """
    `modified` moves when the catalog fields change and `created` does not.
    """
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    edited_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    with freeze_time(created_at):
        pathway = CatalogPathway.objects.create(org_code="Org1", pathway_code="Timestamps", category=category)
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


def test_a_pathway_with_enrollments_cannot_be_deleted(data_science, learner) -> None:
    """PROTECT, so that deleting the wrong pathway can't silently wipe out its enrollment history."""
    enrollment = PathwayEnrollment.objects.create(user=learner, catalog_pathway=data_science, is_active=False)
    with pytest.raises(ProtectedError), transaction.atomic():
        data_science.delete()

    enrollment.delete()  # Deliberately removing the history first is fine.
    data_science.delete()
    assert not CatalogPathway.objects.filter(pk=data_science.pk).exists()


def test_enrollment_string_representation(data_science, learner) -> None:
    """Test the string representation of a pathway enrollment."""
    enrollment = PathwayEnrollment.objects.create(user=learner, catalog_pathway=data_science)
    assert str(enrollment) == f"{learner} in {data_science}"
