"""
Tests of the Pathways API: the versioned content half of a Pathway.
"""
# mypy: disable-error-code="misc"
# (Ignore 'Unexpected attribute "org_code" for model "CatalogCourse"' until
#  https://github.com/typeddjango/django-stubs/issues/1034 is fixed.)

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from freezegun import freeze_time
from opaque_keys.edx.locator import CourseLocator
from organizations.api import ensure_organization  # type: ignore[import]

from openedx_catalog import api as catalog_api
from openedx_catalog.models import CatalogCourse, CatalogPathway, CourseRun
from openedx_content.applets.publishing import api as publishing_api
from openedx_learning import api as learning_api
from openedx_learning.applets.pathways import api as pathways_api  # For the import/restore building blocks.
from openedx_learning.models_api import Pathway, PathwayItem, PathwayItemVersion, PathwayVersion


class PathwaysTestCase(TestCase):
    """
    Base class holding a Catalog Pathway, an empty Pathway implementing it, and some course runs.
    """

    catalog_pathway: CatalogPathway
    pathway: Pathway
    now: datetime
    run_a1: CourseRun
    run_a2: CourseRun
    run_b1: CourseRun

    @classmethod
    def setUpTestData(cls) -> None:
        cls.now = datetime(2026, 5, 8, tzinfo=timezone.utc)
        ensure_organization("Org1")
        cls.catalog_pathway = catalog_api.create_catalog_pathway(
            org_code="Org1",
            pathway_code="DataScience",
            title="Data Science Professional Certificate",
        )
        cls.pathway, _version = learning_api.create_pathway_and_version(
            catalog_pathway=cls.catalog_pathway,
            title="Pathway",
            created=cls.now,
        )
        course_a = CatalogCourse.objects.create(org_code="Org1", course_code="CourseA")
        course_b = CatalogCourse.objects.create(org_code="Org1", course_code="CourseB")
        cls.run_a1 = cls._make_run(course_a, "2026")
        cls.run_a2 = cls._make_run(course_a, "2025")
        cls.run_b1 = cls._make_run(course_b, "2026")

    @classmethod
    def _make_run(cls, catalog_course: CatalogCourse, run_code: str) -> CourseRun:
        """Create a CourseRun of the given catalog course."""
        return CourseRun.objects.create(
            catalog_course=catalog_course,
            run_code=run_code,
            course_key=CourseLocator(
                org=catalog_course.org_code,
                course=catalog_course.course_code,
                run=run_code,
            ),
        )

    def create_pathway(self, catalog_pathway_code: str) -> Pathway:
        """Helper to create another Catalog Pathway and a Pathway, with an empty first version, implementing it."""
        catalog_pathway = catalog_api.create_catalog_pathway(org_code="Org1", pathway_code=catalog_pathway_code)
        pathway, _version = learning_api.create_pathway_and_version(
            catalog_pathway=catalog_pathway,
            title="Another Pathway",
            created=self.now,
        )
        return pathway

    def create_item(
        self,
        item_code: str,
        *,
        title: str = "Item",
        course_runs=(),
        pathway: Pathway | None = None,
    ) -> tuple[PathwayItem, PathwayItemVersion]:
        """Helper to create an Item of ``pathway`` (by default, of ``self.pathway``) and its first version."""
        return learning_api.create_pathway_item_and_version(
            pathway or self.pathway,
            item_code,
            title=title,
            course_runs=course_runs,
            created=self.now,
        )

    def set_items(self, items, *, pathway: Pathway | None = None) -> PathwayVersion:
        """Helper to list Items, in order, in a new version of ``pathway`` (by default, of ``self.pathway``)."""
        return learning_api.create_next_pathway_version(pathway or self.pathway, items=items, created=self.now)

    def publish(self, pathway: Pathway | None = None) -> None:
        """Publish every draft of ``pathway`` (by default, of ``self.pathway``), which has a package of its own."""
        publishing_api.publish_all_drafts((pathway or self.pathway).learning_package_id, published_at=self.now)


