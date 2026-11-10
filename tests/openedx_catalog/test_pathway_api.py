"""
Tests of the catalog-side Pathway API.
"""
# pylint: disable=unused-argument

from datetime import datetime, timezone

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import ProtectedError
from django.db.models.signals import post_save
from django.db.utils import IntegrityError
from freezegun import freeze_time
from organizations.api import ensure_organization  # type: ignore[import]

from openedx_catalog import api as catalog_api
from openedx_catalog.models import CatalogPathway, PathwayCategory, PathwayEnrollment

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(name="org1")
def _org1() -> None:
    """Create an "Org1" organization for use in these tests"""
    ensure_organization("Org1")


@pytest.fixture(name="category")
def _category() -> PathwayCategory:
    """The category created by the initial migration"""
    return catalog_api.get_pathway_category("pathway")


@pytest.fixture(name="data_science")
def _data_science(org1, category) -> CatalogPathway:
    """Create a CatalogPathway for use in these tests"""
    return catalog_api.create_catalog_pathway(
        org_code="Org1",
        pathway_code="DataScience",
        category=category,
        title="Data Science Professional Certificate",
        description="Learn data science.",
    )


@pytest.fixture(name="learner")
def _learner():
    """Create a learner for use in these tests"""
    return User.objects.create(username="learner", email="learner@example.com")


def test_get_pathway_category() -> None:
    """Categories are looked up by their stable code, not by the learner-facing name."""
    masters = catalog_api.create_pathway_category(category_code="masters-degree", title="Master's Degree")
    assert catalog_api.get_pathway_category("masters-degree") == masters
    with pytest.raises(PathwayCategory.DoesNotExist):
        catalog_api.get_pathway_category("Master's Degree")


def test_get_pathway_categories(category) -> None:
    """All categories, ordered by title, including the one created by the initial migration."""
    masters = catalog_api.create_pathway_category(category_code="masters-degree", title="Master's Degree")
    assert list(catalog_api.get_pathway_categories()) == [masters, category]


def test_create_pathway_category() -> None:
    created = catalog_api.create_pathway_category(category_code="class", title="Class", title_plural="Classes")
    category = catalog_api.get_pathway_category("class")
    assert category == created
    assert (category.title, category.get_title_plural()) == ("Class", "Classes")


def test_create_pathway_category_with_a_taken_code() -> None:
    """Codes are case-insensitively unique, so the initial "pathway" code can't be reused in any case."""
    with pytest.raises(IntegrityError), transaction.atomic():
        catalog_api.create_pathway_category(category_code="Pathway", title="Another Pathway")


def test_update_pathway_category() -> None:
    """Only the fields passed are changed, and an empty plural goes back to deriving it from the title."""
    catalog_api.create_pathway_category(category_code="class", title="Class")

    catalog_api.update_pathway_category("class", title_plural="Classes")
    category = catalog_api.get_pathway_category("class")
    assert (category.title, category.get_title_plural()) == ("Class", "Classes")

    catalog_api.update_pathway_category(category, title="Program", title_plural="")
    category = catalog_api.get_pathway_category("class")
    assert (category.title, category.get_title_plural()) == ("Program", "Programs")


def test_update_pathway_category_with_nothing_to_change(django_assert_num_queries) -> None:
    """Passing no fields is a no-op that doesn't touch the database."""
    category = catalog_api.create_pathway_category(category_code="class", title="Class")
    with django_assert_num_queries(0):
        catalog_api.update_pathway_category(category)


def test_delete_pathway_category() -> None:
    """The category may be given as an instance or by its code."""
    catalog_api.create_pathway_category(category_code="class", title="Class")
    catalog_api.delete_pathway_category("class")
    catalog_api.delete_pathway_category(catalog_api.get_pathway_category("pathway"))
    assert not catalog_api.get_pathway_categories().exists()
    with pytest.raises(PathwayCategory.DoesNotExist):
        catalog_api.delete_pathway_category("class")


