"""
Tests of the catalog-side Pathway API.
"""
# pylint: disable=unused-argument

from datetime import datetime, timezone

import pytest
from django.contrib.auth import get_user_model
from freezegun import freeze_time
from organizations.api import ensure_organization  # type: ignore[import]

from openedx_catalog import api as catalog_api
from openedx_catalog.models import CatalogPathway, PathwayCategory
from openedx_catalog.models.pathway_category import DEFAULT_PATHWAY_CATEGORY_CODE
from openedx_content import api as content_api
from openedx_content.models_api import PublishableEntity

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(name="org1")
def _org1() -> None:
    """Create an "Org1" organization for use in these tests"""
    ensure_organization("Org1")


@pytest.fixture(name="data_science")
def _data_science(org1) -> CatalogPathway:
    """Create a CatalogPathway for use in these tests"""
    return catalog_api.create_catalog_pathway(
        org_code="Org1",
        pathway_code="DataScience",
        title="Data Science Professional Certificate",
        description="Learn data science.",
    )


@pytest.fixture(name="learner")
def _learner():
    """Create a learner for use in these tests"""
    return User.objects.create(username="learner", email="learner@example.com")


@pytest.fixture(name="definition")
def _definition() -> PublishableEntity:
    """Create a bare PublishableEntity to stand in for a Pathway definition."""
    package = content_api.create_learning_package(package_ref="pathway-tests", title="Pathway tests")
    return content_api.create_publishable_entity(
        package.id, "pathway:DataScience", datetime(2026, 1, 1, tzinfo=timezone.utc), None
    )


def test_get_default_pathway_category() -> None:
    assert catalog_api.get_default_pathway_category().category_code == DEFAULT_PATHWAY_CATEGORY_CODE


def test_get_pathway_category() -> None:
    """Categories are looked up by their stable code, not by the learner-facing name."""
    masters = PathwayCategory.objects.create(category_code="masters-degree", name="Master's Degree")
    assert catalog_api.get_pathway_category("masters-degree") == masters
    with pytest.raises(PathwayCategory.DoesNotExist):
        catalog_api.get_pathway_category("Master's Degree")


def test_create_with_default_category(org1) -> None:
    """Omitting the category picks the shipped default, and a blank title falls back to the code."""
    pathway = catalog_api.create_catalog_pathway(org_code="Org1", pathway_code="CompSci")
    assert pathway.category.category_code == DEFAULT_PATHWAY_CATEGORY_CODE
    assert pathway.title == "CompSci"


def test_create_with_explicit_category(org1) -> None:
    """Operators can add categories of their own; the default is only a default."""
    masters = PathwayCategory.objects.create(category_code="masters-degree", name="Master's Degree")
    pathway = catalog_api.create_catalog_pathway(
        org_code="Org1",
        pathway_code="CompSci",
        title="Computer Science",
        category=masters,
    )
    assert pathway.category == masters


def test_get_catalog_pathway(data_science) -> None:
    """A catalog pathway can be looked up by pk, key string, or org + code."""
    assert catalog_api.get_catalog_pathway(pk=data_science.id) == data_science
    assert catalog_api.get_catalog_pathway(key_str=data_science.key_str) == data_science
    assert catalog_api.get_catalog_pathway(org_code="Org1", pathway_code="DataScience") == data_science
    with pytest.raises(CatalogPathway.DoesNotExist):
        catalog_api.get_catalog_pathway(org_code="Org1", pathway_code="Nope")


def test_update_catalog_pathway_by_id_and_category(data_science) -> None:
    """The pathway may be given by ID, and the category can be changed like any other catalog field."""
    masters = PathwayCategory.objects.create(category_code="masters-degree", name="Master's Degree")
    catalog_api.update_catalog_pathway(data_science.id, category=masters)
    assert catalog_api.get_catalog_pathway(pk=data_science.id).category == masters


def test_update_catalog_pathway(data_science) -> None:
    """Only the fields passed are changed; the rest are left alone. `modified` records the edit."""
    edited_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    with freeze_time(edited_at):
        catalog_api.update_catalog_pathway(data_science, description="Learn even more data science.")

    reloaded = catalog_api.get_catalog_pathway(pk=data_science.id)
    assert reloaded.description == "Learn even more data science."
    assert reloaded.title == "Data Science Professional Certificate"
    assert reloaded.modified == edited_at


def test_update_catalog_pathway_with_nothing_to_change(data_science) -> None:
    """Passing no fields is a no-op, so `modified` is not bumped."""
    before = catalog_api.get_catalog_pathway(pk=data_science.id).modified
    catalog_api.update_catalog_pathway(data_science)
    assert catalog_api.get_catalog_pathway(pk=data_science.id).modified == before


def test_delete_catalog_pathway(data_science) -> None:
    catalog_api.delete_catalog_pathway(data_science.id)
    with pytest.raises(CatalogPathway.DoesNotExist):
        catalog_api.get_catalog_pathway(pk=data_science.id)