class PathwayCreationTest(PathwaysTestCase):
    """
    Creating a Pathway: its first version, its own Learning Package, and the default timestamp.
    """

    def test_create_pathway_and_version(self):
        """A new Pathway implements its Catalog Pathway and starts with a single version, listing no Items yet."""
        statistics = catalog_api.create_catalog_pathway(org_code="Org1", pathway_code="Stats")
        pathway, version = learning_api.create_pathway_and_version(
            catalog_pathway=statistics,
            title="Pathway",
            created=self.now,
        )

        assert isinstance(pathway, Pathway)
        assert isinstance(version, PathwayVersion)
        assert pathway.catalog_pathway == statistics
        assert version.version_num == 1
        assert pathway.versioning.draft == version
        assert pathway.versioning.published is None
        assert pathway.versioning.has_unpublished_changes
        assert not version.item_rows.exists()

    def test_a_pathway_gets_a_learning_package_of_its_own(self):
        """Named after the Catalog Pathway, whose key is unique across the instance, so it's easy to recognize."""
        statistics = catalog_api.create_catalog_pathway(org_code="Org1", pathway_code="Stats", title="Statistics")
        pathway, _version = learning_api.create_pathway_and_version(catalog_pathway=statistics, title="Statistics")

        assert pathway.learning_package != self.pathway.learning_package
        assert pathway.learning_package.package_ref == statistics.key_str
        assert pathway.learning_package.title == "Statistics"

    def test_a_package_left_behind_by_a_deleted_pathway_is_reused(self):
        statistics = catalog_api.create_catalog_pathway(org_code="Org1", pathway_code="Stats", title="Statistics")
        package = publishing_api.create_learning_package(package_ref=statistics.key_str, title="Left behind")

        pathway, _version = learning_api.create_pathway_and_version(catalog_pathway=statistics, title="Statistics")
        assert pathway.learning_package == package

    def test_a_catalog_pathway_cannot_get_a_second_pathway(self):
        """To change what a Catalog Pathway requires, create a new version of its Pathway instead."""
        with pytest.raises(IntegrityError), transaction.atomic():
            learning_api.create_pathway_and_version(catalog_pathway=self.catalog_pathway, title="Second")

    def test_created_defaults_to_now(self):
        """A single call stamps everything it creates with the same time."""
        created_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with freeze_time(created_at):
            item, item_version = learning_api.create_pathway_item_and_version(self.pathway, "item-1", title="Item")
            version = learning_api.create_next_pathway_version(self.pathway, items=[item])

        assert item.publishable_entity.created == created_at
        assert item_version.created == created_at
        assert version.created == created_at


class CatalogPathwayLookupTest(PathwaysTestCase):
    """
    Finding the Pathway that implements a Catalog Pathway, from the content side, where the link lives.
    """

    def test_the_pathway_implementing_a_catalog_pathway(self):
        assert learning_api.get_pathway_for_catalog_pathway(self.catalog_pathway) == self.pathway
        assert learning_api.get_pathway_for_catalog_pathway(self.catalog_pathway.id) == self.pathway

    def test_a_catalog_pathway_nothing_implements_yet(self):
        """A Catalog Pathway may be a placeholder for a Pathway that doesn't exist yet."""
        placeholder = catalog_api.create_catalog_pathway(org_code="Org1", pathway_code="Placeholder")
        assert learning_api.get_pathway_for_catalog_pathway(placeholder) is None

    def test_the_link_does_not_depend_on_publishing(self):
        """
        The link isn't versioned: it holds before anything is published and after the Pathway is soft-deleted. Whether
        there is a definition to show is up to the Pathway's versions.
        """
        assert self.pathway.versioning.published is None
        assert learning_api.get_pathway_for_catalog_pathway(self.catalog_pathway) == self.pathway

        publishing_api.soft_delete_draft(self.pathway.id)
        pathway = learning_api.get_pathway_for_catalog_pathway(self.catalog_pathway)
        assert pathway == self.pathway
        assert pathway.versioning.draft is None


