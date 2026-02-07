"""
Tests of the Publishing app's python API
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from openedx_content.applets.publishing import api as publishing_api
from openedx_content.applets.publishing.models import (
    Container,
    ContainerVersion,
    Draft,
    DraftChangeLog,
    DraftChangeLogRecord,
    DraftSideEffect,
    LearningPackage,
    PublishableEntity,
    PublishLog,
)

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
            key=key,
            title=title,
            description=description,
            created=created
        )

        assert package.key == "my_key"
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
            key="new_key",
            title="new title",
            description="new description",
        )
        assert updated_package.key == "new_key"
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

        assert package.key == "my_key"
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
                key="my_key",
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
        assert "key" in message_dict


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

        # Publish!
        publishing_api.publish_all_drafts(self.learning_package_1.id)

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

    def test_reset_drafts_to_published_bulk(self) -> None:
        """bulk_draft_changes_for creates only one DraftChangeLog."""
        with publishing_api.bulk_draft_changes_for(self.learning_package_1.id):
            self.test_reset_drafts_to_published()
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
            Draft.objects.filter(pk=entity1.pk),
        )
        assert publish_e1_log.records.count() == 1
        e1_pub_record = publish_e1_log.records.get(entity=entity1)
        assert e1_pub_record is not None
        assert e1_pub_record.old_version == entity1_v1
        assert e1_pub_record.new_version == entity1_v2


class ContainerTestCase(TestCase):
    """
    Test basic operations with Drafts.
    """
    now: datetime
    learning_package: LearningPackage

    @classmethod
    def setUpTestData(cls) -> None:
        cls.now = datetime(2024, 1, 28, 16, 45, 30, tzinfo=timezone.utc)
        cls.learning_package = publishing_api.create_learning_package(
            "containers_package_key",
            "Container Testing LearningPackage 🔥 1",
            created=cls.now,
        )

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
        container: Container = publishing_api.create_container(
            self.learning_package.id,
            "my_container",
            created=self.now,
            created_by=None,
        )
        container_v1: ContainerVersion = publishing_api.create_container_version(
            container.pk,
            1,
            title="My Container",
            entity_rows=[
                publishing_api.ContainerEntityRow(entity_pk=child_1.pk),
                publishing_api.ContainerEntityRow(entity_pk=child_2.pk),
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
        last_change_log = DraftChangeLog.objects.order_by('-id').first()
        assert last_change_log is not None
        assert last_change_log.records.count() == 2
        child_1_change = last_change_log.records.get(entity=child_1)
        assert child_1_change.old_version == child_1_v1
        assert child_1_change.new_version == child_1_v2

        # The container should be here, but the versions should be the same for
        # before and after:
        container_change = last_change_log.records.get(
            entity=container.publishable_entity
        )
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
            container: Container = publishing_api.create_container(
                self.learning_package.id,
                "my_container",
                created=self.now,
                created_by=None,
            )
            container_v1: ContainerVersion = publishing_api.create_container_version(
                container.pk,
                1,
                title="My Container",
                entity_rows=[
                    publishing_api.ContainerEntityRow(entity_pk=child_1.pk),
                    publishing_api.ContainerEntityRow(entity_pk=child_2.pk),
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

        container_change = last_change_log.records.get(
            entity=container.publishable_entity
        )
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

    def test_draft_dependency_multiple_parents(self):
        """
        Test that a change in a draft component affects multiple parents.

        This is the scenario where one Component is contained by multiple Units.
        """
        # Set up a Component that lives in two Units
        component = publishing_api.create_publishable_entity(
            self.learning_package.id, "component_1", created=self.now, created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            component.id, version_num=1, title="Component 1 🌴", created=self.now, created_by=None,
        )
        unit_1 = publishing_api.create_container(
            self.learning_package.id, "unit_1", created=self.now, created_by=None,
        )
        unit_2 = publishing_api.create_container(
            self.learning_package.id, "unit_2", created=self.now, created_by=None,
        )
        for unit in [unit_1, unit_2]:
            publishing_api.create_container_version(
                unit.pk,
                1,
                title="My Unit",
                entity_rows=[
                    publishing_api.ContainerEntityRow(entity_pk=component.pk),
                ],
                created=self.now,
                created_by=None,
            )

        # At this point there should be no side effects because we created
        # everything from the bottom-up.
        assert not DraftSideEffect.objects.all().exists()

        # Now let's change the Component and make sure it created side-effects
        # for both Units.
        publishing_api.create_publishable_entity_version(
            component.id, version_num=2, title="Component 1.2 🌴", created=self.now, created_by=None,
        )
        side_effects = DraftSideEffect.objects.all()
        assert side_effects.count() == 2
        assert side_effects.filter(cause__entity=component).count() == 2
        assert side_effects.filter(effect__entity=unit_1.publishable_entity).count() == 1
        assert side_effects.filter(effect__entity=unit_2.publishable_entity).count() == 1

    def test_multiple_layers_of_containers(self):
        """Test stacking containers three layers deep."""
        # Note that these aren't real "components" and "units". Everything being
        # tested is confined to the publishing app, so those concepts shouldn't
        # be imported here. They're just named this way to make it more obvious
        # what the intended hierarchy is for testing container nesting.
        component = publishing_api.create_publishable_entity(
            self.learning_package.id, "component_1", created=self.now, created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            component.id, version_num=1, title="Component 1 🌴", created=self.now, created_by=None,
        )
        unit = publishing_api.create_container(
            self.learning_package.id, "unit_1", created=self.now, created_by=None,
        )
        publishing_api.create_container_version(
            unit.pk,
            1,
            title="My Unit",
            entity_rows=[
                publishing_api.ContainerEntityRow(entity_pk=component.pk),
            ],
            created=self.now,
            created_by=None,
        )
        subsection = publishing_api.create_container(
            self.learning_package.id, "subsection_1", created=self.now, created_by=None,
        )
        publishing_api.create_container_version(
            subsection.pk,
            1,
            title="My Subsection",
            entity_rows=[
                publishing_api.ContainerEntityRow(entity_pk=unit.pk),
            ],
            created=self.now,
            created_by=None,
        )

        # At this point, no side-effects exist yet because we built it from the
        # bottom-up using different DraftChangeLogs
        assert not DraftSideEffect.objects.all().exists()

        with publishing_api.bulk_draft_changes_for(self.learning_package.id) as change_log:
            publishing_api.create_publishable_entity_version(
                component.id, version_num=2, title="Component 1v2🌴", created=self.now, created_by=None,
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
            component.pk, version_num=3, title="Component v2", created=self.now, created_by=None,
        )
        publish_log = publishing_api.publish_from_drafts(
            self.learning_package.id,
            Draft.objects.filter(entity_id=component.pk),
        )
        assert publish_log.records.count() == 3
        component_publish = publish_log.records.get(entity=component)
        unit_publish = publish_log.records.get(entity=unit.publishable_entity)
        subsection_publish = publish_log.records.get(entity=subsection.publishable_entity)

        assert not component_publish.affected_by.exists()
        assert unit_publish.affected_by.count() == 1
        assert unit_publish.affected_by.first().cause == component_publish
        assert subsection_publish.affected_by.count() == 1
        assert subsection_publish.affected_by.first().cause == unit_publish

    def test_publish_all_layers(self):
        """Test that we can publish multiple layers from one root."""
        # Note that these aren't real "components" and "units". Everything being
        # tested is confined to the publishing app, so those concepts shouldn't
        # be imported here. They're just named this way to make it more obvious
        # what the intended hierarchy is for testing container nesting.
        component = publishing_api.create_publishable_entity(
            self.learning_package.id, "component_1", created=self.now, created_by=None,
        )
        publishing_api.create_publishable_entity_version(
            component.id, version_num=1, title="Component 1 🌴", created=self.now, created_by=None,
        )
        unit = publishing_api.create_container(
            self.learning_package.id, "unit_1", created=self.now, created_by=None,
        )
        publishing_api.create_container_version(
            unit.pk,
            1,
            title="My Unit",
            entity_rows=[
                publishing_api.ContainerEntityRow(entity_pk=component.pk),
            ],
            created=self.now,
            created_by=None,
        )
        subsection = publishing_api.create_container(
            self.learning_package.id, "subsection_1", created=self.now, created_by=None,
        )
        publishing_api.create_container_version(
            subsection.pk,
            1,
            title="My Subsection",
            entity_rows=[
                publishing_api.ContainerEntityRow(entity_pk=unit.pk),
            ],
            created=self.now,
            created_by=None,
        )
        publish_log = publishing_api.publish_from_drafts(
            self.learning_package.id,
            Draft.objects.filter(pk=subsection.pk),
        )

        # The component, unit, and subsection should all be accounted for in
        # the publish log records.
        assert publish_log.records.count() == 3

    def test_container_next_version(self):
        """Test that next_version works for containers."""
        child_1 = publishing_api.create_publishable_entity(
            self.learning_package.id,
            "child_1",
            created=self.now,
            created_by=None,
        )
        container = publishing_api.create_container(
            self.learning_package.id,
            "my_container",
            created=self.now,
            created_by=None,
        )
        assert container.versioning.latest is None
        v1 = publishing_api.create_next_container_version(
            container.pk,
            title="My Container v1",
            entity_rows=None,
            created=self.now,
            created_by=None,
        )
        assert v1.version_num == 1
        assert container.versioning.latest == v1
        v2 = publishing_api.create_next_container_version(
            container.pk,
            title="My Container v2",
            entity_rows=[
                publishing_api.ContainerEntityRow(entity_pk=child_1.pk)
            ],
            created=self.now,
            created_by=None,
        )
        assert v2.version_num == 2
        assert container.versioning.latest == v2
        assert v2.entity_list.entitylistrow_set.count() == 1
        v3 = publishing_api.create_next_container_version(
            container.pk,
            title="My Container v3",
            entity_rows=None,
            created=self.now,
            created_by=None,
        )
        assert v3.version_num == 3
        assert container.versioning.latest == v3
        # Even though we didn't pass any rows, it should copy the previous version's rows
        assert v2.entity_list.entitylistrow_set.count() == 1


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