def test_set_catalog_pathway_content(data_science, definition) -> None:
    """The catalog entry points at its definition; the link can be set by instance or ID, and cleared with None."""
    catalog_api.set_catalog_pathway_content(data_science, definition)
    assert catalog_api.get_catalog_pathway(pk=data_science.id).content_entity == definition

    catalog_api.set_catalog_pathway_content(data_science.id, None)
    assert catalog_api.get_catalog_pathway(pk=data_science.id).content_entity is None

    catalog_api.set_catalog_pathway_content(data_science.id, definition.id)
    assert catalog_api.get_catalog_pathway(pk=data_science.id).content_entity == definition


def test_deleting_catalog_pathway_leaves_definition_in_place(data_science, definition) -> None:
    """Deleting the catalog half unlinks the definition; it does not delete it."""
    catalog_api.set_catalog_pathway_content(data_science, definition)
    catalog_api.delete_catalog_pathway(data_science)
    assert PublishableEntity.objects.filter(id=definition.id).exists()


def test_enrollment_round_trip(data_science, learner) -> None:
    assert not catalog_api.is_enrolled_in_pathway(learner.id, data_science)

    enrollment = catalog_api.enroll_in_pathway(learner.id, data_science)
    assert catalog_api.is_enrolled_in_pathway(learner.id, data_science)
    assert list(catalog_api.get_pathway_enrollments(learner.id)) == [enrollment]

    catalog_api.unenroll_from_pathway(learner.id, data_science)
    assert not catalog_api.is_enrolled_in_pathway(learner.id, data_science)


def test_enrolling_twice_is_idempotent(data_science, learner) -> None:
    """Enrolling again returns the existing enrollment rather than failing, and doesn't touch `modified`."""
    with freeze_time(datetime(2026, 1, 1, tzinfo=timezone.utc)):
        first = catalog_api.enroll_in_pathway(learner.id, data_science)
    with freeze_time(datetime(2026, 2, 1, tzinfo=timezone.utc)):
        second = catalog_api.enroll_in_pathway(learner.id, data_science.id)
    assert first == second
    assert second.modified == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert catalog_api.get_pathway_enrollments(learner.id).count() == 1


def test_unenrolling_keeps_the_row(data_science, learner) -> None:
    """Unenrolling deactivates rather than deletes, so history survives."""
    enrollment = catalog_api.enroll_in_pathway(learner.id, data_science)
    catalog_api.unenroll_from_pathway(learner.id, data_science)

    assert not catalog_api.is_enrolled_in_pathway(learner.id, data_science)
    assert catalog_api.get_pathway_enrollments(learner.id).count() == 0
    inactive = catalog_api.get_pathway_enrollments(learner.id, include_inactive=True)
    assert list(inactive) == [enrollment]
    assert not inactive[0].is_active


def test_re_enrolling_reactivates_the_same_row(data_science, learner) -> None:
    """
    Re-enrolling reuses the original row, keeping the original enrollment date. `modified` tracks each flip of
    `is_active`, so it ends up as "when this learner last enrolled or unenrolled".
    """
    enrolled_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    unenrolled_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    re_enrolled_at = datetime(2026, 3, 1, tzinfo=timezone.utc)

    with freeze_time(enrolled_at):
        original = catalog_api.enroll_in_pathway(learner.id, data_science)
    assert original.modified == enrolled_at

    with freeze_time(unenrolled_at):
        catalog_api.unenroll_from_pathway(learner.id, data_science)
    inactive = catalog_api.get_pathway_enrollments(learner.id, include_inactive=True).get()
    assert not inactive.is_active
    assert inactive.modified == unenrolled_at

    with freeze_time(re_enrolled_at):
        reactivated = catalog_api.enroll_in_pathway(learner.id, data_science)
    assert reactivated.id == original.id
    assert reactivated.is_active
    assert reactivated.created == enrolled_at
    assert reactivated.modified == re_enrolled_at
    assert catalog_api.is_enrolled_in_pathway(learner.id, data_science)


def test_unenrolling_when_not_enrolled_is_a_no_op(data_science, learner) -> None:
    catalog_api.unenroll_from_pathway(learner.id, data_science)  # Should not raise.
    assert not catalog_api.is_enrolled_in_pathway(learner.id, data_science)
    assert catalog_api.get_pathway_enrollments(learner.id, include_inactive=True).count() == 0


def test_unenrolling_twice_does_not_bump_modified(data_science, learner) -> None:
    """Unenrolling only touches rows that are actually active, so a repeat call leaves `modified` alone."""
    catalog_api.enroll_in_pathway(learner.id, data_science)
    with freeze_time(datetime(2026, 2, 1, tzinfo=timezone.utc)):
        catalog_api.unenroll_from_pathway(learner.id, data_science)
    with freeze_time(datetime(2026, 3, 1, tzinfo=timezone.utc)):
        catalog_api.unenroll_from_pathway(learner.id, data_science)

    row = catalog_api.get_pathway_enrollments(learner.id, include_inactive=True).get()
    assert row.modified == datetime(2026, 2, 1, tzinfo=timezone.utc)


def test_deleting_pathway_removes_enrollments(data_science, learner) -> None:
    catalog_api.enroll_in_pathway(learner.id, data_science)
    catalog_api.delete_catalog_pathway(data_science)
    assert catalog_api.get_pathway_enrollments(learner.id).count() == 0