class PathwayVersioningTest(PathwaysTestCase):
    """
    The Pathway definition is versioned; the catalog copy is not.
    """

    def test_catalog_edits_do_not_create_versions(self):
        """
        Editing the catalog half never touches the content half. This is the whole reason the two are separate.
        """
        catalog_api.update_catalog_pathway(self.catalog_pathway, title="Renamed by marketing")

        pathway = learning_api.get_pathway(self.pathway.id)
        assert pathway.versioning.latest.version_num == 1

    def test_definition_edits_do_create_versions(self):
        """Reordering, adding, or removing Items is a change to the definition."""
        item_1, _ = self.create_item("item-1")
        item_2, _ = self.create_item("item-2")

        v2 = self.set_items([item_1, item_2])
        assert v2.version_num == 2
        assert [row.pathway_item for row in v2.item_rows.all()] == [item_1, item_2]

    def test_next_version_keeps_unspecified_fields(self):
        """Passing None keeps the previous value, so a metadata-only edit is cheap."""
        item_1, _ = self.create_item("item-1")
        v2 = self.set_items([item_1])

        v3 = learning_api.create_next_pathway_version(self.pathway, title="Renamed", created=self.now)
        assert v3.title == "Renamed"
        assert v3.version_num == v2.version_num + 1
        assert [row.pathway_item for row in v3.item_rows.all()] == [item_1]

    def test_next_version_by_pathway_id(self):
        v2 = learning_api.create_next_pathway_version(self.pathway.id, title="Renamed", created=self.now)
        assert v2.pathway == self.pathway
        assert v2.version_num == 2

    def test_a_pathway_with_no_versions_has_no_next_version(self):
        """Only the import/restore building block can leave a Pathway without versions, and nothing builds on that."""
        statistics = catalog_api.create_catalog_pathway(org_code="Org1", pathway_code="Stats")
        package = publishing_api.create_learning_package(package_ref="no-versions", title="No versions")
        pathway = pathways_api.create_pathway(package.id, statistics, self.now, None)

        with pytest.raises(PathwayVersion.DoesNotExist):
            learning_api.create_next_pathway_version(pathway, title="Next", created=self.now)


class PathwayItemOwnershipTest(PathwaysTestCase):
    """
    A Pathway owns its Items: they are never shared, unlike the course runs that fulfill them.
    """

    def test_an_item_belongs_to_its_pathway(self):
        item_1, _ = self.create_item("item-1")

        assert item_1.pathway == self.pathway
        assert item_1.publishable_entity.learning_package_id == self.pathway.learning_package_id
        assert learning_api.get_pathway_item_by_code(self.pathway, "item-1") == item_1

    def test_a_version_cannot_list_another_pathways_item(self):
        """The database can't compare an Item's Pathway with the version's, so the API does."""
        other = self.create_pathway("other")
        foreign_item, _ = self.create_item("item-1", pathway=other)

        with pytest.raises(ValidationError):
            self.set_items([foreign_item])

    def test_creating_an_item_does_not_list_it(self):
        """An Item is listed in its Pathway by a new version of the Pathway, not by being created."""
        self.create_item("item-1")

        pathway = learning_api.get_pathway(self.pathway.id)
        assert not learning_api.get_items_in_pathway(pathway, published=False)


