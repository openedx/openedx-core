"""
Basic tests for the subsections API.
"""
from unittest.mock import patch

import ddt  # type: ignore[import]
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

import openedx_content.api as content_api
from openedx_content import models_api as authoring_models

from ..units.test_api import UnitTestCase

Entry = content_api.SubsectionListEntry


# TODO: Turn UnitTestCase into UnitTestMixin and remove the
# test-inherits-tests pylint warning below.
# https://github.com/openedx/openedx-core/issues/308
@ddt.ddt
class SubSectionTestCase(UnitTestCase):  # pylint: disable=test-inherits-tests
    """ Test cases for Subsections (containers of units) """

    def setUp(self) -> None:
        super().setUp()
        self.unit_1, self.unit_1_v1 = self.create_unit(
            key="Unit (1)",
            title="Unit (1)",
        )
        self.unit_2, self.unit_2_v1 = self.create_unit(
            key="Unit (2)",
            title="Unit (2)",
        )

    def create_unit(self, *, title: str = "Test Unit", key: str = "unit:1") -> tuple[
        authoring_models.Unit, authoring_models.UnitVersion
    ]:
        """ Helper method to quickly create a unit """
        return content_api.create_unit_and_version(
            self.learning_package.id,
            key=key,
            title=title,
            created=self.now,
            created_by=None,
        )

    def create_subsection_with_units(
        self,
        units: list[authoring_models.Unit | authoring_models.UnitVersion],
        *,
        title="Unit",
        key="unit:key",
    ) -> authoring_models.Subsection:
        """ Helper method to quickly create a subsection with some units """
        subsection, _subsection_v1 = content_api.create_subsection_and_version(
            learning_package_id=self.learning_package.id,
            key=key,
            title=title,
            units=units,
            created=self.now,
            created_by=None,
        )
        return subsection

    def modify_unit(
        self,
        unit: authoring_models.Unit,
        *,
        title="Modified Unit",
        timestamp=None,
    ) -> authoring_models.UnitVersion:
        """
        Helper method to modify a unit for the purposes of testing units/drafts/pinning/publishing/etc.
        """
        return content_api.create_next_unit_version(
            unit,
            title=title,
            created=timestamp or self.now,
            created_by=None,
        )

    def publish_unit(self, unit: authoring_models.Unit):
        """
        Helper method to publish a single unit.
        """
        content_api.publish_from_drafts(
            self.learning_package.pk,
            draft_qset=content_api.get_all_drafts(self.learning_package.pk).filter(
                entity=unit.publishable_entity,
            ),
        )

    def test_get_subsection(self):
        """
        Test get_subsection()
        """
        subsection = self.create_subsection_with_units([self.unit_1, self.unit_2])
        with self.assertNumQueries(1):
            result = content_api.get_subsection(subsection.pk)
        assert result == subsection
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result.versioning.has_unpublished_changes

    def test_get_subsection_version(self):
        """
        Test get_subsection_version()
        """
        subsection = self.create_subsection_with_units([])
        draft = subsection.versioning.draft
        with self.assertNumQueries(1):
            result = content_api.get_subsection_version(draft.pk)
        assert result == draft

    def test_get_latest_subsection_version(self):
        """
        Test test_get_latest_subsection_version()
        """
        subsection = self.create_subsection_with_units([])
        draft = subsection.versioning.draft
        with self.assertNumQueries(2):
            result = content_api.get_latest_subsection_version(subsection.pk)
        assert result == draft

    def test_get_containers(self):
        """
        Test get_containers()
        """
        subsection = self.create_subsection_with_units([])
        with self.assertNumQueries(1):
            result = list(content_api.get_containers(self.learning_package.id))
        assert result == [self.unit_1.container, self.unit_2.container, subsection.container]
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result[0].versioning.has_unpublished_changes

    def test_get_containers_deleted(self):
        """
        Test that get_containers() does not return soft-deleted sections.
        """
        subsection = self.create_subsection_with_units([])
        content_api.soft_delete_draft(subsection.pk)
        with self.assertNumQueries(1):
            result = list(content_api.get_containers(self.learning_package.id, include_deleted=True))
        assert result == [self.unit_1.container, self.unit_2.container, subsection.container]

        with self.assertNumQueries(1):
            result = list(content_api.get_containers(self.learning_package.id))
        assert result == [self.unit_1.container, self.unit_2.container]

    def test_get_container(self):
        """
        Test get_container()
        """
        subsection = self.create_subsection_with_units([self.unit_1, self.unit_2])
        with self.assertNumQueries(1):
            result = content_api.get_container(subsection.pk)
        assert result == subsection.container
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result.versioning.has_unpublished_changes

    def test_get_container_by_key(self):
        """
        Test get_container_by_key()
        """
        subsection = self.create_subsection_with_units([])
        with self.assertNumQueries(1):
            result = content_api.get_container_by_key(
                self.learning_package.id,
                key=subsection.publishable_entity.key,
            )
        assert result == subsection.container
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result.versioning.has_unpublished_changes

    def test_subsection_container_versioning(self):
        """
        Test that the .versioning helper of a Sebsection returns a SubsectionVersion, and
        same for the generic Container equivalent.
        """
        subsection = self.create_subsection_with_units([self.unit_1, self.unit_2])
        container = subsection.container
        container_version = container.versioning.draft
        assert isinstance(container_version, authoring_models.ContainerVersion)
        subsection_version = subsection.versioning.draft
        assert isinstance(subsection_version, authoring_models.SubsectionVersion)
        assert subsection_version.container_version == container_version
        assert subsection_version.container_version.container == container
        assert subsection_version.subsection == subsection

    def test_create_subsection_queries(self):
        """
        Test how many database queries are required to create a subsection
        """
        # The exact numbers here aren't too important - this is just to alert us if anything significant changes.
        with self.assertNumQueries(28):
            _empty_subsection = self.create_subsection_with_units([])
        with self.assertNumQueries(35):
            # And try with a non-empty subsection:
            self.create_subsection_with_units([self.unit_1, self.unit_2_v1], key="u2")

    def test_create_subsection_with_invalid_children(self):
        """
        Verify that only units can be added to subsections, and a specific
        exception is raised.
        """
        # Create two subsections:
        subsection, subsection_version = content_api.create_subsection_and_version(
            learning_package_id=self.learning_package.id,
            key="subsection:key",
            title="Subsection",
            created=self.now,
            created_by=None,
        )
        assert subsection.versioning.draft == subsection_version
        subsection2, _s2v1 = content_api.create_subsection_and_version(
            learning_package_id=self.learning_package.id,
            key="subsection:key2",
            title="Subsection 2",
            created=self.now,
            created_by=None,
        )
        # Try adding a Subsection to a Subsection
        with pytest.raises(TypeError, match="Subsection units must be either Unit or UnitVersion."):
            content_api.create_next_subsection_version(
                subsection=subsection,
                title="Subsection Containing a Subsection",
                units=[subsection2],
                created=self.now,
                created_by=None,
            )
        # Check that a new version was not created:
        subsection.refresh_from_db()
        assert content_api.get_subsection(subsection.pk).versioning.draft == subsection_version
        assert subsection.versioning.draft == subsection_version

    def test_adding_external_units(self):
        """
        Test that units from another learning package cannot be added to a
        subsection.
        """
        learning_package2 = content_api.create_learning_package(key="other-package", title="Other Package")
        subsection, _subsection_version = content_api.create_subsection_and_version(
            learning_package_id=learning_package2.pk,
            key="subsection:key",
            title="Subsection",
            created=self.now,
            created_by=None,
        )
        assert self.unit_1.container.publishable_entity.learning_package != learning_package2
        # Try adding a a unit from LP 1 (self.learning_package) to a subsection from LP 2
        with pytest.raises(ValidationError, match="Container entities must be from the same learning package."):
            content_api.create_next_subsection_version(
                subsection=subsection,
                title="Subsection Containing an External Unit",
                units=[self.unit_1],
                created=self.now,
                created_by=None,
            )

    @patch('openedx_content.applets.subsections.api._pub_entities_for_units')
    def test_adding_mismatched_versions(self, mock_entities_for_units):  # pylint: disable=arguments-renamed
        """
        Test that versioned units must match their entities.
        """
        mock_entities_for_units.return_value = [
            content_api.ContainerEntityRow(
                entity_pk=self.unit_1.pk,
                version_pk=self.unit_2_v1.pk,
            ),
        ]
        # Try adding a a unit from LP 1 (self.learning_package) to a subsection from LP 2
        with pytest.raises(ValidationError, match="Container entity versions must belong to the specified entity"):
            content_api.create_subsection_and_version(
                learning_package_id=self.unit_1.container.publishable_entity.learning_package.pk,
                key="subsection:key",
                title="Subsection",
                units=[self.unit_1],
                created=self.now,
                created_by=None,
            )

    @ddt.data(True, False)
    @pytest.mark.skip(reason="FIXME: publishable_entity is not deleted from the database with the unit.")
    # FIXME: Also, exception is Container.DoesNotExist, not Unit.DoesNotExist
    def test_cannot_add_invalid_ids(self, pin_version):
        """
        Test that non-existent units cannot be added to subsections
        """
        self.unit_1.delete()
        if pin_version:
            units = [self.unit_1_v1]
        else:
            units = [self.unit_1]
        with pytest.raises((IntegrityError, authoring_models.Unit.DoesNotExist)):
            self.create_subsection_with_units(units)

    def test_create_empty_subsection_and_version(self):
        """Test creating a subsection with no units.

        Expected results:
        1. A subsection and subsection version are created.
        2. The subsection version number is 1.
        3. The subsection is a draft with unpublished changes.
        4. There is no published version of the subsection.
        """
        subsection, subsection_version = content_api.create_subsection_and_version(
            learning_package_id=self.learning_package.id,
            key="subsection:key",
            title="Subsection",
            created=self.now,
            created_by=None,
        )
        assert subsection, subsection_version
        assert subsection_version.version_num == 1
        assert subsection_version in subsection.versioning.versions.all()
        assert subsection.versioning.has_unpublished_changes
        assert subsection.versioning.draft == subsection_version
        assert subsection.versioning.published is None
        assert subsection.publishable_entity.can_stand_alone

    def test_create_next_subsection_version_with_two_unpinned_units(self):
        """Test creating a subsection version with two unpinned units.

        Expected results:
        1. A new subsection version is created.
        2. The subsection version number is 2.
        3. The subsection version is in the subsection's versions.
        4. The units are in the draft subsection version's unit list and are unpinned.
        """
        subsection, _subsection_version = content_api.create_subsection_and_version(
            learning_package_id=self.learning_package.id,
            key="subsection:key",
            title="Subsection",
            created=self.now,
            created_by=None,
        )
        subsection_version_v2 = content_api.create_next_subsection_version(
            subsection=subsection,
            title="Subsection",
            units=[self.unit_1, self.unit_2],
            created=self.now,
            created_by=None,
        )
        assert subsection_version_v2.version_num == 2
        assert subsection_version_v2 in subsection.versioning.versions.all()
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(self.unit_1.versioning.draft),
            Entry(self.unit_2.versioning.draft),
        ]
        with pytest.raises(authoring_models.ContainerVersion.DoesNotExist):
            # There is no published version of the subsection:
            content_api.get_units_in_subsection(subsection, published=True)

    def test_create_next_subsection_version_forcing_version_num(self):
        """
        Test creating a subsection version while forcing the next version number.
        """
        subsection, _subsection_version = content_api.create_subsection_and_version(
            learning_package_id=self.learning_package.id,
            key="subsection:key",
            title="Subsection",
            created=self.now,
            created_by=None,
        )
        subsection_version_v2 = content_api.create_next_subsection_version(
            subsection=subsection,
            title="Subsection",
            units=[self.unit_1, self.unit_2],
            created=self.now,
            created_by=None,
            force_version_num=4
        )
        assert subsection_version_v2.version_num == 4

    def test_create_next_subsection_version_with_unpinned_and_pinned_units(self):
        """
        Test creating a subsection version with one unpinned and one pinned 📌 unit.
        """
        subsection, _subsection_version = content_api.create_subsection_and_version(
            learning_package_id=self.learning_package.id,
            key="subsection:key",
            title="Subsection",
            created=self.now,
            created_by=None,
        )
        subsection_version_v2 = content_api.create_next_subsection_version(
            subsection=subsection,
            title="Subsection",
            units=[self.unit_1, self.unit_2_v1],  # Note the "v1" pinning 📌 the second one to version 1
            created=self.now,
            created_by=None,
        )
        assert subsection_version_v2.version_num == 2
        assert subsection_version_v2 in subsection.versioning.versions.all()
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(self.unit_1_v1),
            Entry(self.unit_2_v1, pinned=True),  # Pinned 📌 to v1
        ]
        with pytest.raises(authoring_models.ContainerVersion.DoesNotExist):
            # There is no published version of the subsection:
            content_api.get_units_in_subsection(subsection, published=True)

    def test_auto_publish_children(self):
        """
        Test that publishing a subsection publishes its child units automatically.
        """
        # Create a draft subsection with two draft units
        subsection = self.create_subsection_with_units([self.unit_1, self.unit_2])
        # Also create another unit that's not in the subsection at all:
        other_unit, _ou_v1 = self.create_unit(title="A draft unit not in the subsection", key="unit:3")

        assert content_api.contains_unpublished_changes(subsection.pk)
        assert self.unit_1.versioning.published is None
        assert self.unit_2.versioning.published is None

        # Publish ONLY the subsection. This should however also auto-publish units 1 & 2 since they're children
        content_api.publish_from_drafts(
            self.learning_package.pk,
            draft_qset=content_api.get_all_drafts(self.learning_package.pk).filter(
                entity=subsection.publishable_entity
            ),
        )
        # Now all changes to the subsection and to unit 1 are published:
        subsection.refresh_from_db()
        self.unit_1.refresh_from_db()
        assert subsection.versioning.has_unpublished_changes is False  # Shallow check
        assert self.unit_1.versioning.has_unpublished_changes is False
        assert content_api.contains_unpublished_changes(subsection.pk) is False  # Deep check
        assert self.unit_1.versioning.published == self.unit_1_v1  # v1 is now the published version.

        # But our other unit that's outside the subsection is not affected:
        other_unit.refresh_from_db()
        assert other_unit.versioning.has_unpublished_changes
        assert other_unit.versioning.published is None

    def test_no_publish_parent(self):
        """
        Test that publishing a unit does NOT publish changes to its parent subsection
        """
        # Create a draft subsection with two draft units
        subsection = self.create_subsection_with_units([self.unit_1, self.unit_2])
        assert subsection.versioning.has_unpublished_changes
        # Publish ONLY one of its child units
        self.publish_unit(self.unit_1)
        self.unit_1.refresh_from_db()  # Clear cache on '.versioning'
        assert self.unit_1.versioning.has_unpublished_changes is False

        # The subsection that contains that unit should still be unpublished:
        subsection.refresh_from_db()  # Clear cache on '.versioning'
        assert subsection.versioning.has_unpublished_changes
        assert subsection.versioning.published is None
        with pytest.raises(authoring_models.ContainerVersion.DoesNotExist):
            # There is no published version of the subsection:
            content_api.get_units_in_subsection(subsection, published=True)

    def test_add_unit_after_publish(self):
        """
        Adding a unit to a published subsection will create a new version and
        show that the subsection has unpublished changes.
        """
        subsection, subsection_version = content_api.create_subsection_and_version(
            learning_package_id=self.learning_package.id,
            key="subsection:key",
            title="Subsection",
            created=self.now,
            created_by=None,
        )
        assert subsection.versioning.draft == subsection_version
        assert subsection.versioning.published is None
        assert subsection.versioning.has_unpublished_changes
        # Publish the empty subsection:
        content_api.publish_all_drafts(self.learning_package.id)
        subsection.refresh_from_db()  # Reloading the subsection is necessary
        assert subsection.versioning.has_unpublished_changes is False  # Shallow check for subsection only, not children
        assert content_api.contains_unpublished_changes(subsection.pk) is False  # Deeper check

        # Add a published unit (unpinned):
        assert self.unit_1.versioning.has_unpublished_changes is False
        subsection_version_v2 = content_api.create_next_subsection_version(
            subsection=subsection,
            title=subsection_version.title,
            units=[self.unit_1],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        # Now the subsection should have unpublished changes:
        subsection.refresh_from_db()  # Reloading the subsection is necessary
        assert subsection.versioning.has_unpublished_changes  # Shallow check: adding a child changes the subsection
        assert content_api.contains_unpublished_changes(subsection.pk)  # Deeper check
        assert subsection.versioning.draft == subsection_version_v2
        assert subsection.versioning.published == subsection_version

    def test_modify_unpinned_unit_after_publish(self):
        """
        Modifying an unpinned unit in a published subsection will NOT create a
        new version nor show that the subsection has unpublished changes (but it will
        "contain" unpublished changes). The modifications will appear in the
        published version of the subsection only after the unit is published.
        """
        # Create a subsection with one unpinned draft unit:
        assert self.unit_1.versioning.has_unpublished_changes
        subsection = self.create_subsection_with_units([self.unit_1])
        assert subsection.versioning.has_unpublished_changes

        # Publish the subsection and the unit:
        content_api.publish_all_drafts(self.learning_package.id)
        subsection.refresh_from_db()  # Reloading the subsection is necessary if we accessed 'versioning' before publish
        self.unit_1.refresh_from_db()
        assert subsection.versioning.has_unpublished_changes is False  # Shallow check
        assert content_api.contains_unpublished_changes(subsection.pk) is False  # Deeper check
        assert self.unit_1.versioning.has_unpublished_changes is False

        # Now modify the unit by changing its title (it remains a draft):
        unit_1_v2 = self.modify_unit(self.unit_1, title="Modified Counting Problem with new title")

        # The unit now has unpublished changes; the subsection doesn't directly but does contain
        subsection.refresh_from_db()  # Refresh to avoid stale 'versioning' cache
        self.unit_1.refresh_from_db()
        assert subsection.versioning.has_unpublished_changes is False  # Shallow check: subsection unchanged
        assert content_api.contains_unpublished_changes(subsection.pk)  # But subsection DOES contain changes
        assert self.unit_1.versioning.has_unpublished_changes

        # Since the unit changes haven't been published, they should only appear in the draft subsection
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(unit_1_v2),  # new version
        ]
        assert content_api.get_units_in_subsection(subsection, published=True) == [
            Entry(self.unit_1_v1),  # old version
        ]

        # But if we publish the unit, the changes will appear in the published version of the subsection.
        self.publish_unit(self.unit_1)
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(unit_1_v2),  # new version
        ]
        assert content_api.get_units_in_subsection(subsection, published=True) == [
            Entry(unit_1_v2),  # new version
        ]
        assert content_api.contains_unpublished_changes(subsection.pk) is False  # No more unpublished changes

    def test_modify_pinned_unit(self):
        """
        When a pinned 📌 unit in subsection is modified and/or published, it will
        have no effect on either the draft nor published version of the subsection,
        which will continue to use the pinned version.
        """
        # Create a subsection with one unit (pinned 📌 to v1):
        subsection = self.create_subsection_with_units([self.unit_1_v1])

        # Publish the subsection and the unit:
        content_api.publish_all_drafts(self.learning_package.id)
        expected_subsection_contents = [
            Entry(self.unit_1_v1, pinned=True),  # pinned 📌 to v1
        ]
        assert content_api.get_units_in_subsection(subsection, published=True) == expected_subsection_contents

        # Now modify the unit by changing its title (it remains a draft):
        self.modify_unit(self.unit_1, title="Modified Counting Problem with new title")

        # The unit now has unpublished changes; the subsection is entirely unaffected
        subsection.refresh_from_db()  # Refresh to avoid stale 'versioning' cache
        self.unit_1.refresh_from_db()
        assert subsection.versioning.has_unpublished_changes is False  # Shallow check
        assert content_api.contains_unpublished_changes(subsection.pk) is False  # Deep check
        assert self.unit_1.versioning.has_unpublished_changes is True

        # Neither the draft nor the published version of the subsection is affected
        assert content_api.get_units_in_subsection(subsection, published=False) == expected_subsection_contents
        assert content_api.get_units_in_subsection(subsection, published=True) == expected_subsection_contents
        # Even if we publish the unit, the subsection stays pinned to the specified version:
        self.publish_unit(self.unit_1)
        assert content_api.get_units_in_subsection(subsection, published=False) == expected_subsection_contents
        assert content_api.get_units_in_subsection(subsection, published=True) == expected_subsection_contents

    def test_create_two_subsections_with_same_units(self):
        """
        Test creating two subsections with different combinations of the same two
        units in each subsection.
        """
        # Create a subsection with unit 2 unpinned, unit 2 pinned 📌, and unit 1:
        subsection1 = self.create_subsection_with_units([self.unit_2, self.unit_2_v1, self.unit_1], key="u1")
        # Create a second subsection with unit 1 pinned 📌, unit 2, and unit 1 unpinned:
        subsection2 = self.create_subsection_with_units([self.unit_1_v1, self.unit_2, self.unit_1], key="u2")

        # Check that the contents are as expected:
        assert [row.unit_version for row in content_api.get_units_in_subsection(subsection1, published=False)] == [
            self.unit_2_v1, self.unit_2_v1, self.unit_1_v1,
        ]
        assert [row.unit_version for row in content_api.get_units_in_subsection(subsection2, published=False)] == [
            self.unit_1_v1, self.unit_2_v1, self.unit_1_v1,
        ]

        # Modify unit 1
        unit_1_v2 = self.modify_unit(self.unit_1, title="unit 1 v2")
        # Publish changes
        content_api.publish_all_drafts(self.learning_package.id)
        # Modify unit 2 - only in the draft
        unit_2_v2 = self.modify_unit(self.unit_2, title="unit 2 DRAFT")

        # Check that the draft contents are as expected:
        assert content_api.get_units_in_subsection(subsection1, published=False) == [
            Entry(unit_2_v2),  # v2 in the draft version
            Entry(self.unit_2_v1, pinned=True),  # pinned 📌 to v1
            Entry(unit_1_v2),  # v2
        ]
        assert content_api.get_units_in_subsection(subsection2, published=False) == [
            Entry(self.unit_1_v1, pinned=True),  # pinned 📌 to v1
            Entry(unit_2_v2),  # v2 in the draft version
            Entry(unit_1_v2),  # v2
        ]

        # Check that the published contents are as expected:
        assert content_api.get_units_in_subsection(subsection1, published=True) == [
            Entry(self.unit_2_v1),  # v1 in the published version
            Entry(self.unit_2_v1, pinned=True),  # pinned 📌 to v1
            Entry(unit_1_v2),  # v2
        ]
        assert content_api.get_units_in_subsection(subsection2, published=True) == [
            Entry(self.unit_1_v1, pinned=True),  # pinned 📌 to v1
            Entry(self.unit_2_v1),  # v1 in the published version
            Entry(unit_1_v2),  # v2
        ]

    def test_publishing_shared_unit(self):
        """
        A complex test case involving two subsections with a shared unit and
        other non-shared units.

        Subsection 1: units C1, C2, C3
        Subsection 2: units C2, C4, C5
        Everything is "unpinned".
        """
        # 1️⃣ Create the subsections and publish them:
        (u1, u1_v1), (u2, _u2_v1), (u3, u3_v1), (u4, u4_v1), (u5, u5_v1) = [
            self.create_unit(key=f"C{i}", title=f"Unit {i}") for i in range(1, 6)
        ]
        subsection1 = self.create_subsection_with_units([u1, u2, u3], title="Subsection 1", key="subsection:1")
        subsection2 = self.create_subsection_with_units([u2, u4, u5], title="Subsection 2", key="subsection:2")
        content_api.publish_all_drafts(self.learning_package.id)
        assert content_api.contains_unpublished_changes(subsection1.pk) is False
        assert content_api.contains_unpublished_changes(subsection2.pk) is False

        # 2️⃣ Then the author edits U2 inside of Subsection 1 making U2v2.
        u2_v2 = self.modify_unit(u2, title="U2 version 2")
        # Both S1 and S2 now contain unpublished changes since they share the unit.
        assert content_api.contains_unpublished_changes(subsection1.pk)
        assert content_api.contains_unpublished_changes(subsection2.pk)
        # (But the subsections themselves are unchanged:)
        subsection1.refresh_from_db()
        subsection2.refresh_from_db()
        assert subsection1.versioning.has_unpublished_changes is False
        assert subsection2.versioning.has_unpublished_changes is False

        # 3️⃣ In addition to this, the author also modifies another unit in Subsection 2 (U5)
        u5_v2 = self.modify_unit(u5, title="U5 version 2")

        # 4️⃣ The author then publishes Subsection 1, and therefore everything in it.
        content_api.publish_from_drafts(
            self.learning_package.pk,
            draft_qset=content_api.get_all_drafts(self.learning_package.pk).filter(
                # Note: we only publish the subsection; the publishing API should auto-publish its units too.
                entity_id=subsection1.publishable_entity.id,
            ),
        )

        # Result: Subsection 1 will show the newly published version of U2:
        assert content_api.get_units_in_subsection(subsection1, published=True) == [
            Entry(u1_v1),
            Entry(u2_v2),  # new published version of U2
            Entry(u3_v1),
        ]

        # Result: someone looking at Subsection 2 should see the newly published unit 2, because publishing it anywhere
        # publishes it everywhere. But publishing U2 and Subsection 1 does not affect the other units in Subsection 2.
        # (Publish propagates downward, not upward)
        assert content_api.get_units_in_subsection(subsection2, published=True) == [
            Entry(u2_v2),  # new published version of U2
            Entry(u4_v1),  # still original version of U4 (it was never modified)
            Entry(u5_v1),  # still original version of U5 (it hasn't been published)
        ]

        # Result: Subsection 2 contains unpublished changes due to modified U5; Subsection 1 does not.
        assert content_api.contains_unpublished_changes(subsection1.pk) is False
        assert content_api.contains_unpublished_changes(subsection2.pk)

        # 5️⃣ Publish unit U5, which should be the only thing unpublished in the learning package
        self.publish_unit(u5)
        # Result: Subsection 2 shows the new version of C5 and no longer contains unpublished changes:
        assert content_api.get_units_in_subsection(subsection2, published=True) == [
            Entry(u2_v2),  # new published version of U2
            Entry(u4_v1),  # still original version of U4 (it was never modified)
            Entry(u5_v2),  # new published version of U5
        ]
        assert content_api.contains_unpublished_changes(subsection2.pk) is False

    def test_query_count_of_contains_unpublished_changes(self):
        """
        Checking for unpublished changes in a subsection should require a fixed number
        of queries, not get more expensive as the subsection gets larger.
        """
        # Add 2 units (unpinned)
        unit_count = 2
        units = []
        for i in range(unit_count):
            unit, _version = self.create_unit(
                key=f"Unit {i}",
                title=f"Unit {i}",
            )
            units.append(unit)
        subsection = self.create_subsection_with_units(units)
        content_api.publish_all_drafts(self.learning_package.id)
        subsection.refresh_from_db()
        with self.assertNumQueries(1):
            assert content_api.contains_unpublished_changes(subsection.pk) is False

        # Modify the most recently created unit:
        self.modify_unit(unit, title="Modified Unit")
        with self.assertNumQueries(1):
            assert content_api.contains_unpublished_changes(subsection.pk) is True

    def test_metadata_change_doesnt_create_entity_list(self):
        """
        Test that changing a container's metadata like title will create a new
        version, but can re-use the same EntityList. API consumers generally
        shouldn't depend on this behavior; it's an optimization.
        """
        subsection = self.create_subsection_with_units([self.unit_1, self.unit_2_v1])

        orig_version_num = subsection.versioning.draft.version_num
        orig_entity_list_id = subsection.versioning.draft.entity_list.pk

        content_api.create_next_subsection_version(subsection, title="New Title", created=self.now)

        subsection.refresh_from_db()
        new_version_num = subsection.versioning.draft.version_num
        new_entity_list_id = subsection.versioning.draft.entity_list.pk

        assert new_version_num > orig_version_num
        assert new_entity_list_id == orig_entity_list_id

    @ddt.data(True, False)
    @pytest.mark.skip(reason="FIXME: we don't yet prevent adding soft-deleted units to subsections")
    def test_cannot_add_soft_deleted_unit(self, publish_first):
        """
        Test that a soft-deleted unit cannot be added to a subsection.

        Although it's valid for subsections to contain soft-deleted units (by
        deleting the unit after adding it), it is likely a mistake if
        you're trying to add one to the subsection.
        """
        unit, _cv = self.create_unit(title="Deleted unit")
        if publish_first:
            # Publish the unit:
            content_api.publish_all_drafts(self.learning_package.id)
        # Now delete it. The draft version is now deleted:
        content_api.soft_delete_draft(unit.pk)
        # Now try adding that unit to a subsection:
        with pytest.raises(ValidationError, match="unit is deleted"):
            self.create_subsection_with_units([unit])

    def test_removing_unit(self):
        """ Test removing a unit from a subsection (but not deleting it) """
        subsection = self.create_subsection_with_units([self.unit_1, self.unit_2])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now remove unit 2
        content_api.create_next_subsection_version(
            subsection=subsection,
            title="Revised with unit 2 deleted",
            units=[self.unit_2],
            created=self.now,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )

        # Now it should not be listed in the subsection:
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(self.unit_1_v1),
        ]
        subsection.refresh_from_db()
        assert subsection.versioning.has_unpublished_changes  # The subsection itself and its unit list have change
        assert content_api.contains_unpublished_changes(subsection.pk)
        # The published version of the subsection is not yet affected:
        assert content_api.get_units_in_subsection(subsection, published=True) == [
            Entry(self.unit_1_v1),
            Entry(self.unit_2_v1),
        ]

        # But when we publish the new subsection version with the removal, the published version is affected:
        content_api.publish_all_drafts(self.learning_package.id)
        # FIXME: Refreshing the subsection is necessary here because get_entities_in_published_container() accesses
        # container.versioning.published, and .versioning is cached with the old version. But this seems like
        # a footgun? We could avoid this if get_entities_in_published_container() took only an ID instead of an object,
        # but that would involve additional database lookup(s).
        subsection.refresh_from_db()
        assert content_api.contains_unpublished_changes(subsection.pk) is False
        assert content_api.get_units_in_subsection(subsection, published=True) == [
            Entry(self.unit_1_v1),
        ]

    def test_soft_deleting_unit(self):
        """ Test soft deleting a unit that's in a subsection (but not removing it) """
        subsection = self.create_subsection_with_units([self.unit_1, self.unit_2])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now soft delete unit 2
        content_api.soft_delete_draft(self.unit_2.pk)

        # Now it should not be listed in the subsection:
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(self.unit_1_v1),
            # unit 2 is soft deleted from the draft.
            # TODO: should we return some kind of placeholder here, to indicate that a unit is still listed in the
            # subsection's unit list but has been soft deleted, and will be fully deleted when published, or restored if
            # reverted?
        ]
        assert subsection.versioning.has_unpublished_changes is False  # Subsection and unit list unchanged
        assert content_api.contains_unpublished_changes(subsection.pk)  # It still contains an unpublished deletion
        # The published version of the subsection is not yet affected:
        assert content_api.get_units_in_subsection(subsection, published=True) == [
            Entry(self.unit_1_v1),
            Entry(self.unit_2_v1),
        ]

        # But when we publish the deletion, the published version is affected:
        content_api.publish_all_drafts(self.learning_package.id)
        assert content_api.contains_unpublished_changes(subsection.pk) is False
        assert content_api.get_units_in_subsection(subsection, published=True) == [
            Entry(self.unit_1_v1),
        ]

    def test_soft_deleting_and_removing_unit(self):
        """ Test soft deleting a unit that's in a subsection AND removing it """
        subsection = self.create_subsection_with_units([self.unit_1, self.unit_2])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now soft delete unit 2
        content_api.soft_delete_draft(self.unit_2.pk)
        # And remove it from the subsection:
        content_api.create_next_subsection_version(
            subsection=subsection,
            title="Revised with unit 2 deleted",
            units=[self.unit_2],
            created=self.now,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )

        # Now it should not be listed in the subsection:
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(self.unit_1_v1),
        ]
        assert subsection.versioning.has_unpublished_changes is True
        assert content_api.contains_unpublished_changes(subsection.pk)
        # The published version of the subsection is not yet affected:
        assert content_api.get_units_in_subsection(subsection, published=True) == [
            Entry(self.unit_1_v1),
            Entry(self.unit_2_v1),
        ]

        # But when we publish the deletion, the published version is affected:
        content_api.publish_all_drafts(self.learning_package.id)
        assert content_api.contains_unpublished_changes(subsection.pk) is False
        assert content_api.get_units_in_subsection(subsection, published=True) == [
            Entry(self.unit_1_v1),
        ]

    def test_soft_deleting_pinned_unit(self):
        """ Test soft deleting a pinned 📌 unit that's in a subsection """
        subsection = self.create_subsection_with_units([self.unit_1_v1, self.unit_2_v1])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now soft delete unit 2
        content_api.soft_delete_draft(self.unit_2.pk)

        # Now it should still be listed in the subsection:
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(self.unit_1_v1, pinned=True),
            Entry(self.unit_2_v1, pinned=True),
        ]
        assert subsection.versioning.has_unpublished_changes is False  # Subsection and unit list unchanged
        assert content_api.contains_unpublished_changes(subsection.pk) is False  # nor does it contain changes
        # The published version of the subsection is also not affected:
        assert content_api.get_units_in_subsection(subsection, published=True) == [
            Entry(self.unit_1_v1, pinned=True),
            Entry(self.unit_2_v1, pinned=True),
        ]

    def test_soft_delete_subsection(self):
        """
        I can delete a subsection without deleting the units it contains.

        See https://github.com/openedx/frontend-app-authoring/issues/1693
        """
        # Create two subsections, one of which we will soon delete:
        subsection_to_delete = self.create_subsection_with_units([self.unit_1, self.unit_2])
        other_subsection = self.create_subsection_with_units([self.unit_1], key="other")

        # Publish everything:
        content_api.publish_all_drafts(self.learning_package.id)
        # Delete the subsection:
        content_api.soft_delete_draft(subsection_to_delete.publishable_entity_id)
        subsection_to_delete.refresh_from_db()
        # Now the draft subsection is soft deleted; units, published subsection, and other subsection are unaffected:
        assert subsection_to_delete.versioning.draft is None  # Subsection is soft deleted.
        assert subsection_to_delete.versioning.published is not None
        self.unit_1.refresh_from_db()
        assert self.unit_1.versioning.draft is not None
        assert content_api.get_units_in_subsection(other_subsection, published=False) == [Entry(self.unit_1_v1)]

        # Publish everything:
        content_api.publish_all_drafts(self.learning_package.id)
        # Now the subsection's published version is also deleted, but nothing else is affected.
        subsection_to_delete.refresh_from_db()
        assert subsection_to_delete.versioning.draft is None  # Subsection is soft deleted.
        assert subsection_to_delete.versioning.published is None
        self.unit_1.refresh_from_db()
        assert self.unit_1.versioning.draft is not None
        assert self.unit_1.versioning.published is not None
        assert content_api.get_units_in_subsection(other_subsection, published=False) == [Entry(self.unit_1_v1)]
        assert content_api.get_units_in_subsection(other_subsection, published=True) == [Entry(self.unit_1_v1)]

    def test_snapshots_of_published_subsection(self):
        """
        Test that we can access snapshots of the historic published version of
        subsections and their contents.
        """
        # At first the subsection has one unit (unpinned):
        subsection = self.create_subsection_with_units([self.unit_1])
        self.modify_unit(self.unit_1, title="Unit 1 as of checkpoint 1")
        before_publish = content_api.get_units_in_published_subsection_as_of(subsection, 0)
        assert before_publish is None

        # Publish everything, creating Checkpoint 1
        checkpoint_1 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 1")

        ########################################################################

        # Now we update the title of the unit.
        self.modify_unit(self.unit_1, title="Unit 1 as of checkpoint 2")
        # Publish everything, creating Checkpoint 2
        checkpoint_2 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 2")
        ########################################################################

        # Now add a second unit to the subsection:
        self.modify_unit(self.unit_1, title="Unit 1 as of checkpoint 3")
        self.modify_unit(self.unit_2, title="Unit 2 as of checkpoint 3")
        content_api.create_next_subsection_version(
            subsection=subsection,
            title="Subsection title in checkpoint 3",
            units=[self.unit_1, self.unit_2],
            created=self.now,
        )
        # Publish everything, creating Checkpoint 3
        checkpoint_3 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 3")
        ########################################################################

        # Now add a third unit to the subsection, a pinned 📌 version of unit 1.
        # This will test pinned versions and also test adding at the beginning rather than the end of the subsection.
        content_api.create_next_subsection_version(
            subsection=subsection,
            title="Subsection title in checkpoint 4",
            units=[self.unit_1_v1, self.unit_1, self.unit_2],
            created=self.now,
        )
        # Publish everything, creating Checkpoint 4
        checkpoint_4 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 4")
        ########################################################################

        # Modify the drafts, but don't publish:
        self.modify_unit(self.unit_1, title="Unit 1 draft")
        self.modify_unit(self.unit_2, title="Unit 2 draft")

        # Now fetch the snapshots:
        as_of_checkpoint_1 = content_api.get_units_in_published_subsection_as_of(subsection, checkpoint_1.pk)
        assert [cv.unit_version.title for cv in as_of_checkpoint_1] == [
            "Unit 1 as of checkpoint 1",
        ]
        as_of_checkpoint_2 = content_api.get_units_in_published_subsection_as_of(subsection, checkpoint_2.pk)
        assert [cv.unit_version.title for cv in as_of_checkpoint_2] == [
            "Unit 1 as of checkpoint 2",
        ]
        as_of_checkpoint_3 = content_api.get_units_in_published_subsection_as_of(subsection, checkpoint_3.pk)
        assert [cv.unit_version.title for cv in as_of_checkpoint_3] == [
            "Unit 1 as of checkpoint 3",
            "Unit 2 as of checkpoint 3",
        ]
        as_of_checkpoint_4 = content_api.get_units_in_published_subsection_as_of(subsection, checkpoint_4.pk)
        assert [cv.unit_version.title for cv in as_of_checkpoint_4] == [
            "Unit (1)",  # Pinned. This title is self.unit_1_v1.title (original v1 title)
            "Unit 1 as of checkpoint 3",  # we didn't modify these units so they're same as in snapshot 3
            "Unit 2 as of checkpoint 3",  # we didn't modify these units so they're same as in snapshot 3
        ]

    def test_subsections_containing(self):
        """
        Test that we can efficiently get a list of all the [draft] subsections
        containing a given unit.
        """
        unit_1_v2 = self.modify_unit(self.unit_1, title="modified unit 1")

        # Create a few subsections, some of which contain unit 1 and others which don't:
        # Note: it is important that some of these subsections contain other units, to ensure the complex JOINs required
        # for this query are working correctly, especially in the case of ignore_pinned=True.
        # Subsection 1 ✅ has unit 1, pinned 📌 to V1
        subsection1_1pinned = self.create_subsection_with_units([self.unit_1_v1, self.unit_2], key="u1")
        # Subsection 2 ✅ has unit 1, pinned 📌 to V2
        subsection2_1pinned_v2 = self.create_subsection_with_units([unit_1_v2, self.unit_2_v1], key="u2")
        # Subsection 3 doesn't contain it
        _subsection3_no = self.create_subsection_with_units([self.unit_2], key="u3")
        # Subsection 4 ✅ has unit 1, unpinned
        subsection4_unpinned = self.create_subsection_with_units([
            self.unit_1, self.unit_2, self.unit_2_v1,
        ], key="u4")
        # Subsections 5/6 don't contain it
        _subsection5_no = self.create_subsection_with_units([self.unit_2_v1, self.unit_2], key="u5")
        _subsection6_no = self.create_subsection_with_units([], key="u6")

        # No need to publish anything as the get_containers_with_entity() API only considers drafts (for now).

        with self.assertNumQueries(1):
            result = [
                c.subsection for c in
                content_api.get_containers_with_entity(self.unit_1.pk).select_related("subsection")
            ]
        assert result == [
            subsection1_1pinned,
            subsection2_1pinned_v2,
            subsection4_unpinned,
        ]

        # Test retrieving only "unpinned", for cases like potential deletion of a unit, where we wouldn't care
        # about pinned uses anyways (they would be unaffected by a delete).

        with self.assertNumQueries(1):
            result2 = [
                c.subsection for c in
                content_api.get_containers_with_entity(
                    self.unit_1.pk, ignore_pinned=True
                ).select_related("subsection")
            ]
        assert result2 == [subsection4_unpinned]

    def test_get_units_in_subsection_queries(self):
        """
        Test the query count of get_units_in_subsection()
        This also tests the generic method get_entities_in_container()
        """
        subsection = self.create_subsection_with_units([
            self.unit_1,
            self.unit_2,
            self.unit_2_v1,
        ])
        with self.assertNumQueries(4):
            result = content_api.get_units_in_subsection(subsection, published=False)
        assert result == [
            Entry(self.unit_1.versioning.draft),
            Entry(self.unit_2.versioning.draft),
            Entry(self.unit_2.versioning.draft, pinned=True),
        ]
        content_api.publish_all_drafts(self.learning_package.id)
        with self.assertNumQueries(4):
            result = content_api.get_units_in_subsection(subsection, published=True)
        assert result == [
            Entry(self.unit_1.versioning.draft),
            Entry(self.unit_2.versioning.draft),
            Entry(self.unit_2.versioning.draft, pinned=True),
        ]

    def test_add_remove_container_children(self):
        """
        Test adding and removing children units from subsections.
        """
        subsection, subsection_version = content_api.create_subsection_and_version(
            learning_package_id=self.learning_package.id,
            key="subsection:key",
            title="Subsection",
            units=[self.unit_1],
            created=self.now,
            created_by=None,
        )
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(self.unit_1.versioning.draft),
        ]
        unit_3, _ = self.create_unit(
            key="Unit (3)",
            title="Unit (3)",
        )
        # Add unit_2 and unit_3
        subsection_version_v2 = content_api.create_next_subsection_version(
            subsection=subsection,
            title=subsection_version.title,
            units=[self.unit_2, unit_3],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        subsection.refresh_from_db()
        assert subsection_version_v2.version_num == 2
        assert subsection_version_v2 in subsection.versioning.versions.all()
        # Verify that unit_2 and unit_3 is added to end
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(self.unit_1.versioning.draft),
            Entry(self.unit_2.versioning.draft),
            Entry(unit_3.versioning.draft),
        ]

        # Remove unit_1
        content_api.create_next_subsection_version(
            subsection=subsection,
            title=subsection_version.title,
            units=[self.unit_1],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )
        subsection.refresh_from_db()
        # Verify that unit_1 is removed
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(self.unit_2.versioning.draft),
            Entry(unit_3.versioning.draft),
        ]

    def test_get_container_children_count(self):
        """
        Test get_container_children_count()
        """
        subsection = self.create_subsection_with_units([self.unit_1])
        assert content_api.get_container_children_count(subsection.container, published=False) == 1
        # publish
        content_api.publish_all_drafts(self.learning_package.id)
        subsection_version = subsection.versioning.draft
        content_api.create_next_subsection_version(
            subsection=subsection,
            title=subsection_version.title,
            units=[self.unit_2],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        subsection.refresh_from_db()
        # Should have two units in draft version and 1 in published version
        assert content_api.get_container_children_count(subsection.container, published=False) == 2
        assert content_api.get_container_children_count(subsection.container, published=True) == 1
        # publish
        content_api.publish_all_drafts(self.learning_package.id)
        subsection.refresh_from_db()
        assert content_api.get_container_children_count(subsection.container, published=True) == 2
        # Soft delete unit_1
        content_api.soft_delete_draft(self.unit_1.pk)
        subsection.refresh_from_db()
        # Should contain only 1 child
        assert content_api.get_container_children_count(subsection.container, published=False) == 1
        content_api.publish_all_drafts(self.learning_package.id)
        subsection.refresh_from_db()
        assert content_api.get_container_children_count(subsection.container, published=True) == 1

    # Tests TODO:
    # Test that I can get a [PublishLog] history of a given subsection and all its children, including children
    #     that aren't currently in the subsection and excluding children that are only in other subsections.
    # Test that I can get a [PublishLog] history of a given subsection and its children, that includes changes
    #     made to the child units while they were part of the subsection but excludes changes
    #     made to those children while they were not part of the subsection. 🫣