def test_delete_pathway_category_in_use(data_science, category) -> None:
    """A category in use can't be deleted until its pathways are moved to another one."""
    with pytest.raises(ProtectedError), transaction.atomic():
        catalog_api.delete_pathway_category(category)

    masters = catalog_api.create_pathway_category(category_code="masters-degree", title="Master's Degree")
    catalog_api.update_catalog_pathway(data_science, category=masters)
    catalog_api.delete_pathway_category(category)
    assert list(catalog_api.get_pathway_categories()) == [masters]


def test_create_catalog_pathway(org1) -> None:
    """Operators can add categories of their own, and a blank title falls back to the code."""
    masters = catalog_api.create_pathway_category(category_code="masters-degree", title="Master's Degree")
    pathway = catalog_api.create_catalog_pathway(org_code="Org1", pathway_code="CompSci", category=masters)
    assert pathway.category == masters
    assert pathway.title == "CompSci"


def test_get_catalog_pathway(data_science) -> None:
    """A catalog pathway can be looked up by pk, key string, or org + code."""
    assert catalog_api.get_catalog_pathway(pk=data_science.id) == data_science
    assert catalog_api.get_catalog_pathway(key_str=data_science.key_str) == data_science
    assert catalog_api.get_catalog_pathway(org_code="Org1", pathway_code="DataScience") == data_science
    with pytest.raises(CatalogPathway.DoesNotExist):
        catalog_api.get_catalog_pathway(org_code="Org1", pathway_code="Nope")


@pytest.fixture(name="three_pathways")
def _three_pathways(org1, category) -> tuple[CatalogPathway, CatalogPathway, CatalogPathway]:
    """Create three catalog pathways across two orgs and two categories, a day apart, oldest first."""
    ensure_organization("Org2")
    masters = catalog_api.create_pathway_category(category_code="masters-degree", title="Master's Degree")
    with freeze_time(datetime(2026, 1, 1, tzinfo=timezone.utc)):
        data_science = catalog_api.create_catalog_pathway(
            org_code="Org1", pathway_code="DataScience", category=category
        )
    with freeze_time(datetime(2026, 1, 2, tzinfo=timezone.utc)):
        comp_sci = catalog_api.create_catalog_pathway(org_code="Org1", pathway_code="CompSci", category=masters)
    with freeze_time(datetime(2026, 1, 3, tzinfo=timezone.utc)):
        history = catalog_api.create_catalog_pathway(org_code="Org2", pathway_code="History", category=masters)
    return data_science, comp_sci, history


def test_get_catalog_pathways(three_pathways) -> None:
    """All catalog pathways, most recently created first."""
    data_science, comp_sci, history = three_pathways
    assert list(catalog_api.get_catalog_pathways()) == [history, comp_sci, data_science]


def test_get_catalog_pathways_by_org_and_category(three_pathways) -> None:
    """The filters combine, and an unknown org or category matches nothing rather than failing."""
    data_science, comp_sci, history = three_pathways
    assert list(catalog_api.get_catalog_pathways(org_code="Org1")) == [comp_sci, data_science]
    assert list(catalog_api.get_catalog_pathways(category_code="masters-degree")) == [history, comp_sci]
    assert list(catalog_api.get_catalog_pathways(org_code="Org1", category_code="masters-degree")) == [comp_sci]
    assert list(catalog_api.get_catalog_pathways(category_code="pathway")) == [data_science]
    assert not catalog_api.get_catalog_pathways(org_code="NoSuchOrg").exists()
    assert not catalog_api.get_catalog_pathways(category_code="no-such-category").exists()


def test_get_catalog_pathways_includes_org_and_category(three_pathways, django_assert_num_queries) -> None:
    """A listing can show each pathway's org and category in a single query, rather than a query per pathway."""
    with django_assert_num_queries(1):
        labels = [(p.org_code, p.category.get_title_plural()) for p in catalog_api.get_catalog_pathways()]
    assert labels == [("Org2", "Master's Degrees"), ("Org1", "Master's Degrees"), ("Org1", "Pathways")]