class PathwayItemOrderingTest(PathwaysTestCase):
    """
    A Pathway holds an ordered list of Items (ADR 0005, decision 1).
    """

    def test_items_come_back_in_author_defined_order(self):
        item_1, _ = self.create_item("item-1", title="First")
        item_2, _ = self.create_item("item-2", title="Second")
        item_3, _ = self.create_item("item-3", title="Third")
        self.set_items([item_3, item_1, item_2])

        entries = learning_api.get_items_in_pathway(self.pathway, published=False)
        assert [entry.pathway_item for entry in entries] == [item_3, item_1, item_2]
        assert [entry.order_num for entry in entries] == [0, 1, 2]
        assert [entry.pathway_item_version.title for entry in entries] == ["Third", "First", "Second"]

    def test_reordering_creates_a_new_version_and_leaves_the_old_one_intact(self):
        """
        Versions are immutable, which is what lets us say what the definition was at a given moment.
        """
        item_1, _ = self.create_item("item-1")
        item_2, _ = self.create_item("item-2")
        v2 = self.set_items([item_1, item_2])

        self.set_items([item_2, item_1])
        assert [row.pathway_item for row in v2.item_rows.all()] == [item_1, item_2]

    def test_items_are_unpinned(self):
        """
        A Pathway tracks the current state of its Items, not a snapshot. Revising an Item is visible through the Pathway
        without a new Pathway version.
        """
        item_1, _ = self.create_item("item-1", title="Original title")
        self.set_items([item_1])

        learning_api.create_next_pathway_item_version(item_1, title="Revised title", created=self.now)
        pathway = learning_api.get_pathway(self.pathway.id)
        entries = learning_api.get_items_in_pathway(pathway, published=False)
        assert [entry.pathway_item_version.title for entry in entries] == ["Revised title"]

    def test_new_version_is_visible_on_the_instance_passed_in(self):
        """
        Creating a version invalidates the draft cached on the caller's instance, so they don't keep reading the version
        they just replaced.
        """
        item_1, _ = self.create_item("item-1")
        item_2, _ = self.create_item("item-2")
        pathway = learning_api.get_pathway(self.pathway.id)
        learning_api.create_next_pathway_version(pathway, items=[item_1], created=self.now)
        assert [entry.pathway_item for entry in learning_api.get_items_in_pathway(pathway, published=False)] == [item_1]

        learning_api.create_next_pathway_version(pathway, items=[item_1, item_2], created=self.now)
        entries = learning_api.get_items_in_pathway(pathway, published=False)
        assert [entry.pathway_item for entry in entries] == [item_1, item_2]

    def test_items_are_dependencies_of_the_pathway_version(self):
        """
        Declaring Items as dependencies is what makes a change to an Item register as a change to the Pathway
        containing it.
        """
        item_1, _ = self.create_item("item-1")
        v2 = self.set_items([item_1])

        dependencies = v2.publishable_entity_version.dependencies.all()
        assert list(dependencies) == [item_1.publishable_entity]


class PathwayPublishingTest(PathwaysTestCase):
    """
    Drafts and published versions are separate views of the same Pathway.
    """

    def test_published_and_draft_views_differ(self):
        item_1, _ = self.create_item("item-1")
        item_2, _ = self.create_item("item-2")
        self.set_items([item_1])
        self.publish()

        self.set_items([item_1, item_2])
        pathway = learning_api.get_pathway(self.pathway.id)

        published = learning_api.get_items_in_pathway(pathway, published=True)
        draft = learning_api.get_items_in_pathway(pathway, published=False)
        assert [entry.pathway_item for entry in published] == [item_1]
        assert [entry.pathway_item for entry in draft] == [item_1, item_2]

    def test_publishing_a_pathway_publishes_its_items(self):
        """
        Because Items are declared as dependencies of the Pathway version, publishing the Pathway carries its
        unpublished Items along. A learner never sees a Pathway pointing at an Item that isn't published yet.
        """
        item_1, _ = self.create_item("item-1")
        self.set_items([item_1])
        self.publish()

        item_2, _ = self.create_item("item-2")
        self.set_items([item_1, item_2])
        assert learning_api.get_pathway_item(item_2.id).versioning.published is None

        publishing_api.publish_from_drafts(
            self.pathway.learning_package_id,
            draft_qset=publishing_api.get_all_drafts(self.pathway.learning_package_id).filter(
                entity=self.pathway.publishable_entity,
            ),
        )
        pathway = learning_api.get_pathway(self.pathway.id)
        published = learning_api.get_items_in_pathway(pathway, published=True)
        assert [entry.pathway_item for entry in published] == [item_1, item_2]

    def test_deleted_items_are_skipped(self):
        """
        An Item whose draft has been soft-deleted has no version to resolve to, so it drops out of the Pathway's list
        rather than breaking the query.
        """
        item_1, _ = self.create_item("item-1")
        item_2, _ = self.create_item("item-2")
        self.set_items([item_1, item_2])

        publishing_api.soft_delete_draft(item_2.id)

        pathway = learning_api.get_pathway(self.pathway.id)
        draft = learning_api.get_items_in_pathway(pathway, published=False)
        assert [entry.pathway_item for entry in draft] == [item_1]

    def test_pathway_with_no_published_version(self):
        with pytest.raises(PathwayVersion.DoesNotExist):
            learning_api.get_items_in_pathway(self.pathway, published=True)


