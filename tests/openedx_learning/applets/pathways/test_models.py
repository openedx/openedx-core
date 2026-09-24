"""
Tests of the database-level guarantees the Pathways models make.

The API validates these too, but the constraints are what protects the data from callers that reach past it.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, RestrictedError

from openedx_catalog import api as catalog_api
from openedx_content import api as content_api
from openedx_learning.models_api import Pathway, PathwayItem, PathwayItemCourseRun, PathwayVersionItem

from .test_api import PathwaysTestCase


class PathwayConstraintsTest(PathwaysTestCase):
    """
    Constraints on Pathways and their Items.
    """

    def test_a_catalog_pathway_is_implemented_by_at_most_one_pathway(self):
        """Even by a Pathway in a different Learning Package, which the API would never create."""
        package = content_api.create_learning_package(package_ref="elsewhere", title="Elsewhere")
        entity = content_api.create_publishable_entity(package.id, "pathway", self.now, None)

        with pytest.raises(IntegrityError), transaction.atomic():
            Pathway.objects.create(
                publishable_entity=entity,
                learning_package=package,
                catalog_pathway=self.catalog_pathway,
            )

    def test_a_learning_package_holds_at_most_one_pathway(self):
        """Even under a different entity_ref, so that publishing a package always means publishing one Pathway."""
        statistics = catalog_api.create_catalog_pathway(org_code="Org1", pathway_code="Stats")
        package = self.pathway.learning_package
        entity = content_api.create_publishable_entity(package.id, "another-pathway", self.now, None)

        with pytest.raises(IntegrityError), transaction.atomic():
            Pathway.objects.create(publishable_entity=entity, learning_package=package, catalog_pathway=statistics)

    def test_item_code_unique_within_a_pathway(self):
        self.create_item("item-1")
        with pytest.raises(IntegrityError), transaction.atomic():
            self.create_item("item-1")

    def test_pathways_may_use_the_same_item_codes(self):
        other = self.create_pathway("Other")
        self.create_item("item-1")
        self.create_item("item-1", pathway=other)

        assert PathwayItem.objects.filter(item_code="item-1").count() == 2

    def test_an_item_cannot_appear_twice_in_a_version(self):
        item_1, _ = self.create_item("item-1")
        version = self.set_items([item_1])

        with pytest.raises(IntegrityError), transaction.atomic():
            PathwayVersionItem.objects.create(pathway_version=version, pathway_item=item_1, order_num=1)

    def test_two_items_cannot_share_a_position(self):
        item_1, _ = self.create_item("item-1")
        item_2, _ = self.create_item("item-2")
        version = self.set_items([item_1])

        with pytest.raises(IntegrityError), transaction.atomic():
            PathwayVersionItem.objects.create(pathway_version=version, pathway_item=item_2, order_num=0)

    def test_version_item_string_representation(self):
        """A version's Item row reads as the version, the Item's position, and the Item."""
        item_1, _ = self.create_item("item-1")
        version = self.set_items([item_1])

        assert str(version.item_rows.get()) == "pathway @ v2 - Pathway #0: pathway-item:item-1"

    def test_an_item_in_use_cannot_be_deleted(self):
        """
        RESTRICT, so that removing an Item from a Pathway is a new version rather than a hole in an old one.
        """
        item_1, _ = self.create_item("item-1")
        self.set_items([item_1])

        with pytest.raises(RestrictedError), transaction.atomic():
            item_1.delete()

    def test_deleting_the_learning_package_deletes_its_pathways_and_items(self):
        """
        Before anything is published, the RESTRICT from a version's Item rows doesn't block this, because those rows go
        in the same cascade. After publishing, something else does; see the next test.
        """
        item_1, _ = self.create_item("item-1")
        self.set_items([item_1])

        self.pathway.learning_package.delete()

        assert not Pathway.objects.exists()
        assert not PathwayItem.objects.exists()

    def test_publishing_blocks_deleting_the_learning_package(self):
        """
        A limitation of ``openedx_content``, not of Pathways, demonstrated here because every published Pathway hits it.

        The Pathway depends on its Items, so publishing them together records a ``PublishSideEffect`` between their
        publish records. ``PublishSideEffect.cause`` and ``.effect`` are RESTRICT foreign keys that nothing cascades to,
        so once a publish has recorded one, the package can no longer be cascade-deleted. Content containers are
        affected too, and even before publishing, because ``EntityListRow.entity`` is RESTRICT as well.

        If this starts failing, the limitation has been lifted: replace this test with a published variant of the one
        above.
        """
        item_1, _ = self.create_item("item-1")
        self.set_items([item_1])
        self.publish()

        with pytest.raises(RestrictedError) as exc_info, transaction.atomic():
            self.pathway.learning_package.delete()

        # The side effects are the only thing in the way; nothing in the Pathway models is.
        assert {type(obj).__name__ for obj in exc_info.value.restricted_objects} == {"PublishSideEffect"}

    def test_a_catalog_pathway_in_use_cannot_be_deleted(self):
        """
        PROTECT, so that deleting the catalog half can't leave learners enrolled in something with no definition.
        """
        with pytest.raises(ProtectedError), transaction.atomic():
            self.catalog_pathway.delete()


class PathwayItemCourseRunConstraintsTest(PathwaysTestCase):
    """
    Constraints on the mapping from an Item to the runs that fulfill it.
    """

    def test_a_run_cannot_be_listed_twice(self):
        _item, version = self.create_item("item-1", course_runs=[self.run_a1])
        with pytest.raises(IntegrityError), transaction.atomic():
            PathwayItemCourseRun.objects.create(pathway_item_version=version, course_run=self.run_a1, order_num=1)

    def test_two_runs_cannot_share_a_position(self):
        _item, version = self.create_item("item-1", course_runs=[self.run_a1])
        with pytest.raises(IntegrityError), transaction.atomic():
            PathwayItemCourseRun.objects.create(pathway_item_version=version, course_run=self.run_a2, order_num=0)

    def test_a_run_in_use_cannot_be_deleted(self):
        """RESTRICT, so a run can't vanish out of an Item's fulfillment list."""
        self.create_item("item-1", course_runs=[self.run_a1])
        with pytest.raises(RestrictedError), transaction.atomic():
            self.run_a1.delete()

    def test_string_representation(self):
        """A row reads as the course run it lists; its priority is shown by its position, not in its name."""
        _item, version = self.create_item("item-1", course_runs=[self.run_a1])
        assert str(version.course_run_rows.get()) == str(self.run_a1)