def test_get_catalog_pathway_includes_org_and_category(three_pathways, django_assert_num_queries) -> None:
    """Whichever way the pathway is looked up."""
    _data_science, comp_sci, _history = three_pathways
    lookups = [
        {"pk": comp_sci.id},
        {"key_str": comp_sci.key_str},
        {"org_code": "Org1", "pathway_code": "CompSci"},
    ]
    for lookup in lookups:
        with django_assert_num_queries(1):
            pathway = catalog_api.get_catalog_pathway(**lookup)
            assert (pathway.org_code, pathway.category.title) == ("Org1", "Master's Degree")


def test_update_catalog_pathway_by_id_and_category(data_science) -> None:
    """The pathway may be given by ID, and the category can be changed like any other catalog field."""
    masters = catalog_api.create_pathway_category(category_code="masters-degree", title="Master's Degree")
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


def test_enrollment_round_trip(data_science, learner) -> None:
    assert not catalog_api.is_enrolled_in_pathway(learner.id, data_science)

    enrollment = catalog_api.enroll_in_pathway(learner.id, data_science)
    assert catalog_api.is_enrolled_in_pathway(learner.id, data_science)
    assert list(catalog_api.get_pathway_enrollments(learner.id)) == [enrollment]

    catalog_api.unenroll_from_pathway(learner.id, data_science)
    assert not catalog_api.is_enrolled_in_pathway(learner.id, data_science)


def test_get_pathway_enrollments_includes_org_and_category(three_pathways, learner, django_assert_num_queries) -> None:
    """A learner's dashboard can show each pathway's org and category in a single query, rather than a query per row."""
    for pathway in three_pathways:
        catalog_api.enroll_in_pathway(learner.id, pathway)

    with django_assert_num_queries(1):
        labels = [
            (e.catalog_pathway.org_code, e.catalog_pathway.category.title)
            for e in catalog_api.get_pathway_enrollments(learner.id)
        ]
    assert sorted(labels) == [("Org1", "Master's Degree"), ("Org1", "Pathway"), ("Org2", "Master's Degree")]


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


def test_enrolling_and_unenrolling_send_post_save(data_science, learner) -> None:
    """Every change of `is_active` goes through `save()`, so `post_save` listeners (e.g. analytics sinks) see it."""
    saved: list[tuple[int, bool]] = []

    def receiver(sender, instance, **kwargs):
        saved.append((instance.id, instance.is_active))

    post_save.connect(receiver, sender=PathwayEnrollment)
    try:
        enrollment = catalog_api.enroll_in_pathway(learner.id, data_science)
        catalog_api.unenroll_from_pathway(learner.id, data_science)
        catalog_api.unenroll_from_pathway(learner.id, data_science)  # No-op: no signal.
        catalog_api.enroll_in_pathway(learner.id, data_science)
    finally:
        post_save.disconnect(receiver, sender=PathwayEnrollment)

    assert saved == [(enrollment.id, True), (enrollment.id, False), (enrollment.id, True)]


def test_deleting_a_pathway_keeps_enrollment_history(data_science, learner) -> None:
    """A pathway anyone was ever enrolled in can't be deleted, even after they've all unenrolled."""
    catalog_api.enroll_in_pathway(learner.id, data_science)
    catalog_api.unenroll_from_pathway(learner.id, data_science)

    with pytest.raises(ProtectedError), transaction.atomic():
        catalog_api.delete_catalog_pathway(data_science)
    assert catalog_api.get_pathway_enrollments(learner.id, include_inactive=True).count() == 1


def test_enrolling_in_a_missing_pathway(org1, learner) -> None:
    """A bad ID raises DoesNotExist, like the rest of the API, rather than an IntegrityError on some backends."""
    with pytest.raises(CatalogPathway.DoesNotExist):
        catalog_api.enroll_in_pathway(learner.id, CatalogPathway.CatalogPathwayID(999_999))