class PathwayItemFulfillmentTest(PathwaysTestCase):
    """
    Mapping Items to the course runs that fulfill them (ADR 0006, decisions 1-3).
    """

    def test_an_item_can_list_runs_of_different_catalog_courses(self):
        """
        Passing any one of the listed runs fulfills the Item, and the list is explicit rather than "any run of this
        catalog course".
        """
        _item, _version = self.create_item(
            "item-1",
            course_runs=[
                learning_api.FulfillingCourseRun(course_run=self.run_a1, enrollment_track="verified"),
                self.run_a2,
                self.run_b1,
            ],
        )
        item = learning_api.get_pathway_item_by_code(self.pathway, "item-1")
        entries = learning_api.get_course_runs_for_item(item, published=False)
        assert [entry.course_run for entry in entries] == [self.run_a1, self.run_a2, self.run_b1]
        assert [entry.order_num for entry in entries] == [0, 1, 2]
        assert entries[0].enrollment_track == "verified"

    def test_the_preferred_run_is_the_first_in_the_list(self):
        """
        Priority is the list order, so the run to enroll a learner in is always one that fulfills the Item.
        """
        _item, version = self.create_item("item-1", course_runs=[self.run_a2, self.run_a1])
        preferred_row = version.preferred_course_run_row
        assert preferred_row is not None
        assert preferred_row.course_run == self.run_a2
        assert preferred_row.course_run in [row.course_run for row in version.course_run_rows.all()]

    def test_an_item_with_no_runs_has_no_preferred_run(self):
        _item, version = self.create_item("item-1")
        assert version.preferred_course_run_row is None

    def test_every_run_may_carry_an_enrollment_track(self):
        """The track belongs to the run, not to its priority: a learner may be enrolled in any of them."""
        _item, version = self.create_item(
            "item-1",
            course_runs=[
                learning_api.FulfillingCourseRun(course_run=self.run_a1, enrollment_track="verified"),
                learning_api.FulfillingCourseRun(course_run=self.run_a2, enrollment_track="audit"),
            ],
        )
        assert [row.enrollment_track for row in version.course_run_rows.all()] == ["verified", "audit"]

    def test_a_run_cannot_be_listed_twice(self):
        with pytest.raises(ValidationError):
            self.create_item("item-1", course_runs=[self.run_a1, self.run_a1])

    def test_reprioritizing_runs_creates_a_new_item_version(self):
        """
        Giving a learner who failed the old run another attempt means putting a newer run first, in a new version.
        """
        item, v1 = self.create_item("item-1", course_runs=[self.run_a2, self.run_a1])
        v2 = learning_api.create_next_pathway_item_version(
            item,
            course_runs=[self.run_a1, self.run_a2],
            created=self.now,
        )
        assert v2.version_num == 2
        assert v1.preferred_course_run_row.course_run == self.run_a2
        assert v2.preferred_course_run_row.course_run == self.run_a1
        # Both runs still fulfill the Item; only the priority changed.
        assert [row.course_run for row in v2.course_run_rows.all()] == [self.run_a1, self.run_a2]

    def test_next_item_version_keeps_the_run_list_by_default(self):
        item, _v1 = self.create_item(
            "item-1",
            course_runs=[
                learning_api.FulfillingCourseRun(course_run=self.run_a1, enrollment_track="verified"),
                self.run_a2,
            ],
        )
        v2 = learning_api.create_next_pathway_item_version(item, title="Renamed", created=self.now)
        assert [row.course_run for row in v2.course_run_rows.all()] == [self.run_a1, self.run_a2]
        assert v2.preferred_course_run_row.course_run == self.run_a1
        assert v2.preferred_course_run_row.enrollment_track == "verified"

    def test_narrowing_an_item_is_allowed(self):
        """
        Dropping a run is an ordinary edit. Whether an already-granted credential survives it is a question for the
        evaluation layer, not this one.
        """
        item, _v1 = self.create_item("item-1", course_runs=[self.run_a1, self.run_a2])
        v2 = learning_api.create_next_pathway_item_version(item, course_runs=[self.run_a1], created=self.now)
        assert [row.course_run for row in v2.course_run_rows.all()] == [self.run_a1]

    def test_items_by_pathway_and_item_id(self):
        """Both creating an Item and versioning it accept IDs as well as model instances."""
        item, _v1 = learning_api.create_pathway_item_and_version(
            self.pathway.id,
            "item-1",
            title="Item",
            course_runs=[self.run_a1],
            created=self.now,
        )
        assert item.pathway == self.pathway

        v2 = learning_api.create_next_pathway_item_version(item.id, title="Renamed", created=self.now)
        assert v2.pathway_item == item
        assert v2.version_num == 2
        assert [row.course_run for row in v2.course_run_rows.all()] == [self.run_a1]

    def test_an_item_with_no_versions_has_no_next_version(self):
        """Only the import/restore building block can leave an Item without versions, and nothing builds on that."""
        item = pathways_api.create_pathway_item(self.pathway, "item-1", self.now, None)

        with pytest.raises(PathwayItemVersion.DoesNotExist):
            learning_api.create_next_pathway_item_version(item, title="Next", created=self.now)

    def test_course_runs_of_an_item_with_no_version_in_that_state(self):
        """An Item that was never published has no published runs; that's an empty list, not an error."""
        item, _v1 = self.create_item("item-1", course_runs=[self.run_a1])

        assert not learning_api.get_course_runs_for_item(item, published=True)
        assert [entry.course_run for entry in learning_api.get_course_runs_for_item(item, published=False)] == [
            self.run_a1
        ]


