"""
Tests of the database-level guarantees the Pathways models make.

The API validates these too, but the constraints are what protects the data from callers that reach past it.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError

from openedx_learning import api as learning_api
from openedx_learning.models_api import PathwayItemCourseRun, PathwayVersionItem

from .test_api import PathwaysTestCase


class PathwayConstraintsTest(PathwaysTestCase):
    """
    Constraints on Pathways and their Items.
    """

    def test_pathway_code_unique_within_learning_package(self):
        self.create_pathway("data-science")
        with pytest.raises(IntegrityError), transaction.atomic():
            self.create_pathway("data-science")

    def test_item_code_unique_within_learning_package(self):
        self.create_item("item-1")
        with pytest.raises(IntegrityError), transaction.atomic():
            self.create_item("item-1")

    def test_an_item_cannot_appear_twice_in_a_version(self):
        item_1, _ = self.create_item("item-1")
        _pathway, version = self.create_pathway(items=[item_1])

        with pytest.raises(IntegrityError), transaction.atomic():
            PathwayVersionItem.objects.create(pathway_version=version, pathway_item=item_1, order_num=1)

    def test_two_items_cannot_share_a_position(self):
        item_1, _ = self.create_item("item-1")
        item_2, _ = self.create_item("item-2")
        _pathway, version = self.create_pathway(items=[item_1])

        with pytest.raises(IntegrityError), transaction.atomic():
            PathwayVersionItem.objects.create(pathway_version=version, pathway_item=item_2, order_num=0)

    def test_an_item_in_use_cannot_be_deleted(self):
        """
        RESTRICT, so that removing an Item from a Pathway is a new version rather than a hole in an old one.
        """
        item_1, _ = self.create_item("item-1")
        self.create_pathway(items=[item_1])

        with pytest.raises(RestrictedError), transaction.atomic():
            item_1.delete()


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

    def test_at_most_one_default_run(self):
        """
        Conditional unique constraints are ignored by MySQL and MariaDB, so on those backends this invariant rests on
        the API check alone.
        """
        _item, version = self.create_item(
            "item-1",
            course_runs=[learning_api.FulfillingCourseRun(course_run=self.run_a1, is_default=True)],
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            PathwayItemCourseRun.objects.create(
                pathway_item_version=version,
                course_run=self.run_a2,
                order_num=1,
                is_default=True,
            )

    def test_enrollment_track_requires_the_default_flag(self):
        _item, version = self.create_item("item-1", course_runs=[self.run_a1])
        with pytest.raises(IntegrityError), transaction.atomic():
            PathwayItemCourseRun.objects.create(
                pathway_item_version=version,
                course_run=self.run_a2,
                order_num=1,
                is_default=False,
                enrollment_track="verified",
            )

    def test_a_run_in_use_cannot_be_deleted(self):
        """RESTRICT, so a run can't vanish out of an Item's fulfillment list."""
        self.create_item("item-1", course_runs=[self.run_a1])
        with pytest.raises(RestrictedError), transaction.atomic():
            self.run_a1.delete()
