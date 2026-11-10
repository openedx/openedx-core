"""
Tests of the Publishing app's python API
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase

from openedx_content.applets.containers import api as containers_api
from openedx_content.applets.publishing import api as publishing_api
from openedx_content.applets.publishing.models import (
    Draft,
    DraftChangeLog,
    DraftChangeLogRecord,
    DraftSideEffect,
    LearningPackage,
    PublishableEntity,
    PublishLog,
    PublishLogRecord,
)
from openedx_content.models_api import Container
from tests.test_django_app.models import TestContainer

User = get_user_model()


class LearningPackageTestCase(TestCase):
    """
    Test creating a LearningPackage
    """
    def test_normal(self) -> None:  # Note: we must specify '-> None' to opt in to type checking
        """
        Normal flow with no errors.
        """
        key = "my_key"
        title = "My Excellent Title with Emoji 🔥"
        created = datetime(2023, 4, 2, 15, 9, 0, tzinfo=timezone.utc)
        description = "A fun Description!"
        package = publishing_api.create_learning_package(
            package_ref=key,
            title=title,
            description=description,
            created=created
        )

        assert package.package_ref == "my_key"
        assert package.title == "My Excellent Title with Emoji 🔥"
        assert package.description == "A fun Description!"
        assert package.created == created
        assert package.updated == created

        # Should be auto-generated
        assert isinstance(package.uuid, UUID)

        # Having an actual value here means we were persisted to the database.
        assert isinstance(package.id, int)

        # Now test editing the fields.
        updated_package = publishing_api.update_learning_package(
            package.id,
            package_ref="new_key",
            title="new title",
            description="new description",
        )
        assert updated_package.package_ref == "new_key"
        assert updated_package.title == "new title"
        assert updated_package.description == "new description"
        assert updated_package.created == created
        assert updated_package.updated != created  # new time would be auto-generated

    def test_auto_datetime(self) -> None:
        """
        Auto-generated created datetime works as expected.
        """
        key = "my_key"
        title = "My Excellent Title with Emoji 🔥"
        package = publishing_api.create_learning_package(key, title)

        assert package.package_ref == "my_key"
        assert package.title == "My Excellent Title with Emoji 🔥"

        # Auto-generated datetime checking...
        assert isinstance(package.created, datetime)
        assert package.created == package.updated
        assert package.created.tzinfo == timezone.utc  # pylint: disable=no-member,useless-suppression

        # Should be auto-generated
        assert isinstance(package.uuid, UUID)

        # Having an actual value here means we were persisted to the database.
        assert isinstance(package.id, int)

    def test_non_utc_time(self) -> None:
        """
        Require UTC timezone for created.
        """
        with pytest.raises(ValidationError) as excinfo:
            publishing_api.create_learning_package(
                package_ref="my_key",
                title="A Title",
                created=datetime(2023, 4, 2)
            )
        message_dict = excinfo.value.message_dict

        # Both datetime fields should be marked as invalid
        assert "created" in message_dict
        assert "updated" in message_dict

    def test_already_exists(self) -> None:
        """
        Raises ValidationError for duplicate keys.
        """
        publishing_api.create_learning_package("my_key", "Original")
        with pytest.raises(ValidationError) as excinfo:
            publishing_api.create_learning_package("my_key", "Duplicate")
        message_dict = excinfo.value.message_dict
        assert "package_ref" in message_dict


class DraftTestCase(TestCase):
    """
    Test basic operations with Drafts.
    """
    now: datetime
    learning_package_1: LearningPackage
    learning_package_2: LearningPackage

    @classmethod
    def setUpTestData(cls) -> None:
        cls.now = datetime(2024, 1, 28, 16, 45, 30, tzinfo=timezone.utc)
        cls.learning_package_1 = publishing_api.create_learning_package(
            "my_package_key_1",
            "Draft Testing LearningPackage 🔥 1",
            created=cls.now,
        )
        cls.learning_package_2 = publishing_api.create_learning_package(
            "my_package_key_2",
            "Draft Testing LearningPackage 🔥 2",
            created=cls.now,
        )

    def test_draft_lifecycle(self) -> None:
        """
        Test basic lifecycle of a Draft.
        """
        entity = publishing_api.create_publishable_entity(
            self.learning_package_1.id,
            "my_entity",
            created=self.now,
            created_by=None,
        )
        # Drafts are NOT created when a PublishableEntity is created, only when
        # its first PublisahbleEntityVersion is.
        assert publishing_api.get_draft_version(entity.id) is None

        entity_version = publishing_api.create_publishable_entity_version(
            entity.id,
            version_num=1,
            title="An Entity 🌴",
            created=self.now,
            created_by=None,
        )
        assert entity_version == publishing_api.get_draft_version(entity.id)

        # We never really remove rows from the table holding Drafts. We just
        # mark the version as None.
        publishing_api.soft_delete_draft(entity.id)
        deleted_entity_version = publishing_api.get_draft_version(entity.id)
        assert deleted_entity_version is None

    def test_set_draft_args(self) -> None:
        """Make sure it works with Draft and int, and raises exception otherwise"""
        entity = publishing_api.create_publishable_entity(
            self.learning_package_1.id,
            "my_set_draft_args_entity",
            created=self.now,
            created_by=None,
        )
        entity_version = publishing_api.create_publishable_entity_version(
            entity.id,
            version_num=1,
            title="An Entity 🌴",
            created=self.now,
            created_by=None,
        )

        # Int calling version
        publishing_api.soft_delete_draft(entity.id)
        publishing_api.set_draft_version(entity.draft.pk, entity_version.pk)
        assert Draft.objects.get(entity=entity).version == entity_version

        # Draft calling version
        publishing_api.soft_delete_draft(entity.id)
        publishing_api.set_draft_version(entity.draft, entity_version.pk)
        assert Draft.objects.get(entity=entity).version == entity_version

        # Unrecognized type
        with pytest.raises(TypeError):
            publishing_api.set_draft_version(1.0, entity_version.pk)  # type: ignore[arg-type]

    def test_soft_deletes(self) -> None:
        """Test the publishing behavior of soft deletes."""
        entity = publishing_api.create_publishable_entity(
            self.learning_package_1.id,
            "my_entity",
            created=self.now,
            created_by=None,
        )
        entity_version = publishing_api.create_publishable_entity_version(
            entity.id,
            version_num=1,
            title="An Entity 🌴",
            created=self.now,
            created_by=None,
        )

        # Initial publish
        publish_log = publishing_api.publish_all_drafts(self.learning_package_1.id)
        log_records = list(publish_log.records.all())
        assert len(log_records) == 1
        record = log_records[0]
        assert record.entity_id == entity.id
        assert record.old_version is None
        assert record.new_version_id == entity_version.id

        # Publishing the soft-delete
        publishing_api.soft_delete_draft(entity.id)
        publish_log = publishing_api.publish_all_drafts(self.learning_package_1.id)
        log_records = list(publish_log.records.all())
        assert len(log_records) == 1
        record = log_records[0]
        assert record.entity_id == entity.id
        assert record.old_version_id == entity_version.id
        assert record.new_version is None

        # Verify that we do not re-publish soft-deleted records. We initially
        # had a bug here because NULL != NULL in SQL, so the check to "publish
        # all the Drafts that have different versions than their Published
        # counterparts" would mistakenly pull in records that were NULL in both
        # places.
        publish_log = publishing_api.publish_all_drafts(self.learning_package_1.id)
        assert publish_log.records.count() == 0

    def test_soft_delete_and_reset(self) -> None:
        """
        Test edge case where we create, soft-delete, and then reset.

        This is an edge case because creating and then soft-deleting an item
        sequence of actions will make both the Draft and Published version NULL.
        In this situation reset_drafts_to_published should NOT create a
        DraftChangeLog (because they already match, so there's nothing to do).
        But we had a bug that redundantly set the Draft version to NULL again
        because NULL != NULL in SQL and we were doing the Draft vs. Published
        comparison naively without taking that into account.
        """
        entity = publishing_api.create_publishable_entity(
            self.learning_package_1.id,
            "my_entity",
            created=self.now,
            created_by=None,
        )
        # Draft Change #1: create the new version
        publishing_api.create_publishable_entity_version(
            entity.id,
            version_num=1,
            title="An Entity 🌴",
            created=self.now,
            created_by=None,
        )
        assert DraftChangeLog.objects.count() == 1

        # Change #1: delete the draft (set the draft version to None)
        publishing_api.soft_delete_draft(entity.id)
        assert DraftChangeLog.objects.count() == 2

        # This should NOT create a change:
        publishing_api.reset_drafts_to_published(self.learning_package_1.id)
        assert DraftChangeLog.objects.count() == 2

    def test_reset_drafts_to_published(self) -> None:
        self.inner_reset_drafts_to_published(bulk=False)

    def test_reset_drafts_to_published_bulk(self) -> None:
        self.inner_reset_drafts_to_published(bulk=True)

    def inner_reset_drafts_to_published(self, bulk: bool) -> None:
        """
        Test throwing out Draft data and resetting to the Published versions.

        One place this might turn up is if we've imported an older version of
        the library and it causes a bunch of new versions to be created.

        Note that these tests don't associate any content with the versions
        being created. They don't have to, because making those content
        associations is the job of the ``components`` package, and potentially
        other higher-level things. We're never deleting ``PublishableEntity``
        or ``PublishableEntityVersion`` instances, so we don't have to worry
        about potentially breaking the associated models of those higher level
        apps. These tests just need to ensure that the Published and Draft
        models are updated properly to point to the correct versions.

        This could be broken up into separate tests for each scenario, but I
        wanted to make sure nothing went wrong when multiple types of reset were
        happening at the same time.
        """
        with (publishing_api.bulk_draft_changes_for(self.learning_package_1.id) if bulk else nullcontext()):
            # This is the Entity that's going to get a couple of new versions
            # between Draft and Published.
            entity_with_changes = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "entity_with_changes",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                entity_with_changes.id,
                version_num=1,
                title="I'm entity_with_changes v1",
                created=self.now,
                created_by=None,
            )

            # This Entity is going to remain unchanged between Draft and Published.
            entity_with_no_changes = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "entity_with_no_changes",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                entity_with_no_changes.id,
                version_num=1,
                title="I'm entity_with_no_changes v1",
                created=self.now,
                created_by=None,
            )

            # This Entity will be Published, but will then be soft-deleted.
            entity_with_pending_delete = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "entity_with_pending_delete",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                entity_with_pending_delete.id,
                version_num=1,
                title="I'm entity_with_pending_delete v1",
                created=self.now,
                created_by=None,
            )

        if bulk:
            # bulk_draft_changes_for creates only one DraftChangeLog for all the preceeding operations
            assert DraftChangeLog.objects.count() == 1

        # Publish!
        publishing_api.publish_all_drafts(self.learning_package_1.id)

        with (publishing_api.bulk_draft_changes_for(self.learning_package_1.id) if bulk else nullcontext()) as changes2:
            # Create versions 2, 3, 4 of entity_with_changes. After this, the
            # Published version is 1 and the Draft version is 4.
            for version_num in range(2, 5):
                publishing_api.create_publishable_entity_version(
                    entity_with_changes.id,
                    version_num=version_num,
                    title=f"I'm entity_with_changes v{version_num}",
                    created=self.now,
                    created_by=None,
                )

            # Soft-delete entity_with_pending_delete. After this, the Published
            # version is 1 and the Draft version is None.
            publishing_api.soft_delete_draft(entity_with_pending_delete.id)

            # Create a new entity that only exists in Draft form (no Published
            # version).
            new_entity = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "new_entity",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                new_entity.id,
                version_num=1,
                title="I'm new_entity v1",
                created=self.now,
                created_by=None,
            )

            # The versions we expect in (entity, published version_num, draft
            # version_num) tuples.
            expected_pre_reset_state = [
                (entity_with_changes, 1, 4),
                (entity_with_no_changes, 1, 1),
                (entity_with_pending_delete, 1, None),
                (new_entity, None, 1),
            ]
            for entity, pub_version_num, draft_version_num in expected_pre_reset_state:
                # Make sure we grab a new copy from the database so we're not
                # getting stale cached values:
                assert pub_version_num == self._get_published_version_num(entity)
                assert draft_version_num == self._get_draft_version_num(entity)

            # Now reset to draft here!
            publishing_api.reset_drafts_to_published(self.learning_package_1.id)

        # Versions we expect after reset in (entity, published version num)
        # tuples. The only entity that is not version 1 is the new one.
        expected_post_reset_state = [
            (entity_with_changes, 1),
            (entity_with_no_changes, 1),
            (entity_with_pending_delete, 1),
            (new_entity, None),
        ]
        for entity, pub_version_num in expected_post_reset_state:
            assert (
                self._get_published_version_num(entity) ==
                self._get_draft_version_num(entity) ==
                pub_version_num
            )

        if bulk:
            # The second half of this test has no net effect because it just resets to the published version
            # The DraftChangeLog (changes2) was created and then deleted.
            assert changes2.pk is None  # type: ignore[union-attr]
            assert DraftChangeLog.objects.count() == 1

    def test_get_entities_with_unpublished_changes(self) -> None:
        """Test fetching entities with unpublished changes after soft deletes."""
        entity = publishing_api.create_publishable_entity(
            self.learning_package_1.id,
            "my_entity",
            created=self.now,
            created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            entity.id,
            version_num=1,
            title="An Entity 🌴",
            created=self.now,
            created_by=None,
        )

        # Fetch unpublished entities
        entities = publishing_api.get_entities_with_unpublished_changes(self.learning_package_1.id)
        records = list(entities.all())
        assert len(records) == 1
        record = records[0]
        assert record.id == entity.id

        # Initial publish
        publishing_api.publish_all_drafts(self.learning_package_1.id)

        # soft-delete entity
        publishing_api.soft_delete_draft(entity.id)
        entities = publishing_api.get_entities_with_unpublished_changes(self.learning_package_1.id)
        assert len(entities) == 0
        entities = publishing_api.get_entities_with_unpublished_changes(self.learning_package_1.id,
                                                                        include_deleted_drafts=True)
        assert len(entities) == 1

        # publish soft-delete
        publishing_api.publish_all_drafts(self.learning_package_1.id)
        entities = publishing_api.get_entities_with_unpublished_changes(self.learning_package_1.id,
                                                                        include_deleted_drafts=True)
        # should not return published soft-deleted entities.
        assert len(entities) == 0

    def test_filter_publishable_entities(self) -> None:
        count_published = 7
        count_drafts = 6
        count_no_drafts = 3

        for index in range(count_published):
            # Create entities to publish
            entity = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                f"entity_published_{index}",
                created=self.now,
                created_by=None,
            )

            publishing_api.create_publishable_entity_version(
                entity.id,
                version_num=1,
                title=f"Entity_published_{index}",
                created=self.now,
                created_by=None,
            )

        publishing_api.publish_all_drafts(self.learning_package_1.id)

        for index in range(count_drafts):
            # Create entities with drafts
            entity = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                f"entity_draft_{index}",
                created=self.now,
                created_by=None,
            )

            publishing_api.create_publishable_entity_version(
                entity.id,
                version_num=1,
                title=f"Entity_draft_{index}",
                created=self.now,
                created_by=None,
            )

        for index in range(count_no_drafts):
            # Create entities without drafts
            entity = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                f"entity_no_draft_{index}",
                created=self.now,
                created_by=None,
            )

        drafts = publishing_api.filter_publishable_entities(
            PublishableEntity.objects.all(),
            has_draft=True,
        )
        assert drafts.count() == (count_published + count_drafts)

        no_drafts = publishing_api.filter_publishable_entities(
            PublishableEntity.objects.all(),
            has_draft=False,
        )
        assert no_drafts.count() == count_no_drafts

        published = publishing_api.filter_publishable_entities(
            PublishableEntity.objects.all(),
            has_published=True,
        )
        assert published.count() == count_published

        no_published = publishing_api.filter_publishable_entities(
            PublishableEntity.objects.all(),
            has_published=False,
        )
        assert no_published.count() == (count_drafts + count_no_drafts)

    def _get_published_version_num(self, entity: PublishableEntity) -> int | None:
        published_version = publishing_api.get_published_version(entity.id)
        if published_version is not None:
            return published_version.version_num
        return None

    def _get_draft_version_num(self, entity: PublishableEntity) -> int | None:
        draft_version = publishing_api.get_draft_version(entity.id)
        if draft_version is not None:
            return draft_version.version_num
        return None


class DraftChangeLogTestCase(TestCase):
    """
    Test basic operations with DraftChangeLogs and bulk draft operations.
    """
    now: datetime
    learning_package_1: LearningPackage
    learning_package_2: LearningPackage

    @classmethod
    def setUpTestData(cls) -> None:
        cls.now = datetime(2024, 1, 28, 16, 45, 30, tzinfo=timezone.utc)
        cls.learning_package_1 = publishing_api.create_learning_package(
            "my_package_key_1",
            "Draft Testing LearningPackage 🔥 1",
            created=cls.now,
        )
        cls.learning_package_2 = publishing_api.create_learning_package(
            "my_package_key_2",
            "Draft Testing LearningPackage 🔥 2",
            created=cls.now,
        )

    def test_simple_draft_change_log(self) -> None:
        """
        Simplest test that multiple writes make it into one DraftChangeLog.
        """
        with publishing_api.bulk_draft_changes_for(self.learning_package_1.id):
            entity = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "my_entity",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                entity.id,
                version_num=1,
                title="An Entity 🌴",
                created=self.now,
                created_by=None,
            )
            entity2 = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "my_entity2",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                entity2.id,
                version_num=1,
                title="An Entity 🌴 2",
                created=self.now,
                created_by=None,
            )
        draft_sets = list(DraftChangeLog.objects.all())
        assert len(draft_sets) == 1
        assert len(draft_sets[0].records.all()) == 2

        # Now that we're outside of the context manager, check that we're making
        # a new DraftChangeLog...
        entity3 = publishing_api.create_publishable_entity(
            self.learning_package_1.id,
            "my_entity3",
            created=self.now,
            created_by=None,
        )
        e3_v1 = publishing_api.create_publishable_entity_version(
            entity3.id,
            version_num=1,
            title="An Entity 🌴 3",
            created=self.now,
            created_by=None,
        )
        draft_sets = list(DraftChangeLog.objects.all().order_by('id'))
        assert len(draft_sets) == 2
        assert len(draft_sets[1].records.all()) == 1

        # Now make one entirely redundant change, and make sure it didn't create
        # anything (setting a draft to the same version it already was should be
        # a no-op).
        publishing_api.set_draft_version(entity3.id, e3_v1.pk)
        draft_sets = list(DraftChangeLog.objects.all().order_by('id'))
        assert len(draft_sets) == 2
        assert len(draft_sets[1].records.all()) == 1

    def test_nested_draft_changesets(self) -> None:
        """
        We should look up the stack to find the right one for our Learning Package.
        """
        with publishing_api.bulk_draft_changes_for(self.learning_package_1.id) as dcl_1:
            lp1_e1 = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "lp1_e1",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                lp1_e1.id,
                version_num=1,
                title="LP1 E1 v1",
                created=self.now,
                created_by=None,
            )
            with publishing_api.bulk_draft_changes_for(self.learning_package_2.id) as dcl_2:
                # This should make its way into the *outer* context, because
                # we're creating the new publishable entity version for
                # learning_package_1, not learning_package_2
                lp1_e1_v2 = publishing_api.create_publishable_entity_version(
                    lp1_e1.id,
                    version_num=2,
                    title="LP1 E1 v1",
                    created=self.now,
                    created_by=None,
                )

                # Make sure our change above made it to the outer context and
                # didn't make a new one (or go to the inner context).
                assert DraftChangeLog.objects.all().count() == 2
                assert DraftChangeLogRecord.objects.all().count() == 1
                lp1_e1_record = DraftChangeLogRecord.objects.first()
                assert lp1_e1_record is not None
                assert lp1_e1_record.old_version is None
                assert lp1_e1_record.new_version == lp1_e1_v2
                assert lp1_e1_record.draft_change_log.learning_package == self.learning_package_1

                # This will go to the inner context:
                lp2_e1 = publishing_api.create_publishable_entity(
                    self.learning_package_2.id,
                    "lp2_e1",
                    created=self.now,
                    created_by=None,
                )
                lp2_e1_v1 = publishing_api.create_publishable_entity_version(
                    lp2_e1.id,
                    version_num=1,
                    title="LP2 E1 v1",
                    created=self.now,
                    created_by=None,
                )
            # This doesn't error, but it creates a new DraftChangeLog instead of
            # re-using dcl_2
            lp2_e1_v2 = publishing_api.create_publishable_entity_version(
                lp2_e1.id,
                version_num=2,
                title="LP2 E1 v2",
                created=self.now,
                created_by=None,
            )

        # Sanity check that the first/outer DraftChangeLog hasn't changed.
        assert dcl_1.records.count() == 1

        # Check the state of the second/inner DraftChangeLog
        assert dcl_2.records.count() == 1
        lp2_e1_record = dcl_2.records.get(entity=lp2_e1)
        assert lp2_e1_record.old_version is None
        assert lp2_e1_record.new_version == lp2_e1_v1

        # We should have 3 DraftChangeLogs, because the last change to lp2_e1
        # was done outside of any context for Learning Package 2. Instead of
        # using Learning Package 1's context, it should create its own
        # DraftChangeLog:
        assert DraftChangeLog.objects.count() == 3
        implicit_dcl = DraftChangeLog.objects.order_by('id').last()
        assert implicit_dcl is not None
        assert implicit_dcl.records.count() == 1
        implicit_lp2_e1_record = implicit_dcl.records.get(entity=lp2_e1)
        assert implicit_lp2_e1_record.old_version == lp2_e1_v1
        assert implicit_lp2_e1_record.new_version == lp2_e1_v2

    def test_multiple_draft_changes(self) -> None:
        """
        Test that multiple changes to the same entity are collapsed.
        """
        with publishing_api.bulk_draft_changes_for(self.learning_package_1.id):
            entity = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "my_entity",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                entity.id,
                version_num=1,
                title="An Entity 🌴 v1",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                entity.id,
                version_num=2,
                title="An Entity 🌴 v2",
                created=self.now,
                created_by=None,
            )
        draft_sets = list(DraftChangeLog.objects.all().order_by('id'))
        assert len(draft_sets) == 1
        changes = list(draft_sets[0].records.all())
        assert len(changes) == 1
        change = changes[0]
        assert change.old_version is None
        assert change.new_version is not None
        assert change.new_version.version_num == 2

    def test_some_draft_changes_cancel_out(self) -> None:
        """Test that re remove redundant changes from our DraftChangeLog."""
        with publishing_api.bulk_draft_changes_for(self.learning_package_1.id):
            # This change will get cancelled out (because we create a draft and
            # then delete it), so changes related to entity_1 will be removed
            # after the context ends.
            entity_1 = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "Entity-1",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                entity_1.id,
                version_num=1,
                title="An Entity 🌴 v1",
                created=self.now,
                created_by=None,
            )
            publishing_api.soft_delete_draft(entity_1.id)

            # The change to entity_2 will persist
            entity_2 = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "Entity-2",
                created=self.now,
                created_by=None,
            )
            e2_v1 = publishing_api.create_publishable_entity_version(
                entity_2.id,
                version_num=1,
                title="E2 title",
                created=self.now,
                created_by=None,
            )
        assert DraftChangeLog.objects.all().count() == 1
        change_log = DraftChangeLog.objects.first()
        assert change_log is not None
        assert change_log.records.count() == 1
        change = change_log.records.get(entity_id=entity_2.id)
        assert change.old_version is None
        assert change.new_version == e2_v1

    def test_multiple_draft_changes_all_cancel_out(self) -> None:
        """
        If all changes made cancel out, the entire DraftRecord gets deleted.
        """
        # Make sure a version change from (None -> None) gets removed.
        with publishing_api.bulk_draft_changes_for(self.learning_package_1.id):
            entity = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "my_entity",
                created=self.now,
                created_by=None,
            )
            v1 = publishing_api.create_publishable_entity_version(
                entity.id,
                version_num=1,
                title="An Entity 🌴 v1",
                created=self.now,
                created_by=None,
            )
            publishing_api.soft_delete_draft(entity.id)

        assert not DraftChangeLog.objects.all().exists()

        # This next call implicitly makes a DraftChangeLog
        publishing_api.set_draft_version(entity.id, v1.pk)
        assert DraftChangeLog.objects.all().count() == 1

        # Make sure a change from v1 -> v2 -> v1 gets removed.
        with publishing_api.bulk_draft_changes_for(self.learning_package_1.id):
            for i in range(2, 5):
                # Make a few new versions
                publishing_api.create_publishable_entity_version(
                    entity.id,
                    version_num=i,
                    title=f"An Entity v{i}",
                    created=self.now,
                    created_by=None,
                )
            # Reset to version 1
            publishing_api.set_draft_version(entity.id, v1.pk)

        assert DraftChangeLog.objects.all().count() == 1


class PublishLogTestCase(TestCase):
    """
    Test basic operations with PublishLogs and PublishSideEffects.
    """
    now: datetime
    learning_package_1: LearningPackage
    learning_package_2: LearningPackage

    @classmethod
    def setUpTestData(cls) -> None:
        cls.now = datetime(2024, 1, 28, 16, 45, 30, tzinfo=timezone.utc)
        cls.learning_package_1 = publishing_api.create_learning_package(
            "my_package_key_1",
            "PublishLog Testing LearningPackage 🔥 1",
            created=cls.now,
        )
        cls.learning_package_2 = publishing_api.create_learning_package(
            "my_package_key_2",
            "PublishLog Testing LearningPackage 🔥 2",
            created=cls.now,
        )

    def test_simple_publish_log(self) -> None:
        """
        Simplest test that multiple writes make it into one PublishLog.
        """
        with publishing_api.bulk_draft_changes_for(self.learning_package_1.id):
            entity1 = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "my_entity",
                created=self.now,
                created_by=None,
            )
            entity1_v1 = publishing_api.create_publishable_entity_version(
                entity1.id,
                version_num=1,
                title="An Entity 🌴",
                created=self.now,
                created_by=None,
            )
            entity2 = publishing_api.create_publishable_entity(
                self.learning_package_1.id,
                "my_entity2",
                created=self.now,
                created_by=None,
            )
            entity2_v1 = publishing_api.create_publishable_entity_version(
                entity2.id,
                version_num=1,
                title="An Entity 🌴 2",
                created=self.now,
                created_by=None,
            )

        # Check simplest publish of two things...
        publish_log = publishing_api.publish_all_drafts(self.learning_package_1.id)
        assert PublishLog.objects.all().count() == 1
        assert publish_log.records.all().count() == 2

        record_1 = publish_log.records.get(entity=entity1)
        assert record_1 is not None
        assert record_1.old_version is None
        assert record_1.new_version == entity1_v1

        record_2 = publish_log.records.get(entity=entity2)
        assert record_2 is not None
        assert record_2.old_version is None
        assert record_2.new_version == entity2_v1

        # Check that an empty publish still creates a PublishLog, just one with
        # no records. This may be useful for triggering tasks that trigger off
        # of publishing events, though I'm not confident about that at the
        # moment.
        publish_log = publishing_api.publish_all_drafts(self.learning_package_1.id)
        assert publish_log.records.count() == 0

        # Check that we can publish a subset...
        entity1_v2 = publishing_api.create_publishable_entity_version(
            entity1.id,
            version_num=2,
            title="An Entity 🌴",
            created=self.now,
            created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            entity2.id,
            version_num=2,
            title="An Entity 🌴 2",
            created=self.now,
            created_by=None,
        )
        publish_e1_log = publishing_api.publish_from_drafts(
            self.learning_package_1.id,
            Draft.objects.filter(pk=entity1.id),
        )
        assert publish_e1_log.records.count() == 1
        e1_pub_record = publish_e1_log.records.get(entity=entity1)
        assert e1_pub_record is not None
        assert e1_pub_record.old_version == entity1_v1
        assert e1_pub_record.new_version == entity1_v2

    def test_publish_all_drafts_sets_direct_true(self) -> None:
        """publish_all_drafts() marks every PublishLogRecord as direct=True."""
        entity_1 = publishing_api.create_publishable_entity(
            self.learning_package_1.id, "direct_entity_1",
            created=self.now, created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            entity_1.id, version_num=1, title="Direct Entity 1",
            created=self.now, created_by=None,
        )
        entity_2 = publishing_api.create_publishable_entity(
            self.learning_package_1.id, "direct_entity_2",
            created=self.now, created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            entity_2.id, version_num=1, title="Direct Entity 2",
            created=self.now, created_by=None,
        )
        publish_log = publishing_api.publish_all_drafts(self.learning_package_1.id)
        assert publish_log.records.get(entity=entity_1).direct is True
        assert publish_log.records.get(entity=entity_2).direct is True

    def test_publish_from_drafts_sets_direct_true(self) -> None:
        """An explicitly selected entity in publish_from_drafts() gets direct=True."""
        entity = publishing_api.create_publishable_entity(
            self.learning_package_1.id, "explicit_entity",
            created=self.now, created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            entity.id, version_num=1, title="Explicit Entity",
            created=self.now, created_by=None,
        )
        publish_log = publishing_api.publish_from_drafts(
            self.learning_package_1.id,
            Draft.objects.filter(entity=entity),
        )
        assert publish_log.records.get(entity=entity).direct is True

    def test_publish_mix_of_new_and_already_published(self) -> None:
        """
        Publishing never-published and already-published entities together.

        The Published rows are written with a single upsert: a new row for the
        entity that has never been published, and an update of the version and
        log record for the one that has.
        """
        entity_1 = publishing_api.create_publishable_entity(
            self.learning_package_1.id, "entity_1", created=self.now, created_by=None,
        )
        entity_1_v1 = publishing_api.create_publishable_entity_version(
            entity_1.id, version_num=1, title="Entity 1 v1", created=self.now, created_by=None,
        )
        first_log = publishing_api.publish_all_drafts(self.learning_package_1.id)
        assert publishing_api.get_published_version(entity_1.id) == entity_1_v1

        # entity_1 gets a newer draft (its Published row must be updated) and
        # entity_2 is brand new (a Published row must be inserted):
        entity_1_v2 = publishing_api.create_publishable_entity_version(
            entity_1.id, version_num=2, title="Entity 1 v2", created=self.now, created_by=None,
        )
        entity_2 = publishing_api.create_publishable_entity(
            self.learning_package_1.id, "entity_2", created=self.now, created_by=None,
        )
        entity_2_v1 = publishing_api.create_publishable_entity_version(
            entity_2.id, version_num=1, title="Entity 2 v1", created=self.now, created_by=None,
        )

        with self.assertNumQueries(19):
            second_log = publishing_api.publish_all_drafts(self.learning_package_1.id)

        entities = PublishableEntity.objects.select_related("published__publish_log_record")
        published_1 = entities.get(id=entity_1.id).published
        assert published_1.version_id == entity_1_v2.id
        assert published_1.publish_log_record.publish_log_id == second_log.id
        assert published_1.publish_log_record.old_version_id == entity_1_v1.id
        published_2 = entities.get(id=entity_2.id).published
        assert published_2.version_id == entity_2_v1.id
        assert published_2.publish_log_record.publish_log_id == second_log.id
        assert published_2.publish_log_record.old_version_id is None
        # The first publish's record for entity_1 is untouched:
        assert PublishLogRecord.objects.get(publish_log=first_log, entity=entity_1).new_version_id == entity_1_v1.id

    def test_publish_log_record_direct_defaults_to_false(self) -> None:
        """
        New PublishLogRecords default to direct=False (not None).

        None is reserved for historical records that pre-date the direct field
        (set via the backfill data migration). Records created by the
        application—e.g. side-effect records in _create_side_effects_for_change_log()
        that don't explicitly set direct—should get False, not None.
        """
        field = PublishLogRecord._meta.get_field('direct')
        assert field.default is False


class EntitiesQueryTestCase(TestCase):
    """
    Tests for querying PublishableEntity objects.
    """
    now: datetime
    learning_package_1: LearningPackage

    @classmethod
    def setUpTestData(cls) -> None:
        """
        Initialize our content data
        """

        cls.now = datetime(2025, 8, 4, 12, 00, 00, tzinfo=timezone.utc)
        cls.learning_package_1 = publishing_api.create_learning_package(
            "my_package_key_1",
            "Entities Testing LearningPackage 🔥 1",
            created=cls.now,
        )

        with publishing_api.bulk_draft_changes_for(cls.learning_package_1.id):
            entity = publishing_api.create_publishable_entity(
                cls.learning_package_1.id,
                "my_entity",
                created=cls.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                entity.id,
                version_num=1,
                title="An Entity 🌴",
                created=cls.now,
                created_by=None,
            )
            entity2 = publishing_api.create_publishable_entity(
                cls.learning_package_1.id,
                "my_entity2",
                created=cls.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                entity2.id,
                version_num=1,
                title="An Entity 🌴 2",
                created=cls.now,
                created_by=None,
            )
        publishing_api.publish_all_drafts(cls.learning_package_1.id)

    def test_get_publishable_entities(self) -> None:
        """
        Test that get_entities returns all entities for a learning package.
        """
        entities = publishing_api.get_publishable_entities(self.learning_package_1.id)
        assert entities.count() == 2
        for entity in entities:
            assert isinstance(entity, PublishableEntity)
            assert entity.learning_package_id == self.learning_package_1.id
            assert entity.created == self.now

    def test_get_publishable_entities_n_plus_problem(self) -> None:
        """
        Check get_publishable_entities if N+1 query problem exists when accessing related entities.
        """
        entities = publishing_api.get_publishable_entities(self.learning_package_1.id)

        # assert that only 1 query is made even when accessing related entities
        with self.assertNumQueries(1):
            # Related entities to review:
            # - draft.version
            # - published.version

            for e in entities:
                # Instead of just checking the version number, we verify the related query count.
                # If an N+1 issue exists, accessing versions or other related fields would trigger more than one query.
                draft = getattr(e, 'draft', None)
                published = getattr(e, 'published', None)
                assert draft and draft.version.version_num == 1
                assert published and published.version.version_num == 1


class PublishingHistoryMixin:
    """
    Shared setup for history-related TestCases.

    Provides timestamps and a setUpTestData that creates a single
    LearningPackage and PublishableEntity reused across all tests in the class.
    """
    learning_package: LearningPackage
    entity: PublishableEntity

    time_1 = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    time_2 = datetime(2026, 6, 1, 11, 0, 0, tzinfo=timezone.utc)
    time_3 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    time_4 = datetime(2026, 6, 1, 13, 0, 0, tzinfo=timezone.utc)
    time_5 = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def setUpTestData(cls) -> None:
        """Create a shared LearningPackage and PublishableEntity for all tests in the class."""
        cls.learning_package = publishing_api.create_learning_package(
            "history_pkg",
            "History Test Package",
            created=cls.time_1,
        )
        cls.entity = publishing_api.create_publishable_entity(
            cls.learning_package.id,
            "test_entity",
            created=cls.time_1,
            created_by=None,
        )

    def _make_version(self, version_num: int, at: datetime, created_by=None):
        return publishing_api.create_publishable_entity_version(
            self.entity.id,
            version_num=version_num,
            title=f"v{version_num}",
            created=at,
            created_by=created_by,
        )

    def _publish(self, at: datetime) -> PublishLog:
        return publishing_api.publish_all_drafts(self.learning_package.id, published_at=at)


class GetEntityDraftHistoryTestCase(PublishingHistoryMixin, TestCase):
    """
    Tests for get_entity_draft_history.
    """
    # Publish timestamps sit strictly between draft-change timestamps
    publish_time_1 = datetime(2026, 6, 1, 10, 30, 0, tzinfo=timezone.utc)
    publish_time_2 = datetime(2026, 6, 1, 11, 30, 0, tzinfo=timezone.utc)

    def test_no_versions_never_published(self) -> None:
        """Returns empty queryset when the entity has no versions and has never been published."""
        history = publishing_api.get_entity_draft_history(self.entity.id)

        assert history.count() == 0

    def test_never_published(self) -> None:
        """Returns all draft records when the entity has never been published."""
        self._make_version(1, self.time_1)
        self._make_version(2, self.time_2)

        history = publishing_api.get_entity_draft_history(self.entity.id)

        assert history.count() == 2
        # most-recent-first ordering
        assert list(history.values_list("new_version__version_num", flat=True)) == [2, 1]

    def test_no_changes_since_publish(self) -> None:
        """Returns empty queryset when no draft changes have been made after the last publish."""
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)

        history = publishing_api.get_entity_draft_history(self.entity.id)

        assert history.count() == 0

    def test_changes_since_publish(self) -> None:
        """Returns only draft records made after the last publish, ordered most-recent-first."""
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)
        self._make_version(2, self.time_2)
        self._make_version(3, self.time_3)

        history = publishing_api.get_entity_draft_history(self.entity.id)

        assert history.count() == 2
        assert list(history.values_list("new_version__version_num", flat=True)) == [3, 2]

    def test_unpublished_soft_delete(self) -> None:
        """
        A soft-delete that is still pending (not yet published) is included in
        the draft history since the last real publish.
        """
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)
        publishing_api.set_draft_version(self.entity.id, None, set_at=self.time_2)

        history = publishing_api.get_entity_draft_history(self.entity.id)

        assert history.count() == 1
        record = history.get()
        assert record.new_version is None

    def test_after_published_soft_delete_no_new_changes(self) -> None:
        """
        When the last publish was a soft-delete (Published.version=None) and
        there are no subsequent draft changes, history is empty.
        """
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)
        publishing_api.set_draft_version(self.entity.id, None, set_at=self.time_2)
        self._publish(self.publish_time_2)  # publish the soft-delete

        history = publishing_api.get_entity_draft_history(self.entity.id)

        assert history.count() == 0

    def test_after_published_soft_delete_with_new_changes(self) -> None:
        """
        When the last publish was a soft-delete, only the draft changes made
        after that publish are returned (i.e. the post-delete edits).
        """
        version_1 = self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)
        publishing_api.set_draft_version(self.entity.id, None, set_at=self.time_2)
        self._publish(self.publish_time_2)  # publish the soft-delete
        # Restore: point draft back to v1 after the delete was published
        publishing_api.set_draft_version(self.entity.id, version_1.id, set_at=self.time_3)
        self._make_version(2, self.time_4)

        history = publishing_api.get_entity_draft_history(self.entity.id)

        assert history.count() == 2
        assert list(history.values_list("new_version__version_num", flat=True)) == [2, 1]

    def test_accepts_entity_or_int(self) -> None:
        """Works identically when called with a PublishableEntity or its int pk."""
        self._make_version(1, self.time_1)

        history_by_int = publishing_api.get_entity_draft_history(self.entity.id)
        history_by_entity = publishing_api.get_entity_draft_history(self.entity)

        assert list(history_by_int) == list(history_by_entity)

    def test_reset_to_published_clears_draft_history(self) -> None:
        """After reset_drafts_to_published, the draft history is empty."""
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)
        self._make_version(2, self.time_2)
        publishing_api.reset_drafts_to_published(
            self.learning_package.id, reset_at=self.time_3
        )

        history = publishing_api.get_entity_draft_history(self.entity.id)

        assert history.count() == 0

    def test_reset_to_published_then_new_changes(self) -> None:
        """After reset + new edits, only the post-reset changes appear."""
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)
        self._make_version(2, self.time_2)
        publishing_api.reset_drafts_to_published(
            self.learning_package.id, reset_at=self.time_3
        )
        self._make_version(3, self.time_4)

        history = publishing_api.get_entity_draft_history(self.entity.id)

        assert history.count() == 1
        record = history.get()
        assert record.new_version is not None
        assert record.new_version.version_num == 3

    def test_multiple_resets_use_latest(self) -> None:
        """When reset is called multiple times, the latest reset time is used as lower bound."""
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)
        self._make_version(2, self.time_2)
        publishing_api.reset_drafts_to_published(
            self.learning_package.id, reset_at=self.time_3
        )
        self._make_version(3, self.time_4)
        publishing_api.reset_drafts_to_published(
            self.learning_package.id, reset_at=self.time_5
        )

        history = publishing_api.get_entity_draft_history(self.entity.id)

        assert history.count() == 0


class GetEntityPublishHistoryTestCase(PublishingHistoryMixin, TestCase):
    """
    Tests for get_entity_publish_history.
    """
    publish_time_1 = datetime(2026, 6, 1, 10, 30, 0, tzinfo=timezone.utc)
    publish_time_2 = datetime(2026, 6, 1, 12, 30, 0, tzinfo=timezone.utc)
    publish_time_3 = datetime(2026, 6, 1, 14, 30, 0, tzinfo=timezone.utc)

    def test_never_published(self) -> None:
        """Returns empty queryset when the entity has never been published."""
        history = publishing_api.get_entity_publish_history(self.entity.id)

        assert history.count() == 0

    def test_single_publish(self) -> None:
        """Returns one record with correct old/new versions after the first publish."""
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)

        history = publishing_api.get_entity_publish_history(self.entity.id)

        assert history.count() == 1
        record = history.get()
        assert record.old_version is None
        assert record.new_version is not None
        assert record.new_version.version_num == 1

    def test_multiple_publishes_ordered_most_recent_first(self) -> None:
        """
        Returns one record per publish ordered most-recent-first, with the
        correct old/new versions. Multiple draft versions created between
        publishes are compacted: the record only captures the version that was
        actually published, not the intermediate ones.
        """
        # First publish: v1
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)

        # Create v2 and v3 between the first and second publish; only v3 lands in the record.
        self._make_version(2, self.time_2)
        self._make_version(3, self.time_3)
        self._publish(self.publish_time_2)

        # Create v4 and v5 before the third publish; only v5 lands in the record.
        self._make_version(4, self.time_4)
        self._make_version(5, self.time_5)
        self._publish(self.publish_time_3)

        history = list(publishing_api.get_entity_publish_history(self.entity.id))

        assert len(history) == 3
        # most recent publish: v3 -> v5
        assert history[0].old_version is not None
        assert history[0].new_version is not None
        assert history[0].old_version.version_num == 3
        assert history[0].new_version.version_num == 5
        # second publish: v1 -> v3
        assert history[1].old_version is not None
        assert history[1].new_version is not None
        assert history[1].old_version.version_num == 1
        assert history[1].new_version.version_num == 3
        # first publish: None -> v1
        assert history[2].old_version is None
        assert history[2].new_version is not None
        assert history[2].new_version.version_num == 1

    def test_soft_delete_publish(self) -> None:
        """
        Publishing a soft-delete produces a record with new_version=None,
        reflecting that the entity was removed from the published state.
        """
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)
        publishing_api.set_draft_version(self.entity.id, None, set_at=self.time_2)
        self._publish(self.publish_time_2)

        history = list(publishing_api.get_entity_publish_history(self.entity.id))

        assert len(history) == 2
        # most recent: the soft-delete publish
        assert history[0].old_version is not None
        assert history[0].old_version.version_num == 1
        assert history[0].new_version is None
        # original publish
        assert history[1].old_version is None
        assert history[1].new_version is not None
        assert history[1].new_version.version_num == 1

    def test_accepts_entity_or_int(self) -> None:
        """Works identically when called with a PublishableEntity or its int pk."""
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)

        history_by_int = publishing_api.get_entity_publish_history(self.entity.id)
        history_by_entity = publishing_api.get_entity_publish_history(self.entity)

        assert list(history_by_int) == list(history_by_entity)


class GetEntityVersionContributorsTestCase(PublishingHistoryMixin, TestCase):
    """
    Tests for get_entity_version_contributors.
    """
    user_1: Any
    user_2: Any
    user_3: Any

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        cls.user_1 = User.objects.create(username="contributor_1")
        cls.user_2 = User.objects.create(username="contributor_2")
        cls.user_3 = User.objects.create(username="contributor_3")

    def test_no_changes_in_range(self) -> None:
        """Returns empty queryset when no draft changes fall within the version range."""
        self._make_version(1, self.time_1, created_by=self.user_1.id)

        contributors = publishing_api.get_entity_version_contributors(
            self.entity.id, old_version_num=1, new_version_num=1
        )

        assert contributors.count() == 0

    def test_single_contributor(self) -> None:
        """Returns the user who made changes in the version range."""
        self._make_version(1, self.time_1)
        self._make_version(2, self.time_2, created_by=self.user_1.id)

        contributors = publishing_api.get_entity_version_contributors(
            self.entity.id, old_version_num=1, new_version_num=2
        )

        assert contributors.count() == 1
        assert contributors.get() == self.user_1

    def test_multiple_contributors_are_distinct(self) -> None:
        """Returns distinct users even if one user contributed multiple versions in the range."""
        self._make_version(1, self.time_1)
        self._make_version(2, self.time_2, created_by=self.user_1.id)
        self._make_version(3, self.time_3, created_by=self.user_2.id)
        self._make_version(4, self.time_4, created_by=self.user_1.id)  # user_1 again

        contributors = publishing_api.get_entity_version_contributors(
            self.entity.id, old_version_num=1, new_version_num=4
        )

        assert contributors.count() == 2
        assert set(contributors) == {self.user_1, self.user_2}

    def test_excludes_changes_outside_version_range(self) -> None:
        """Changes at or before old_version_num and after new_version_num are excluded."""
        self._make_version(1, self.time_1, created_by=self.user_1.id)  # at boundary, excluded
        self._make_version(2, self.time_2, created_by=self.user_2.id)  # inside range
        self._make_version(3, self.time_3, created_by=self.user_3.id)  # after range, excluded

        contributors = publishing_api.get_entity_version_contributors(
            self.entity.id, old_version_num=1, new_version_num=2
        )

        assert contributors.count() == 1
        assert contributors.get() == self.user_2

    def test_excludes_null_changed_by(self) -> None:
        """Changes with no associated user (changed_by=None) are never returned."""
        self._make_version(1, self.time_1, created_by=None)
        self._make_version(2, self.time_2, created_by=None)

        contributors = publishing_api.get_entity_version_contributors(
            self.entity.id, old_version_num=1, new_version_num=2
        )

        assert contributors.count() == 0

    def test_soft_delete_includes_edits_and_delete_record(self) -> None:
        """
        When new_version_num is None (soft-delete publish), both regular edits
        after old_version_num and the soft-delete record itself are included.
        """
        self._make_version(1, self.time_1)
        self._make_version(2, self.time_2, created_by=self.user_1.id)
        self._make_version(3, self.time_3, created_by=self.user_2.id)
        # Soft-delete from v3 by user_3
        publishing_api.set_draft_version(
            self.entity.id, None, set_at=self.time_4, set_by=self.user_3.id
        )

        contributors = publishing_api.get_entity_version_contributors(
            self.entity.id, old_version_num=1, new_version_num=None
        )

        assert set(contributors) == {self.user_1, self.user_2, self.user_3}

    def test_soft_delete_excludes_changes_before_range(self) -> None:
        """
        When new_version_num is None, changes at or before old_version_num
        are still excluded, including a soft-delete record whose old_version
        falls before the range.
        """
        self._make_version(1, self.time_1, created_by=self.user_1.id)
        # Soft-delete from v1 — old_version_num=1, so old_version(1) < 1 is false,
        # but old_version_num >= old_version_num means 1 >= 2 → excluded
        publishing_api.set_draft_version(
            self.entity.id, None, set_at=self.time_2, set_by=self.user_2.id
        )

        contributors = publishing_api.get_entity_version_contributors(
            self.entity.id, old_version_num=2, new_version_num=None
        )

        assert contributors.count() == 0

    def test_accepts_entity_or_int(self) -> None:
        """Works identically when called with a PublishableEntity or its int pk."""
        self._make_version(1, self.time_1, created_by=self.user_1.id)
        self._make_version(2, self.time_2, created_by=self.user_2.id)

        contributors_by_int = publishing_api.get_entity_version_contributors(
            self.entity.id, old_version_num=1, new_version_num=2
        )
        contributors_by_entity = publishing_api.get_entity_version_contributors(
            self.entity, old_version_num=1, new_version_num=2
        )

        assert list(contributors_by_int) == list(contributors_by_entity)

    def test_contributors_ordered_by_most_recent_contribution_first(self) -> None:
        """Contributors are returned most-recent-first based on their latest changed_at."""
        self._make_version(1, self.time_1)
        self._make_version(2, self.time_2, created_by=self.user_1.id)  # user_1 earlier
        self._make_version(3, self.time_3, created_by=self.user_2.id)  # user_2 latest

        contributors = list(publishing_api.get_entity_version_contributors(
            self.entity.id, old_version_num=1, new_version_num=3
        ))

        assert contributors == [self.user_2, self.user_1]

    def test_contributor_order_uses_latest_when_user_appears_multiple_times(self) -> None:
        """When one user contributes multiple times, their most recent edit determines their position."""
        self._make_version(1, self.time_1)
        self._make_version(2, self.time_2, created_by=self.user_2.id)  # user_2 first edit
        self._make_version(3, self.time_3, created_by=self.user_1.id)  # user_1 only edit
        self._make_version(4, self.time_4, created_by=self.user_2.id)  # user_2 latest edit → moves to front

        contributors = list(publishing_api.get_entity_version_contributors(
            self.entity.id, old_version_num=1, new_version_num=4
        ))

        assert contributors == [self.user_2, self.user_1]


class GetEntityPublishHistoryEntriesTestCase(PublishingHistoryMixin, TestCase):
    """
    Tests for get_entity_publish_history_entries.
    """
    publish_time_1 = datetime(2026, 6, 1, 10, 30, 0, tzinfo=timezone.utc)
    publish_time_2 = datetime(2026, 6, 1, 12, 30, 0, tzinfo=timezone.utc)
    publish_time_3 = datetime(2026, 6, 1, 13, 30, 0, tzinfo=timezone.utc)

    def test_returns_draft_changes_for_the_requested_publish_group(self) -> None:
        """
        Returns only the DraftChangeLogRecords that belong to the requested
        publish group (identified by its uuid), not those from other groups.
        """
        self._make_version(1, self.time_1)
        first_publish = self._publish(self.publish_time_1)
        self._make_version(2, self.time_2)
        self._make_version(3, self.time_3)
        second_publish = self._publish(self.publish_time_2)

        entries_first = publishing_api.get_entity_publish_history_entries(
            self.entity.id, str(first_publish.uuid)
        )
        entries_second = publishing_api.get_entity_publish_history_entries(
            self.entity.id, str(second_publish.uuid)
        )

        assert list(entries_first.values_list("new_version__version_num", flat=True)) == [1]
        assert list(entries_second.values_list("new_version__version_num", flat=True)) == [3, 2]

    def test_soft_delete_publish_includes_delete_record(self) -> None:
        """
        When the requested publish group was a soft-delete, the soft-delete
        DraftChangeLogRecord (new_version=None) is included in the entries.
        """
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)
        publishing_api.set_draft_version(self.entity.id, None, set_at=self.time_2)
        soft_delete_publish = self._publish(self.publish_time_2)

        entries = publishing_api.get_entity_publish_history_entries(
            self.entity.id, str(soft_delete_publish.uuid)
        )

        assert entries.count() == 1
        assert entries.get().new_version is None

    def test_raises_if_publish_log_uuid_not_found(self) -> None:
        """Raises PublishLogRecord.DoesNotExist for a uuid not associated with this entity."""
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)

        with pytest.raises(PublishLogRecord.DoesNotExist):
            publishing_api.get_entity_publish_history_entries(
                self.entity.id, "00000000-0000-0000-0000-000000000000"
            )

    def test_accepts_entity_or_int(self) -> None:
        """Works identically when called with a PublishableEntity or its int pk."""
        self._make_version(1, self.time_1)
        publish_log = self._publish(self.publish_time_1)

        entries_by_int = publishing_api.get_entity_publish_history_entries(
            self.entity.id, str(publish_log.uuid)
        )
        entries_by_entity = publishing_api.get_entity_publish_history_entries(
            self.entity, str(publish_log.uuid)
        )

        assert list(entries_by_int) == list(entries_by_entity)

    def test_reset_within_publish_window_excluded(self) -> None:
        """
        Draft entries from a reset_drafts_to_published() call within the publish
        window are excluded. Only entries made after the last reset appear.
        """
        self._make_version(1, self.time_1)
        self._publish(self.publish_time_1)
        self._make_version(2, self.time_2)
        publishing_api.reset_drafts_to_published(
            self.learning_package.id, reset_at=self.time_3
        )
        self._make_version(3, self.time_4)
        second_publish = self._publish(self.publish_time_3)

        entries = publishing_api.get_entity_publish_history_entries(
            self.entity.id, str(second_publish.uuid)
        )

        assert entries.count() == 1
        entry = entries.get()
        assert entry.new_version is not None
        assert entry.new_version.version_num == 3


class GetDescendantComponentEntityIdsTestCase(PublishingHistoryMixin, TestCase):
    """
    Tests for get_descendant_component_entity_ids.
    """

    def setUp(self) -> None:
        super().setUp()
        Container.reset_cache()
        # self.entity from PublishingHistoryMixin has no version — create one so
        # get_entities_in_container can access entity.draft without raising.
        publishing_api.create_publishable_entity_version(
            self.entity.id, version_num=1, title="Entity v1",
            created=self.time_1, created_by=None,
        )

    def _make_extra_entity(self, key: str) -> PublishableEntity:
        """Create an additional PublishableEntity with a v1 draft version."""
        entity = publishing_api.create_publishable_entity(
            self.learning_package.id, key, created=self.time_1, created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            entity.id, version_num=1, title=key,
            created=self.time_1, created_by=None,
        )
        return entity

    def _make_container(self, key: str, children: list) -> Container:
        """Create a Container with a v1 version pointing at the given children."""
        container: Container = containers_api.create_container(
            self.learning_package.id, key, created=self.time_1, created_by=None,
            container_cls=TestContainer,
        )
        containers_api.create_container_version(
            container.pk,
            1,
            title=key,
            entities=children,
            created=self.time_1,
            created_by=None,
        )
        return container

    def test_no_children_returns_empty(self) -> None:
        """A container with no children returns an empty list."""
        container = self._make_container("empty_container", children=[])
        result = containers_api.get_descendant_component_entity_ids(container)
        assert not result

    def test_direct_component_children(self) -> None:
        """Direct component children are returned."""
        second_component = self._make_extra_entity("second_component")
        unit = self._make_container("unit_direct", children=[self.entity, second_component])

        result = containers_api.get_descendant_component_entity_ids(unit)

        assert set(result) == {self.entity.pk, second_component.pk}

    def test_nested_returns_only_leaf_components(self) -> None:
        """
        Section → Subsection → Unit → Component hierarchy.
        Only the leaf component entity ID is returned; intermediate containers
        (subsection, unit) are excluded.
        """
        unit = self._make_container("unit_nested", children=[self.entity])
        subsection = self._make_container("subsection_nested", children=[unit])
        section = self._make_container("section_nested", children=[subsection])

        result = containers_api.get_descendant_component_entity_ids(section)

        assert set(result) == {self.entity.pk}
        assert unit.pk not in result
        assert subsection.pk not in result

    def test_multiple_components_across_sub_containers(self) -> None:
        """All leaf components across multiple sub-containers are collected."""
        second_component = self._make_extra_entity("second_component_multi")
        third_component = self._make_extra_entity("third_component_multi")
        first_unit = self._make_container("first_unit_multi", children=[self.entity, second_component])
        second_unit = self._make_container("second_unit_multi", children=[third_component])
        section = self._make_container("section_multi", children=[first_unit, second_unit])

        result = containers_api.get_descendant_component_entity_ids(section)

        assert set(result) == {self.entity.pk, second_component.pk, third_component.pk}

    def test_soft_deleted_sub_container_stops_traversal(self) -> None:
        """
        When a sub-container's draft is soft-deleted, the BFS skips it and its
        descendants are not included.
        """
        unit = self._make_container("unit_soft_deleted", children=[self.entity])
        section = self._make_container("section_with_deleted_unit", children=[unit])

        publishing_api.soft_delete_draft(unit.pk)

        result = containers_api.get_descendant_component_entity_ids(section)

        assert self.entity.pk not in result

    def test_container_without_version_returns_empty(self) -> None:
        """
        A container created with no ContainerVersion has no Draft.version,
        so the BFS returns nothing.
        """
        container: Container = containers_api.create_container(
            self.learning_package.id, "no_version_container",
            created=self.time_1, created_by=None,
            container_cls=TestContainer,
        )

        result = containers_api.get_descendant_component_entity_ids(container)

        assert not result


# TODO: refactor these tests to use a "fake" container model so there's no dependency on the containers applet?
# All we need is a similar generic publishableentity with dependencies.


class TestContainerSideEffects(TestCase):
    """
    Tests related to Container side effects and dependencies
    """
    now: datetime
    learning_package: LearningPackage

    def setUp(self) -> None:
        super().setUp()
        Container.reset_cache()

    @classmethod
    def setUpTestData(cls) -> None:
        cls.now = datetime(2024, 1, 28, 16, 45, 30, tzinfo=timezone.utc)
        cls.learning_package = publishing_api.create_learning_package(
            "containers_package_key",
            "Container Testing LearningPackage 🔥 1",
            created=cls.now,
        )

    def tearDown(self):
        Container.reset_cache()  # <- needed in tests involving Container subclasses
        return super().tearDown()

    def test_parent_child_side_effects(self) -> None:
        """Test that modifying a child has side-effects on its parent."""
        child_1 = publishing_api.create_publishable_entity(
            self.learning_package.id,
            "child_1",
            created=self.now,
            created_by=None,
        )
        child_1_v1 = publishing_api.create_publishable_entity_version(
            child_1.id,
            version_num=1,
            title="Child 1 🌴",
            created=self.now,
            created_by=None,
        )
        child_2 = publishing_api.create_publishable_entity(
            self.learning_package.id,
            "child_2",
            created=self.now,
            created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            child_2.id,
            version_num=1,
            title="Child 2 🌴",
            created=self.now,
            created_by=None,
        )
        container = containers_api.create_container(
            self.learning_package.id,
            "my_container",
            created=self.now,
            created_by=None,
            container_cls=TestContainer,
        )
        container_v1 = containers_api.create_container_version(
            container.id,
            1,
            title="My Container",
            entities=[
                child_1,
                child_2,
            ],
            created=self.now,
            created_by=None,
        )

        # All this was just set up. Now that we have our container-child
        # relationships, altering a child should add the parent container to
        # the DraftChangeLog.
        child_1_v2 = publishing_api.create_publishable_entity_version(
            child_1.id,
            version_num=2,
            title="Child 1 v2",
            created=self.now,
            created_by=None,
        )
        last_change_log = DraftChangeLog.objects.order_by("-id").first()
        assert last_change_log is not None
        assert last_change_log.records.count() == 2
        child_1_change = last_change_log.records.get(entity=child_1)
        assert child_1_change.old_version == child_1_v1
        assert child_1_change.new_version == child_1_v2

        # The container should be here, but the versions should be the same for
        # before and after:
        container_change = last_change_log.records.get(entity=container.publishable_entity)
        assert container_change.old_version == container_v1.publishable_entity_version
        assert container_change.new_version == container_v1.publishable_entity_version

        # Exactly one side-effect should have been created because we changed
        # child_1 after it was part of a container.
        side_effects = DraftSideEffect.objects.all()
        assert side_effects.count() == 1
        side_effect = side_effects.first()
        assert side_effect is not None
        assert side_effect.cause == child_1_change
        assert side_effect.effect == container_change

    def test_bulk_parent_child_side_effects(self) -> None:
        """Test that modifying a child has side-effects on its parent. (bulk version)"""
        with publishing_api.bulk_draft_changes_for(self.learning_package.id):
            child_1 = publishing_api.create_publishable_entity(
                self.learning_package.id,
                "child_1",
                created=self.now,
                created_by=None,
            )
            publishing_api.create_publishable_entity_version(
                child_1.id,
                version_num=1,
                title="Child 1 🌴",
                created=self.now,
                created_by=None,
            )
            child_2 = publishing_api.create_publishable_entity(
                self.learning_package.id,
                "child_2",
                created=self.now,
                created_by=None,
            )
            child_2_v1 = publishing_api.create_publishable_entity_version(
                child_2.id,
                version_num=1,
                title="Child 2 🌴",
                created=self.now,
                created_by=None,
            )
            container = containers_api.create_container(
                self.learning_package.id,
                "my_container",
                created=self.now,
                created_by=None,
                container_cls=TestContainer,
            )
            container_v1 = containers_api.create_container_version(
                container.id,
                1,
                title="My Container",
                entities=[child_1, child_2],
                created=self.now,
                created_by=None,
            )

            # All this was just set up. Now that we have our container-child
            # relationships, altering a child should add the parent container to
            # the DraftChangeLog.
            child_1_v2 = publishing_api.create_publishable_entity_version(
                child_1.id,
                version_num=2,
                title="Child 1 v2",
                created=self.now,
                created_by=None,
            )

        # Because we're doing it in bulk, there's only one DraftChangeLog entry.
        assert DraftChangeLog.objects.count() == 1
        last_change_log = DraftChangeLog.objects.first()
        assert last_change_log is not None
        # There's only ever one change entry per publishable entity
        assert last_change_log.records.count() == 3

        child_1_change = last_change_log.records.get(entity=child_1)
        assert child_1_change.old_version is None
        assert child_1_change.new_version == child_1_v2

        child_2_change = last_change_log.records.get(entity=child_2)
        assert child_2_change.old_version is None
        assert child_2_change.new_version == child_2_v1

        container_change = last_change_log.records.get(entity=container.publishable_entity)
        assert container_change.old_version is None
        assert container_change.new_version == container_v1.publishable_entity_version

        # There are two side effects here, because we grouped our draft edits
        # together using bulk_draft_changes_for, so changes to both children
        # count towards side-effects on the container.
        side_effects = DraftSideEffect.objects.all()
        assert side_effects.count() == 2
        caused_by_child_1 = side_effects.get(cause=child_1_change)
        caused_by_child_2 = side_effects.get(cause=child_2_change)
        assert caused_by_child_1.effect == container_change
        assert caused_by_child_2.effect == container_change

    def test_draft_dependency_multiple_parents(self) -> None:
        """
        Test that a change in a draft component affects multiple parents.

        This is the scenario where one Component is contained by multiple Units.
        """
        # Set up a Component that lives in two Units
        component = publishing_api.create_publishable_entity(
            self.learning_package.id,
            "component_1",
            created=self.now,
            created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            component.id,
            version_num=1,
            title="Component 1 🌴",
            created=self.now,
            created_by=None,
        )
        unit_1 = containers_api.create_container(
            self.learning_package.id,
            "unit_1",
            created=self.now,
            created_by=None,
            container_cls=TestContainer,
        )
        unit_2 = containers_api.create_container(
            self.learning_package.id,
            "unit_2",
            created=self.now,
            created_by=None,
            container_cls=TestContainer,
        )
        for unit in [unit_1, unit_2]:
            containers_api.create_container_version(
                unit.id,
                1,
                title="My Unit",
                entities=[component],
                created=self.now,
                created_by=None,
            )

        # At this point there should be no side effects because we created
        # everything from the bottom-up.
        assert not DraftSideEffect.objects.all().exists()

        # Now let's change the Component and make sure it created side-effects
        # for both Units.
        publishing_api.create_publishable_entity_version(
            component.id,
            version_num=2,
            title="Component 1.2 🌴",
            created=self.now,
            created_by=None,
        )
        side_effects = DraftSideEffect.objects.all()
        assert side_effects.count() == 2
        assert side_effects.filter(cause__entity=component).count() == 2
        assert side_effects.filter(effect__entity=unit_1.publishable_entity).count() == 1
        assert side_effects.filter(effect__entity=unit_2.publishable_entity).count() == 1

    def test_multiple_layers_of_containers(self) -> None:
        """Test stacking containers three layers deep."""
        # Note that these aren't real "components" and "units". Everything being
        # tested is confined to the publishing app, so those concepts shouldn't
        # be imported here. They're just named this way to make it more obvious
        # what the intended hierarchy is for testing container nesting.
        component = publishing_api.create_publishable_entity(
            self.learning_package.id,
            "component_1",
            created=self.now,
            created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            component.id,
            version_num=1,
            title="Component 1 🌴",
            created=self.now,
            created_by=None,
        )
        unit = containers_api.create_container(
            self.learning_package.id,
            "unit_1",
            created=self.now,
            created_by=None,
            container_cls=TestContainer,
        )
        containers_api.create_container_version(
            unit.id,
            1,
            title="My Unit",
            entities=[component],
            created=self.now,
            created_by=None,
        )
        subsection = containers_api.create_container(
            self.learning_package.id,
            "subsection_1",
            created=self.now,
            created_by=None,
            container_cls=TestContainer,
        )
        containers_api.create_container_version(
            subsection.id,
            1,
            title="My Subsection",
            entities=[unit],
            created=self.now,
            created_by=None,
        )

        # At this point, no side-effects exist yet because we built it from the
        # bottom-up using different DraftChangeLogs
        assert not DraftSideEffect.objects.all().exists()

        with publishing_api.bulk_draft_changes_for(self.learning_package.id) as change_log:
            publishing_api.create_publishable_entity_version(
                component.id,
                version_num=2,
                title="Component 1v2🌴",
                created=self.now,
                created_by=None,
            )

        assert DraftSideEffect.objects.count() == 2
        component_change = change_log.records.get(entity=component)
        unit_change = change_log.records.get(entity=unit.publishable_entity)
        subsection_change = change_log.records.get(entity=subsection.publishable_entity)

        assert not component_change.affected_by.exists()
        assert unit_change.affected_by.count() == 1
        assert unit_change.affected_by.first().cause == component_change
        assert subsection_change.affected_by.count() == 1
        assert subsection_change.affected_by.first().cause == unit_change

        publish_log = publishing_api.publish_all_drafts(self.learning_package.id)
        assert publish_log.records.count() == 3

        publishing_api.create_publishable_entity_version(
            component.id,
            version_num=3,
            title="Component v2",
            created=self.now,
            created_by=None,
        )
        publish_log = publishing_api.publish_from_drafts(
            self.learning_package.id,
            Draft.objects.filter(entity_id=component.id),
        )
        assert publish_log.records.count() == 3
        component_publish = publish_log.records.get(entity=component)
        unit_publish = publish_log.records.get(entity=unit.publishable_entity)
        subsection_publish = publish_log.records.get(entity=subsection.publishable_entity)

        assert not component_publish.affected_by.exists()
        assert unit_publish.affected_by.count() == 1
        assert unit_publish.affected_by.first().cause == component_publish  # type: ignore[union-attr]
        assert subsection_publish.affected_by.count() == 1
        assert subsection_publish.affected_by.first().cause == unit_publish  # type: ignore[union-attr]

    def test_publish_all_layers(self) -> None:
        """Test that we can publish multiple layers from one root."""
        # Note that these aren't real "components" and "units". Everything being
        # tested is confined to the publishing app, so those concepts shouldn't
        # be imported here. They're just named this way to make it more obvious
        # what the intended hierarchy is for testing container nesting.
        component = publishing_api.create_publishable_entity(
            self.learning_package.id,
            "component_1",
            created=self.now,
            created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            component.id,
            version_num=1,
            title="Component 1 🌴",
            created=self.now,
            created_by=None,
        )
        unit = containers_api.create_container(
            self.learning_package.id,
            "unit_1",
            created=self.now,
            created_by=None,
            container_cls=TestContainer,
        )
        containers_api.create_container_version(
            unit.id,
            1,
            title="My Unit",
            entities=[component],
            created=self.now,
            created_by=None,
        )
        subsection = containers_api.create_container(
            self.learning_package.id,
            "subsection_1",
            created=self.now,
            created_by=None,
            container_cls=TestContainer,
        )
        containers_api.create_container_version(
            subsection.id,
            1,
            title="My Subsection",
            entities=[unit],
            created=self.now,
            created_by=None,
        )
        publish_log = publishing_api.publish_from_drafts(
            self.learning_package.id,
            Draft.objects.filter(pk=subsection.id),
        )

        # The component, unit, and subsection should all be accounted for in
        # the publish log records.
        assert publish_log.records.count() == 3

    def test_direct_field_publishing_container_marks_dependencies_indirect(self) -> None:
        """
        Publishing a Unit explicitly marks the Unit as direct=True and its
        unpublished Component dependency as direct=False.
        """
        component = publishing_api.create_publishable_entity(
            self.learning_package.id, "direct_component",
            created=self.now, created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            component.id, version_num=1, title="Direct Component",
            created=self.now, created_by=None,
        )
        unit = containers_api.create_container(
            self.learning_package.id, "direct_unit",
            created=self.now, created_by=None, container_cls=TestContainer,
        )
        containers_api.create_container_version(
            unit.id, 1, title="Direct Unit", entities=[component],
            created=self.now, created_by=None,
        )
        publish_log = publishing_api.publish_from_drafts(
            self.learning_package.id,
            Draft.objects.filter(entity=unit.publishable_entity),
        )
        assert publish_log.records.get(entity=unit.publishable_entity).direct is True
        assert publish_log.records.get(entity=component).direct is False

    def test_direct_field_unit_no_version_change_still_direct_true(self) -> None:
        """
        Publishing a Unit that has no version change of its own (draft version
        == published version) still marks the Unit's record as direct=True.

        The user explicitly selected the Unit to publish, so it gets direct=True
        even though the only actual change is in its Component child. The Unit's
        record has old_version == new_version (pure side-effect in terms of
        versioning), but user intent was directed at the Unit.
        """
        component = publishing_api.create_publishable_entity(
            self.learning_package.id, "no_change_component",
            created=self.now, created_by=None,
        )
        component_v1 = publishing_api.create_publishable_entity_version(
            component.id, version_num=1, title="No-change Component",
            created=self.now, created_by=None,
        )
        unit = containers_api.create_container(
            self.learning_package.id, "no_change_unit",
            created=self.now, created_by=None, container_cls=TestContainer,
        )
        unit_v1 = containers_api.create_container_version(
            unit.id, 1, title="No-change Unit", entities=[component],
            created=self.now, created_by=None,
        )
        # Initial publish so both Unit and Component have a published version.
        publishing_api.publish_from_drafts(
            self.learning_package.id,
            Draft.objects.filter(entity=unit.publishable_entity),
        )

        # Create a new Component version. The Unit's draft stays at unit_v1,
        # but its dependencies_hash_digest now differs from the published state.
        publishing_api.create_publishable_entity_version(
            component.id, version_num=2, title="No-change Component v2",
            created=self.now, created_by=None,
        )

        # Publish the Unit explicitly. The Unit has no version change of its
        # own (old_version == new_version == unit_v1).
        publish_log = publishing_api.publish_from_drafts(
            self.learning_package.id,
            Draft.objects.filter(entity=unit.publishable_entity),
        )
        unit_record = publish_log.records.get(entity=unit.publishable_entity)
        component_record = publish_log.records.get(entity=component)

        # User selected the Unit → direct=True despite no version change.
        assert unit_record.direct is True
        assert unit_record.old_version_id == unit_v1.pk
        assert unit_record.new_version_id == unit_v1.pk

        # Component was pulled in as a dependency → direct=False.
        assert component_record.direct is False
        assert component_record.old_version == component_v1
        assert component_record.new_version != component_v1

    def test_direct_field_publishing_component_marks_parent_indirect(self) -> None:
        """
        Publishing a Component directly marks the Component as direct=True.
        The parent Unit also gets a PublishLogRecord (because it has an unpinned
        reference to the Component and its dependencies_hash_digest now differs
        from the published state) with direct=False.
        """
        component = publishing_api.create_publishable_entity(
            self.learning_package.id, "leaf_component",
            created=self.now, created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            component.id, version_num=1, title="Leaf Component",
            created=self.now, created_by=None,
        )
        unit = containers_api.create_container(
            self.learning_package.id, "leaf_unit",
            created=self.now, created_by=None, container_cls=TestContainer,
        )
        containers_api.create_container_version(
            unit.id, 1, title="Leaf Unit", entities=[component],
            created=self.now, created_by=None,
        )
        # First publish everything to establish a published baseline for the Unit
        publishing_api.publish_all_drafts(self.learning_package.id)

        # Create a new component version so it has unpublished changes
        publishing_api.create_publishable_entity_version(
            component.id, version_num=2, title="Leaf Component v2",
            created=self.now, created_by=None,
        )
        publish_log = publishing_api.publish_from_drafts(
            self.learning_package.id,
            Draft.objects.filter(entity=component),
        )
        assert publish_log.records.get(entity=component).direct is True
        assert publish_log.records.get(entity=unit.publishable_entity).direct is False

    def test_direct_field_both_selected_both_direct(self) -> None:
        """
        When both a Unit and its Component are explicitly selected, both
        get direct=True even though Component is also a dependency of Unit.
        """
        component = publishing_api.create_publishable_entity(
            self.learning_package.id, "both_component",
            created=self.now, created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            component.id, version_num=1, title="Both Component",
            created=self.now, created_by=None,
        )
        unit = containers_api.create_container(
            self.learning_package.id, "both_unit",
            created=self.now, created_by=None, container_cls=TestContainer,
        )
        containers_api.create_container_version(
            unit.id, 1, title="Both Unit", entities=[component],
            created=self.now, created_by=None,
        )
        publish_log = publishing_api.publish_from_drafts(
            self.learning_package.id,
            Draft.objects.filter(entity__in=[component, unit.publishable_entity]),
        )
        assert publish_log.records.get(entity=component).direct is True
        assert publish_log.records.get(entity=unit.publishable_entity).direct is True

    def test_container_next_version(self) -> None:
        """Test that next_version works for containers."""
        child_1 = publishing_api.create_publishable_entity(
            self.learning_package.id,
            "child_1",
            created=self.now,
            created_by=None,
        )
        container = containers_api.create_container(
            self.learning_package.id,
            "my_container",
            created=self.now,
            created_by=None,
            container_cls=TestContainer,
        )
        assert container.versioning.latest is None
        v1 = containers_api.create_next_container_version(
            container.id,
            title="My Container v1",
            entities=None,
            created=self.now,
            created_by=None,
        )
        assert v1.version_num == 1
        assert container.versioning.latest == v1
        v2 = containers_api.create_next_container_version(
            container.id,
            title="My Container v2",
            entities=[child_1],
            created=self.now,
            created_by=None,
        )
        assert v2.version_num == 2
        assert container.versioning.latest == v2
        assert v2.entity_list.entitylistrow_set.count() == 1
        v3 = containers_api.create_next_container_version(
            container.id,
            title="My Container v3",
            entities=None,
            created=self.now,
            created_by=None,
        )
        assert v3.version_num == 3
        assert container.versioning.latest == v3
        # Even though we didn't pass any rows, it should copy the previous version's rows
        assert v2.entity_list.entitylistrow_set.count() == 1


# Tests TODO:
# Test that I can get a [PublishLog] history of a given container and all its children, including children that aren't
#     currently in the container and excluding children that are only in other containers.
# Test that I can get a [PublishLog] history of a given container and its children, that includes changes made to the
#     child components while they were part of the container but excludes changes made to those children while they were
#     not part of the container. 🫣


class CrossEntityValidationTestCase(TransactionTestCase):
    """
    Tests for validation gaps where API calls can corrupt state by mixing
    entities/versions/packages that shouldn't be combined.
    """

    now: datetime
    learning_package_1: LearningPackage
    learning_package_2: LearningPackage

    def setUp(self):
        super().setUp()
        self.now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.learning_package_1 = publishing_api.create_learning_package(
            "cross_entity_validation_lp_1",
            "Cross-Entity Validation LP 1",
            created=self.now,
        )
        self.learning_package_2 = publishing_api.create_learning_package(
            "cross_entity_validation_lp_2",
            "Cross-Entity Validation LP 2",
            created=self.now,
        )

    def test_set_draft_version_rejects_version_from_different_entity(self) -> None:
        """
        set_draft_version() should reject a PublishableEntityVersion that
        belongs to a different PublishableEntity.

        If this validation is missing, entity_a's Draft will point to a version
        that was defined for entity_b. This corrupts the publishing state:
        component_a.versioning.draft would return component_b's data, and
        publishing would propagate the wrong content.
        """
        entity_a = publishing_api.create_publishable_entity(
            self.learning_package_1.id,
            "entity_a",
            created=self.now,
            created_by=None,
        )
        entity_b = publishing_api.create_publishable_entity(
            self.learning_package_1.id,
            "entity_b",
            created=self.now,
            created_by=None,
        )

        # Create v1 for entity_a (draft_a -> v1)
        publishing_api.create_publishable_entity_version(
            entity_a.id,
            version_num=1,
            title="Entity A v1",
            created=self.now,
            created_by=None,
        )

        # Create v1 and v2 for entity_b. After v2 is created, entity_b's
        # draft points to v2, so v1 is "free" (no Draft points to it) and
        # won't trigger a OneToOne constraint violation.
        version_b_v1 = publishing_api.create_publishable_entity_version(
            entity_b.id,
            version_num=1,
            title="Entity B v1",
            created=self.now,
            created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            entity_b.id,
            version_num=2,
            title="Entity B v2",
            created=self.now,
            created_by=None,
        )

        # Confirm version_b_v1 belongs to entity_b, not entity_a.
        assert version_b_v1.entity_id == entity_b.id
        assert version_b_v1.entity_id != entity_a.id

        # This should raise an error because version_b_v1 belongs to entity_b,
        # not entity_a. Without validation, this silently corrupts entity_a's
        # draft to point to entity_b's content.
        with pytest.raises(
            ValidationError,
            match=(
                r"Entity mismatch - the specified PublishableEntityVersion "
                r"\(<PublishableEntityVersion: entity_b @ v1 - Entity B v1>\) does not match the "
                r"PublishableEntity \(<PublishableEntity: entity_a>\)."
            ),
        ):
            publishing_api.set_draft_version(entity_a.id, version_b_v1.pk)

    def test_publish_from_drafts_rejects_cross_package_drafts(self) -> None:
        """
        publish_from_drafts() should reject drafts that don't belong to the
        specified LearningPackage.

        If this validation is missing, a PublishLog is created for LP 1 but
        with PublishLogRecords referencing entities from LP 2. The Published
        rows for LP 2's entities would point to records in LP 1's PublishLog,
        corrupting the publish history for both packages.
        """
        # Create an entity in LP 2
        entity_in_lp2 = publishing_api.create_publishable_entity(
            self.learning_package_2.id,
            "entity_in_lp2",
            created=self.now,
            created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            entity_in_lp2.id,
            version_num=1,
            title="Entity in LP2",
            created=self.now,
            created_by=None,
        )

        # Get drafts from LP 2
        drafts_from_lp2 = Draft.objects.filter(entity__learning_package_id=self.learning_package_2.id)
        assert drafts_from_lp2.exists()

        # This should raise an error because we're trying to publish LP 2's
        # drafts under LP 1's PublishLog.
        with pytest.raises(
            ValidationError,
            match=r"Draft entity \(id=\d+\) is from learning package \d+; expected learning package \d+.",
        ):
            publishing_api.publish_from_drafts(
                self.learning_package_1.id,
                drafts_from_lp2,
            )

    def test_create_version_rejects_cross_package_dependencies(self) -> None:
        """
        create_publishable_entity_version() should reject dependencies that
        are from a different LearningPackage.

        If this validation is missing, PublishableEntityVersionDependency rows
        are created linking entities across packages. The side-effect machinery
        would then propagate draft/publish changes across LearningPackage
        boundaries, creating DraftChangeLogRecords and PublishLogRecords in the
        wrong package's logs.
        """
        entity_in_lp1 = publishing_api.create_publishable_entity(
            self.learning_package_1.id,
            "entity_in_lp1",
            created=self.now,
            created_by=None,
        )
        entity_in_lp2 = publishing_api.create_publishable_entity(
            self.learning_package_2.id,
            "dep_entity_in_lp2",
            created=self.now,
            created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            entity_in_lp2.id,
            version_num=1,
            title="Dependency in LP2",
            created=self.now,
            created_by=None,
        )

        # This should raise an error because entity_in_lp2 is from a
        # different LearningPackage than entity_in_lp1.
        with pytest.raises(ValidationError, match="Dependencies must be from the same learning package"):
            publishing_api.create_publishable_entity_version(
                entity_in_lp1.id,
                version_num=1,
                title="Entity in LP1 with cross-package dep",
                created=self.now,
                created_by=None,
                dependencies=[entity_in_lp2.id],
            )

    def test_publish_functions_rejected_inside_bulk_draft_changes_for(self) -> None:
        """
        publish_all_drafts() and publish_from_drafts() must not be callable
        from within a bulk_draft_changes_for() context.

        bulk_draft_changes_for() opens a DraftChangeLog for accumulating draft
        edits; running a publish inside it mixes draft-change bookkeeping with
        publish bookkeeping in the same atomic block, which corrupts the
        ordering of DraftChangeLog vs. PublishLog records and can leave Drafts
        and Published rows out of sync if the outer context later raises.
        """
        lp1_id = self.learning_package_1.id
        entity = publishing_api.create_publishable_entity(
            self.learning_package_1.id,
            "entity_for_bulk_publish_check",
            created=self.now,
            created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            entity.id,
            version_num=1,
            title="Entity v1",
            created=self.now,
            created_by=None,
        )

        with pytest.raises(
            ValidationError,
            match=f"Cannot publish learning package {lp1_id} while in bulk_draft_changes_for()."
        ):
            with publishing_api.bulk_draft_changes_for(lp1_id):
                publishing_api.publish_all_drafts(lp1_id)

        with pytest.raises(
            ValidationError,
            match=f"Cannot publish learning package {lp1_id} while in bulk_draft_changes_for()."
        ):
            with publishing_api.bulk_draft_changes_for(lp1_id):
                publishing_api.publish_from_drafts(lp1_id, Draft.objects.filter(entity__learning_package_id=lp1_id))

        # But we CAN publish if the bulk_draft_changes_for is a different learning package:
        with publishing_api.bulk_draft_changes_for(self.learning_package_2.id):
            publishing_api.publish_all_drafts(lp1_id)