class PathwayFulfillmentQueriesTest(PathwaysTestCase):
    """
    The queries that ADR 0006's evaluation triggers are built on.
    """

    def test_one_run_can_fulfill_several_items(self):
        """
        Each Item is fulfilled independently; the overlap is resolved at this layer and never leaks up to the Pathway.
        """
        item_1, _ = self.create_item("item-1", course_runs=[self.run_a1])
        item_2, _ = self.create_item("item-2", course_runs=[self.run_a1, self.run_b1])
        self.set_items([item_1, item_2])
        self.publish()

        fulfilled = learning_api.get_pathway_items_fulfilled_by_course_run(self.run_a1, published=True)
        assert set(fulfilled) == {item_1, item_2}

    def test_run_that_fulfills_nothing(self):
        item_1, _ = self.create_item("item-1", course_runs=[self.run_a1])
        self.set_items([item_1])
        self.publish()

        assert not learning_api.get_pathway_items_fulfilled_by_course_run(self.run_b1, published=True).exists()

    def test_draft_only_changes_are_invisible_to_the_published_query(self):
        """
        An author can revise an Item repeatedly; only the published result should reach learners (ADR 0006, decision 7).
        """
        item_1, _ = self.create_item("item-1", course_runs=[self.run_a1])
        self.set_items([item_1])
        self.publish()

        learning_api.create_next_pathway_item_version(item_1, course_runs=[self.run_a1, self.run_b1], created=self.now)
        published = learning_api.get_pathway_items_fulfilled_by_course_run(self.run_b1, published=True)
        draft = learning_api.get_pathway_items_fulfilled_by_course_run(self.run_b1, published=False)
        assert not published.exists()
        assert set(draft) == {item_1}

    def test_a_run_can_count_towards_several_pathways(self):
        """
        Items are never shared, but course runs are: one run can count towards several Pathways through different
        Items.
        """
        other = self.create_pathway("Stats")
        item_1, _ = self.create_item("item-1", course_runs=[self.run_a1])
        item_2, _ = self.create_item("item-2", course_runs=[self.run_b1])
        item_3, _ = self.create_item("item-3", course_runs=[self.run_a1], pathway=other)
        self.set_items([item_1, item_2])
        self.set_items([item_3], pathway=other)
        self.publish()
        self.publish(other)

        def containing(course_run):
            return set(learning_api.get_pathways_containing_course_run(course_run, published=True))

        assert containing(self.run_a1) == {self.pathway, other}
        assert containing(self.run_b1) == {self.pathway}
        assert not containing(self.run_a2)

    def test_an_item_dropped_from_the_pathway_no_longer_counts(self):
        """
        An Item still belongs to its Pathway after being dropped from the Pathway's list, but its runs stop counting
        towards the Pathway - in the draft straight away, and in the published view once that is published.
        """
        item_1, _ = self.create_item("item-1", course_runs=[self.run_a1])
        self.set_items([item_1])
        self.publish()

        self.set_items([])
        assert set(learning_api.get_pathways_containing_course_run(self.run_a1, published=True)) == {self.pathway}
        assert not learning_api.get_pathways_containing_course_run(self.run_a1, published=False).exists()
