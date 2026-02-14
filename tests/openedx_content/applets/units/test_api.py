"""
Basic tests for the units API.
"""
from unittest.mock import patch

import ddt  # type: ignore[import]
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

import openedx_content.api as content_api
from openedx_content import models_api as authoring_models

from ..components.test_api import ComponentTestCase

Entry = content_api.UnitListEntry


@ddt.ddt
class UnitTestCase(ComponentTestCase):
    """ Test cases for Units (containers of components) """

    def setUp(self) -> None:
        super().setUp()
        self.component_1, self.component_1_v1 = self.create_component(
            key="Query Counting",
            title="Querying Counting Problem",
        )
        self.component_2, self.component_2_v1 = self.create_component(
            key="Query Counting (2)",
            title="Querying Counting Problem (2)",
        )

    def create_component(self, *, title: str = "Test Component", key: str = "component:1") -> tuple[
        authoring_models.Component, authoring_models.ComponentVersion
    ]:
        """ Helper method to quickly create a component """
        return content_api.create_component_and_version(
            self.learning_package.id,
            component_type=self.problem_type,
            local_key=key,
            title=title,
            created=self.now,
            created_by=None,
        )

    def create_unit_with_components(
        self,
        components: list[authoring_models.Component | authoring_models.ComponentVersion],
        *,
        title="Unit",
        key="unit:key",
    ) -> authoring_models.Unit:
        """ Helper method to quickly create a unit with some components """
        unit, _unit_v1 = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            key=key,
            title=title,
            components=components,
            created=self.now,
            created_by=None,
        )
        return unit

    def modify_component(
        self,
        component: authoring_models.Component,
        *,
        title="Modified Component",
        timestamp=None,
    ) -> authoring_models.ComponentVersion:
        """
        Helper method to modify a component for the purposes of testing units/drafts/pinning/publishing/etc.
        """
        return content_api.create_next_component_version(
            component.pk,
            content_to_replace={},
            title=title,
            created=timestamp or self.now,
            created_by=None,
        )

    def test_get_unit(self):
        """
        Test get_unit()
        """
        unit = self.create_unit_with_components([self.component_1, self.component_2])
        with self.assertNumQueries(1):
            result = content_api.get_unit(unit.pk)
        assert result == unit
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result.versioning.has_unpublished_changes

    def test_get_unit_version(self):
        """
        Test get_unit_version()
        """
        unit = self.create_unit_with_components([])
        draft = unit.versioning.draft
        with self.assertNumQueries(1):
            result = content_api.get_unit_version(draft.pk)
        assert result == draft

    def test_get_latest_unit_version(self):
        """
        Test test_get_latest_unit_version()
        """
        unit = self.create_unit_with_components([])
        draft = unit.versioning.draft
        with self.assertNumQueries(2):
            result = content_api.get_latest_unit_version(unit.pk)
        assert result == draft

    def test_get_containers(self):
        """
        Test get_containers()
        """
        unit = self.create_unit_with_components([])
        with self.assertNumQueries(1):
            result = list(content_api.get_containers(self.learning_package.id))
        assert result == [unit.container]
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result[0].versioning.has_unpublished_changes

    def test_get_containers_deleted(self):
        """
        Test that get_containers() does not return soft-deleted units.
        """
        unit = self.create_unit_with_components([])
        content_api.soft_delete_draft(unit.pk)
        with self.assertNumQueries(1):
            result = list(content_api.get_containers(self.learning_package.id, include_deleted=True))
        assert result == [unit.container]

        with self.assertNumQueries(1):
            result = list(content_api.get_containers(self.learning_package.id))
        assert not result

    def test_get_container(self):
        """
        Test get_container()
        """
        unit = self.create_unit_with_components([self.component_1, self.component_2])
        with self.assertNumQueries(1):
            result = content_api.get_container(unit.pk)
        assert result == unit.container
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result.versioning.has_unpublished_changes

    def test_get_container_by_key(self):
        """
        Test get_container_by_key()
        """
        unit = self.create_unit_with_components([])
        with self.assertNumQueries(1):
            result = content_api.get_container_by_key(
                self.learning_package.id,
                key=unit.publishable_entity.key,
            )
        assert result == unit.container
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result.versioning.has_unpublished_changes

    def test_unit_container_versioning(self):
        """
        Test that the .versioning helper of a Unit returns a UnitVersion, and
        same for the generic Container equivalent.
        """
        unit = self.create_unit_with_components([self.component_1, self.component_2])
        container = unit.container
        container_version = container.versioning.draft
        assert isinstance(container_version, authoring_models.ContainerVersion)
        unit_version = unit.versioning.draft
        assert isinstance(unit_version, authoring_models.UnitVersion)
        assert unit_version.container_version == container_version
        assert unit_version.container_version.container == container
        assert unit_version.unit == unit

    def test_create_unit_queries(self):
        """
        Test how many database queries are required to create a unit
        """
        # The exact numbers here aren't too important - this is just to alert us if anything significant changes.
        with self.assertNumQueries(26):
            _empty_unit = self.create_unit_with_components([])
        with self.assertNumQueries(32):
            # And try with a non-empty unit:
            self.create_unit_with_components([self.component_1, self.component_2_v1], key="u2")

    def test_create_unit_with_invalid_children(self):
        """
        Verify that only components can be added to units, and a specific
        exception is raised.
        """
        # Create two units:
        unit, unit_version = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            key="unit:key",
            title="Unit",
            created=self.now,
            created_by=None,
        )
        assert unit.versioning.draft == unit_version
        unit2, _u2v1 = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            key="unit:key2",
            title="Unit 2",
            created=self.now,
            created_by=None,
        )
        # Try adding a Unit to a Unit
        with pytest.raises(TypeError, match="Unit components must be either Component or ComponentVersion."):
            content_api.create_next_unit_version(
                unit=unit,
                title="Unit Containing a Unit",
                components=[unit2],
                created=self.now,
                created_by=None,
            )
        # Check that a new version was not created:
        unit.refresh_from_db()
        assert content_api.get_unit(unit.pk).versioning.draft == unit_version
        assert unit.versioning.draft == unit_version

    def test_adding_external_components(self):
        """
        Test that components from another learning package cannot be added to a
        unit.
        """
        learning_package2 = content_api.create_learning_package(key="other-package", title="Other Package")
        unit, _unit_version = content_api.create_unit_and_version(
            learning_package_id=learning_package2.pk,
            key="unit:key",
            title="Unit",
            created=self.now,
            created_by=None,
        )
        assert self.component_1.learning_package != learning_package2
        # Try adding a a component from LP 1 (self.learning_package) to a unit from LP 2
        with pytest.raises(ValidationError, match="Container entities must be from the same learning package."):
            content_api.create_next_unit_version(
                unit=unit,
                title="Unit Containing an External Component",
                components=[self.component_1],
                created=self.now,
                created_by=None,
            )

    @patch('openedx_content.applets.units.api._pub_entities_for_components')
    def test_adding_mismatched_versions(self, mock_entities_for_components):
        """
        Test that versioned components must match their entities.
        """
        mock_entities_for_components.return_value = [
            content_api.ContainerEntityRow(
                entity_pk=self.component_1.pk,
                version_pk=self.component_2_v1.pk,
            ),
        ]
        # Try adding a a component from LP 1 (self.learning_package) to a unit from LP 2
        with pytest.raises(ValidationError, match="Container entity versions must belong to the specified entity"):
            content_api.create_unit_and_version(
                learning_package_id=self.component_1.learning_package.id,
                key="unit:key",
                title="Unit",
                components=[self.component_1],
                created=self.now,
                created_by=None,
            )

    @ddt.data(True, False)
    def test_cannot_add_invalid_ids(self, pin_version):
        """
        Test that non-existent components cannot be added to units
        """
        self.component_1.delete()
        if pin_version:
            components = [self.component_1_v1]
        else:
            components = [self.component_1]
        with pytest.raises((IntegrityError, authoring_models.Component.DoesNotExist)):
            self.create_unit_with_components(components)

    def test_create_empty_unit_and_version(self):
        """Test creating a unit with no components.

        Expected results:
        1. A unit and unit version are created.
        2. The unit version number is 1.
        3. The unit is a draft with unpublished changes.
        4. There is no published version of the unit.
        """
        unit, unit_version = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            key="unit:key",
            title="Unit",
            created=self.now,
            created_by=None,
        )
        assert unit, unit_version
        assert unit_version.version_num == 1
        assert unit_version in unit.versioning.versions.all()
        assert unit.versioning.has_unpublished_changes
        assert unit.versioning.draft == unit_version
        assert unit.versioning.published is None
        assert unit.publishable_entity.can_stand_alone

    def test_create_next_unit_version_with_two_unpinned_components(self):
        """Test creating a unit version with two unpinned components.

        Expected results:
        1. A new unit version is created.
        2. The unit version number is 2.
        3. The unit version is in the unit's versions.
        4. The components are in the draft unit version's component list and are unpinned.
        """
        unit, _unit_version = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            key="unit:key",
            title="Unit",
            created=self.now,
            created_by=None,
        )
        unit_version_v2 = content_api.create_next_unit_version(
            unit=unit,
            title="Unit",
            components=[self.component_1, self.component_2],
            created=self.now,
            created_by=None,
        )
        assert unit_version_v2.version_num == 2
        assert unit_version_v2 in unit.versioning.versions.all()
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(self.component_1.versioning.draft),
            Entry(self.component_2.versioning.draft),
        ]
        with pytest.raises(authoring_models.ContainerVersion.DoesNotExist):
            # There is no published version of the unit:
            content_api.get_components_in_unit(unit, published=True)

    def test_create_next_unit_version_with_unpinned_and_pinned_components(self):
        """
        Test creating a unit version with one unpinned and one pinned 📌 component.
        """
        unit, _unit_version = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            key="unit:key",
            title="Unit",
            created=self.now,
            created_by=None,
        )
        unit_version_v2 = content_api.create_next_unit_version(
            unit=unit,
            title="Unit",
            components=[self.component_1, self.component_2_v1],  # Note the "v1" pinning 📌 the second one to version 1
            created=self.now,
            created_by=None,
        )
        assert unit_version_v2.version_num == 2
        assert unit_version_v2 in unit.versioning.versions.all()
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(self.component_1_v1),
            Entry(self.component_2_v1, pinned=True),  # Pinned 📌 to v1
        ]
        with pytest.raises(authoring_models.ContainerVersion.DoesNotExist):
            # There is no published version of the unit:
            content_api.get_components_in_unit(unit, published=True)

    def test_create_next_unit_version_forcing_version_num(self):
        """
        Test creating a unit version with forcing the version number.
        """
        unit, _unit_version = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            key="unit:key",
            title="Unit",
            created=self.now,
            created_by=None,
        )
        unit_version_v2 = content_api.create_next_unit_version(
            unit=unit,
            title="Unit",
            components=[self.component_1, self.component_2_v1],  # Note the "v1" pinning 📌 the second one to version 1
            created=self.now,
            created_by=None,
            force_version_num=5,
        )
        assert unit_version_v2.version_num == 5

    def test_auto_publish_children(self):
        """
        Test that publishing a unit publishes its child components automatically.
        """
        # Create a draft unit with two draft components
        unit = self.create_unit_with_components([self.component_1, self.component_2])
        # Also create another component that's not in the unit at all:
        other_component, _oc_v1 = self.create_component(title="A draft component not in the unit", key="component:3")

        assert content_api.contains_unpublished_changes(unit.pk)
        assert self.component_1.versioning.published is None
        assert self.component_2.versioning.published is None

        # Publish ONLY the unit. This should however also auto-publish components 1 & 2 since they're children
        content_api.publish_from_drafts(
            self.learning_package.pk,
            draft_qset=content_api.get_all_drafts(self.learning_package.pk).filter(entity=unit.publishable_entity),
        )
        # Now all changes to the unit and to component 1 are published:
        unit.refresh_from_db()
        self.component_1.refresh_from_db()
        assert unit.versioning.has_unpublished_changes is False  # Shallow check
        assert self.component_1.versioning.has_unpublished_changes is False
        assert content_api.contains_unpublished_changes(unit.pk) is False  # Deep check
        assert self.component_1.versioning.published == self.component_1_v1  # v1 is now the published version.

        # But our other component that's outside the unit is not affected:
        other_component.refresh_from_db()
        assert other_component.versioning.has_unpublished_changes
        assert other_component.versioning.published is None

    def test_no_publish_parent(self):
        """
        Test that publishing a component does NOT publish changes to its parent unit
        """
        # Create a draft unit with two draft components
        unit = self.create_unit_with_components([self.component_1, self.component_2])
        assert unit.versioning.has_unpublished_changes
        # Publish ONLY one of its child components
        self.publish_component(self.component_1)
        self.component_1.refresh_from_db()  # Clear cache on '.versioning'
        assert self.component_1.versioning.has_unpublished_changes is False

        # The unit that contains that component should still be unpublished:
        unit.refresh_from_db()  # Clear cache on '.versioning'
        assert unit.versioning.has_unpublished_changes
        assert unit.versioning.published is None
        with pytest.raises(authoring_models.ContainerVersion.DoesNotExist):
            # There is no published version of the unit:
            content_api.get_components_in_unit(unit, published=True)

    def test_add_component_after_publish(self):
        """
        Adding a component to a published unit will create a new version and
        show that the unit has unpublished changes.
        """
        unit, unit_version = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            key="unit:key",
            title="Unit",
            created=self.now,
            created_by=None,
        )
        assert unit.versioning.draft == unit_version
        assert unit.versioning.published is None
        assert unit.versioning.has_unpublished_changes
        # Publish the empty unit:
        content_api.publish_all_drafts(self.learning_package.id)
        unit.refresh_from_db()  # Reloading the unit is necessary
        assert unit.versioning.has_unpublished_changes is False  # Shallow check for just the unit itself, not children
        assert content_api.contains_unpublished_changes(unit.pk) is False  # Deeper check

        # Add a published component (unpinned):
        assert self.component_1.versioning.has_unpublished_changes is False
        unit_version_v2 = content_api.create_next_unit_version(
            unit=unit,
            title=unit_version.title,
            components=[self.component_1],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        # Now the unit should have unpublished changes:
        unit.refresh_from_db()  # Reloading the unit is necessary
        assert unit.versioning.has_unpublished_changes  # Shallow check - adding a child is a change to the unit
        assert content_api.contains_unpublished_changes(unit.pk)  # Deeper check
        assert unit.versioning.draft == unit_version_v2
        assert unit.versioning.published == unit_version

    def test_modify_unpinned_component_after_publish(self):
        """
        Modifying an unpinned component in a published unit will NOT create a
        new version nor show that the unit has unpublished changes (but it will
        "contain" unpublished changes). The modifications will appear in the
        published version of the unit only after the component is published.
        """
        # Create a unit with one unpinned draft component:
        assert self.component_1.versioning.has_unpublished_changes
        unit = self.create_unit_with_components([self.component_1])
        assert unit.versioning.has_unpublished_changes

        # Publish the unit and the component:
        content_api.publish_all_drafts(self.learning_package.id)
        unit.refresh_from_db()  # Reloading the unit is necessary if we accessed 'versioning' before publish
        self.component_1.refresh_from_db()
        assert unit.versioning.has_unpublished_changes is False  # Shallow check
        assert content_api.contains_unpublished_changes(unit.pk) is False  # Deeper check
        assert self.component_1.versioning.has_unpublished_changes is False

        # Now modify the component by changing its title (it remains a draft):
        component_1_v2 = self.modify_component(self.component_1, title="Modified Counting Problem with new title")

        # The component now has unpublished changes; the unit doesn't directly but does contain
        unit.refresh_from_db()  # Reloading the unit is necessary, or 'unit.versioning' will be outdated
        self.component_1.refresh_from_db()
        assert unit.versioning.has_unpublished_changes is False  # Shallow check should be false - unit is unchanged
        assert content_api.contains_unpublished_changes(unit.pk)  # But unit DOES contain changes
        assert self.component_1.versioning.has_unpublished_changes

        # Since the component changes haven't been published, they should only appear in the draft unit
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(component_1_v2),  # new version
        ]
        assert content_api.get_components_in_unit(unit, published=True) == [
            Entry(self.component_1_v1),  # old version
        ]

        # But if we publish the component, the changes will appear in the published version of the unit.
        self.publish_component(self.component_1)
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(component_1_v2),  # new version
        ]
        assert content_api.get_components_in_unit(unit, published=True) == [
            Entry(component_1_v2),  # new version
        ]
        assert content_api.contains_unpublished_changes(unit.pk) is False  # No longer contains unpublished changes

    def test_modify_pinned_component(self):
        """
        When a pinned 📌 component in unit is modified and/or published, it will
        have no effect on either the draft nor published version of the unit,
        which will continue to use the pinned version.
        """
        # Create a unit with one component (pinned 📌 to v1):
        unit = self.create_unit_with_components([self.component_1_v1])

        # Publish the unit and the component:
        content_api.publish_all_drafts(self.learning_package.id)
        expected_unit_contents = [
            Entry(self.component_1_v1, pinned=True),  # pinned 📌 to v1
        ]
        assert content_api.get_components_in_unit(unit, published=True) == expected_unit_contents

        # Now modify the component by changing its title (it remains a draft):
        self.modify_component(self.component_1, title="Modified Counting Problem with new title")

        # The component now has unpublished changes; the unit is entirely unaffected
        unit.refresh_from_db()  # Reloading the unit is necessary, or 'unit.versioning' will be outdated
        self.component_1.refresh_from_db()
        assert unit.versioning.has_unpublished_changes is False  # Shallow check
        assert content_api.contains_unpublished_changes(unit.pk) is False  # Deep check
        assert self.component_1.versioning.has_unpublished_changes is True

        # Neither the draft nor the published version of the unit is affected
        assert content_api.get_components_in_unit(unit, published=False) == expected_unit_contents
        assert content_api.get_components_in_unit(unit, published=True) == expected_unit_contents
        # Even if we publish the component, the unit stays pinned to the specified version:
        self.publish_component(self.component_1)
        assert content_api.get_components_in_unit(unit, published=False) == expected_unit_contents
        assert content_api.get_components_in_unit(unit, published=True) == expected_unit_contents

    def test_create_two_units_with_same_components(self):
        """
        Test creating two units with different combinations of the same two
        components in each unit.
        """
        # Create a unit with component 2 unpinned, component 2 pinned 📌, and component 1:
        unit1 = self.create_unit_with_components([self.component_2, self.component_2_v1, self.component_1], key="u1")
        # Create a second unit with component 1 pinned 📌, component 2, and component 1 unpinned:
        unit2 = self.create_unit_with_components([self.component_1_v1, self.component_2, self.component_1], key="u2")

        # Check that the contents are as expected:
        assert [row.component_version for row in content_api.get_components_in_unit(unit1, published=False)] == [
            self.component_2_v1, self.component_2_v1, self.component_1_v1,
        ]
        assert [row.component_version for row in content_api.get_components_in_unit(unit2, published=False)] == [
            self.component_1_v1, self.component_2_v1, self.component_1_v1,
        ]

        # Modify component 1
        component_1_v2 = self.modify_component(self.component_1, title="component 1 v2")
        # Publish changes
        content_api.publish_all_drafts(self.learning_package.id)
        # Modify component 2 - only in the draft
        component_2_v2 = self.modify_component(self.component_2, title="component 2 DRAFT")

        # Check that the draft contents are as expected:
        assert content_api.get_components_in_unit(unit1, published=False) == [
            Entry(component_2_v2),  # v2 in the draft version
            Entry(self.component_2_v1, pinned=True),  # pinned 📌 to v1
            Entry(component_1_v2),  # v2
        ]
        assert content_api.get_components_in_unit(unit2, published=False) == [
            Entry(self.component_1_v1, pinned=True),  # pinned 📌 to v1
            Entry(component_2_v2),  # v2 in the draft version
            Entry(component_1_v2),  # v2
        ]

        # Check that the published contents are as expected:
        assert content_api.get_components_in_unit(unit1, published=True) == [
            Entry(self.component_2_v1),  # v1 in the published version
            Entry(self.component_2_v1, pinned=True),  # pinned 📌 to v1
            Entry(component_1_v2),  # v2
        ]
        assert content_api.get_components_in_unit(unit2, published=True) == [
            Entry(self.component_1_v1, pinned=True),  # pinned 📌 to v1
            Entry(self.component_2_v1),  # v1 in the published version
            Entry(component_1_v2),  # v2
        ]

    def test_publishing_shared_component(self):
        """
        A complex test case involving two units with a shared component and
        other non-shared components.

        Unit 1: components C1, C2, C3
        Unit 2: components C2, C4, C5
        Everything is "unpinned".
        """
        # 1️⃣ Create the units and publish them:
        (c1, c1_v1), (c2, _c2_v1), (c3, c3_v1), (c4, c4_v1), (c5, c5_v1) = [
            self.create_component(key=f"C{i}", title=f"Component {i}") for i in range(1, 6)
        ]
        unit1 = self.create_unit_with_components([c1, c2, c3], title="Unit 1", key="unit:1")
        unit2 = self.create_unit_with_components([c2, c4, c5], title="Unit 2", key="unit:2")
        content_api.publish_all_drafts(self.learning_package.id)
        assert content_api.contains_unpublished_changes(unit1.pk) is False
        assert content_api.contains_unpublished_changes(unit2.pk) is False

        # 2️⃣ Then the author edits C2 inside of Unit 1 making C2v2.
        c2_v2 = self.modify_component(c2, title="C2 version 2")
        # This makes U1 and U2 both show up as Units that CONTAIN unpublished changes, because they share the component.
        assert content_api.contains_unpublished_changes(unit1.pk)
        assert content_api.contains_unpublished_changes(unit2.pk)
        # (But the units themselves are unchanged:)
        unit1.refresh_from_db()
        unit2.refresh_from_db()
        assert unit1.versioning.has_unpublished_changes is False
        assert unit2.versioning.has_unpublished_changes is False

        # 3️⃣ In addition to this, the author also modifies another component in Unit 2 (C5)
        c5_v2 = self.modify_component(c5, title="C5 version 2")

        # 4️⃣ The author then publishes Unit 1, and therefore everything in it.
        content_api.publish_from_drafts(
            self.learning_package.pk,
            draft_qset=content_api.get_all_drafts(self.learning_package.pk).filter(
                # Note: we only publish the unit; the publishing API should auto-publish its components too.
                entity_id=unit1.publishable_entity.id,
            ),
        )

        # Result: Unit 1 will show the newly published version of C2:
        assert content_api.get_components_in_unit(unit1, published=True) == [
            Entry(c1_v1),
            Entry(c2_v2),  # new published version of C2
            Entry(c3_v1),
        ]

        # Result: someone looking at Unit 2 should see the newly published component 2, because publishing it anywhere
        # publishes it everywhere. But publishing C2 and Unit 1 does not affect the other components in Unit 2.
        # (Publish propagates downward, not upward)
        assert content_api.get_components_in_unit(unit2, published=True) == [
            Entry(c2_v2),  # new published version of C2
            Entry(c4_v1),  # still original version of C4 (it was never modified)
            Entry(c5_v1),  # still original version of C5 (it hasn't been published)
        ]

        # Result: Unit 2 CONTAINS unpublished changes because of the modified C5. Unit 1 doesn't contain unpub changes.
        assert content_api.contains_unpublished_changes(unit1.pk) is False
        assert content_api.contains_unpublished_changes(unit2.pk)

        # 5️⃣ Publish component C5, which should be the only thing unpublished in the learning package
        self.publish_component(c5)
        # Result: Unit 2 shows the new version of C5 and no longer contains unpublished changes:
        assert content_api.get_components_in_unit(unit2, published=True) == [
            Entry(c2_v2),  # new published version of C2
            Entry(c4_v1),  # still original version of C4 (it was never modified)
            Entry(c5_v2),  # new published version of C5
        ]
        assert content_api.contains_unpublished_changes(unit2.pk) is False

    def test_query_count_of_contains_unpublished_changes(self):
        """
        Checking for unpublished changes in a unit should require a fixed number
        of queries, not get more expensive as the unit gets larger.
        """
        # Add 100 components (unpinned)
        component_count = 100
        components = []
        for i in range(component_count):
            component, _version = self.create_component(
                key=f"Query Counting {i}",
                title=f"Querying Counting Problem {i}",
            )
            components.append(component)
        unit = self.create_unit_with_components(components)
        content_api.publish_all_drafts(self.learning_package.id)
        unit.refresh_from_db()
        with self.assertNumQueries(1):
            assert content_api.contains_unpublished_changes(unit.pk) is False

        # Modify the most recently created component:
        self.modify_component(component, title="Modified Component")
        with self.assertNumQueries(1):
            assert content_api.contains_unpublished_changes(unit.pk) is True

    def test_metadata_change_doesnt_create_entity_list(self):
        """
        Test that changing a container's metadata like title will create a new
        version, but can re-use the same EntityList. API consumers generally
        shouldn't depend on this behavior; it's an optimization.
        """
        unit = self.create_unit_with_components([self.component_1, self.component_2_v1])

        orig_version_num = unit.versioning.draft.version_num
        orig_entity_list_id = unit.versioning.draft.entity_list.pk

        content_api.create_next_unit_version(unit, title="New Title", created=self.now)

        unit.refresh_from_db()
        new_version_num = unit.versioning.draft.version_num
        new_entity_list_id = unit.versioning.draft.entity_list.pk

        assert new_version_num > orig_version_num
        assert new_entity_list_id == orig_entity_list_id

    @ddt.data(True, False)
    @pytest.mark.skip(reason="FIXME: we don't yet prevent adding soft-deleted components to units")
    def test_cannot_add_soft_deleted_component(self, publish_first):
        """
        Test that a soft-deleted component cannot be added to a unit.

        Although it's valid for units to contain soft-deleted components (by
        deleting the component after adding it), it is likely a mistake if
        you're trying to add one to the unit.
        """
        component, _cv = self.create_component(title="Deleted component")
        if publish_first:
            # Publish the component:
            content_api.publish_all_drafts(self.learning_package.id)
        # Now delete it. The draft version is now deleted:
        content_api.soft_delete_draft(component.pk)
        # Now try adding that component to a unit:
        with pytest.raises(ValidationError, match="component is deleted"):
            self.create_unit_with_components([component])

    def test_removing_component(self):
        """ Test removing a component from a unit (but not deleting it) """
        unit = self.create_unit_with_components([self.component_1, self.component_2])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now remove component 2
        content_api.create_next_unit_version(
            unit=unit,
            title="Revised with component 2 deleted",
            components=[self.component_2],
            created=self.now,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )

        # Now it should not be listed in the unit:
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(self.component_1_v1),
        ]
        unit.refresh_from_db()
        assert unit.versioning.has_unpublished_changes  # The unit itself and its component list have change
        assert content_api.contains_unpublished_changes(unit.pk)
        # The published version of the unit is not yet affected:
        assert content_api.get_components_in_unit(unit, published=True) == [
            Entry(self.component_1_v1),
            Entry(self.component_2_v1),
        ]

        # But when we publish the new unit version with the removal, the published version is affected:
        content_api.publish_all_drafts(self.learning_package.id)
        # FIXME: Refreshing the unit is necessary here because get_entities_in_published_container() accesses
        # container.versioning.published, and .versioning is cached with the old version. But this seems like
        # a footgun? We could avoid this if get_entities_in_published_container() took only an ID instead of an object,
        # but that would involve additional database lookup(s).
        unit.refresh_from_db()
        assert content_api.contains_unpublished_changes(unit.pk) is False
        assert content_api.get_components_in_unit(unit, published=True) == [
            Entry(self.component_1_v1),
        ]

    def test_soft_deleting_component(self):
        """ Test soft deleting a component that's in a unit (but not removing it) """
        unit = self.create_unit_with_components([self.component_1, self.component_2])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now soft delete component 2
        content_api.soft_delete_draft(self.component_2.pk)

        # Now it should not be listed in the unit:
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(self.component_1_v1),
            # component 2 is soft deleted from the draft.
            # TODO: should we return some kind of placeholder here, to indicate that a component is still listed in the
            # unit's component list but has been soft deleted, and will be fully deleted when published, or restored if
            # reverted?
        ]
        assert unit.versioning.has_unpublished_changes is False  # The unit itself and its component list is not changed
        assert content_api.contains_unpublished_changes(unit.pk)  # But it CONTAINS an unpublished change (a deletion)
        # The published version of the unit is not yet affected:
        assert content_api.get_components_in_unit(unit, published=True) == [
            Entry(self.component_1_v1),
            Entry(self.component_2_v1),
        ]

        # But when we publish the deletion, the published version is affected:
        content_api.publish_all_drafts(self.learning_package.id)
        assert content_api.contains_unpublished_changes(unit.pk) is False
        assert content_api.get_components_in_unit(unit, published=True) == [
            Entry(self.component_1_v1),
        ]

    def test_soft_deleting_and_removing_component(self):
        """ Test soft deleting a component that's in a unit AND removing it """
        unit = self.create_unit_with_components([self.component_1, self.component_2])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now soft delete component 2
        content_api.soft_delete_draft(self.component_2.pk)
        # And remove it from the unit:
        content_api.create_next_unit_version(
            unit=unit,
            title="Revised with component 2 deleted",
            components=[self.component_2],
            created=self.now,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )

        # Now it should not be listed in the unit:
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(self.component_1_v1),
        ]
        assert unit.versioning.has_unpublished_changes is True
        assert content_api.contains_unpublished_changes(unit.pk)
        # The published version of the unit is not yet affected:
        assert content_api.get_components_in_unit(unit, published=True) == [
            Entry(self.component_1_v1),
            Entry(self.component_2_v1),
        ]

        # But when we publish the deletion, the published version is affected:
        content_api.publish_all_drafts(self.learning_package.id)
        assert content_api.contains_unpublished_changes(unit.pk) is False
        assert content_api.get_components_in_unit(unit, published=True) == [
            Entry(self.component_1_v1),
        ]

    def test_soft_deleting_pinned_component(self):
        """ Test soft deleting a pinned 📌 component that's in a unit """
        unit = self.create_unit_with_components([self.component_1_v1, self.component_2_v1])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now soft delete component 2
        content_api.soft_delete_draft(self.component_2.pk)

        # Now it should still be listed in the unit:
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(self.component_1_v1, pinned=True),
            Entry(self.component_2_v1, pinned=True),
        ]
        assert unit.versioning.has_unpublished_changes is False  # The unit itself and its component list is not changed
        assert content_api.contains_unpublished_changes(unit.pk) is False  # nor does it contain changes
        # The published version of the unit is also not affected:
        assert content_api.get_components_in_unit(unit, published=True) == [
            Entry(self.component_1_v1, pinned=True),
            Entry(self.component_2_v1, pinned=True),
        ]

    def test_soft_delete_unit(self):
        """
        I can delete a unit without deleting the components it contains.

        See https://github.com/openedx/frontend-app-authoring/issues/1693
        """
        # Create two units, one of which we will soon delete:
        unit_to_delete = self.create_unit_with_components([self.component_1, self.component_2])
        other_unit = self.create_unit_with_components([self.component_1], key="other")

        # Publish everything:
        content_api.publish_all_drafts(self.learning_package.id)
        # Delete the unit:
        content_api.soft_delete_draft(unit_to_delete.publishable_entity_id)
        unit_to_delete.refresh_from_db()
        # Now the draft unit is [soft] deleted, but the components, published unit, and other unit is unaffected:
        assert unit_to_delete.versioning.draft is None  # Unit is soft deleted.
        assert unit_to_delete.versioning.published is not None
        self.component_1.refresh_from_db()
        assert self.component_1.versioning.draft is not None
        assert content_api.get_components_in_unit(other_unit, published=False) == [Entry(self.component_1_v1)]

        # Publish everything:
        content_api.publish_all_drafts(self.learning_package.id)
        # Now the unit's published version is also deleted, but nothing else is affected.
        unit_to_delete.refresh_from_db()
        assert unit_to_delete.versioning.draft is None  # Unit is soft deleted.
        assert unit_to_delete.versioning.published is None
        self.component_1.refresh_from_db()
        assert self.component_1.versioning.draft is not None
        assert self.component_1.versioning.published is not None
        assert content_api.get_components_in_unit(other_unit, published=False) == [Entry(self.component_1_v1)]
        assert content_api.get_components_in_unit(other_unit, published=True) == [Entry(self.component_1_v1)]

    def test_snapshots_of_published_unit(self):
        """
        Test that we can access snapshots of the historic published version of
        units and their contents.
        """
        # At first the unit has one component (unpinned):
        unit = self.create_unit_with_components([self.component_1])
        self.modify_component(self.component_1, title="Component 1 as of checkpoint 1")
        before_publish = content_api.get_components_in_published_unit_as_of(unit, 0)
        assert before_publish is None

        # Publish everything, creating Checkpoint 1
        checkpoint_1 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 1")

        ########################################################################

        # Now we update the title of the component.
        self.modify_component(self.component_1, title="Component 1 as of checkpoint 2")
        # Publish everything, creating Checkpoint 2
        checkpoint_2 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 2")
        ########################################################################

        # Now add a second component to the unit:
        self.modify_component(self.component_1, title="Component 1 as of checkpoint 3")
        self.modify_component(self.component_2, title="Component 2 as of checkpoint 3")
        content_api.create_next_unit_version(
            unit=unit,
            title="Unit title in checkpoint 3",
            components=[self.component_1, self.component_2],
            created=self.now,
        )
        # Publish everything, creating Checkpoint 3
        checkpoint_3 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 3")
        ########################################################################

        # Now add a third component to the unit, a pinned 📌 version of component 1.
        # This will test pinned versions and also test adding at the beginning rather than the end of the unit.
        content_api.create_next_unit_version(
            unit=unit,
            title="Unit title in checkpoint 4",
            components=[self.component_1_v1, self.component_1, self.component_2],
            created=self.now,
        )
        # Publish everything, creating Checkpoint 4
        checkpoint_4 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 4")
        ########################################################################

        # Modify the drafts, but don't publish:
        self.modify_component(self.component_1, title="Component 1 draft")
        self.modify_component(self.component_2, title="Component 2 draft")

        # Now fetch the snapshots:
        as_of_checkpoint_1 = content_api.get_components_in_published_unit_as_of(unit, checkpoint_1.pk)
        assert [cv.component_version.title for cv in as_of_checkpoint_1] == [
            "Component 1 as of checkpoint 1",
        ]
        as_of_checkpoint_2 = content_api.get_components_in_published_unit_as_of(unit, checkpoint_2.pk)
        assert [cv.component_version.title for cv in as_of_checkpoint_2] == [
            "Component 1 as of checkpoint 2",
        ]
        as_of_checkpoint_3 = content_api.get_components_in_published_unit_as_of(unit, checkpoint_3.pk)
        assert [cv.component_version.title for cv in as_of_checkpoint_3] == [
            "Component 1 as of checkpoint 3",
            "Component 2 as of checkpoint 3",
        ]
        as_of_checkpoint_4 = content_api.get_components_in_published_unit_as_of(unit, checkpoint_4.pk)
        assert [cv.component_version.title for cv in as_of_checkpoint_4] == [
            "Querying Counting Problem",  # Pinned. This title is self.component_1_v1.title (original v1 title)
            "Component 1 as of checkpoint 3",  # we didn't modify these components so they're same as in snapshot 3
            "Component 2 as of checkpoint 3",  # we didn't modify these components so they're same as in snapshot 3
        ]

    def test_units_containing(self):
        """
        Test that we can efficiently get a list of all the [draft] units
        containing a given component.
        """
        component_1_v2 = self.modify_component(self.component_1, title="modified component 1")

        # Create a few units, some of which contain component 1 and others which don't:
        # Note: it is important that some of these units contain other components, to ensure the complex JOINs required
        # for this query are working correctly, especially in the case of ignore_pinned=True.
        # Unit 1 ✅ has component 1, pinned 📌 to V1
        unit1_1pinned = self.create_unit_with_components([self.component_1_v1, self.component_2], key="u1")
        # Unit 2 ✅ has component 1, pinned 📌 to V2
        unit2_1pinned_v2 = self.create_unit_with_components([component_1_v2, self.component_2_v1], key="u2")
        # Unit 3 doesn't contain it
        _unit3_no = self.create_unit_with_components([self.component_2], key="u3")
        # Unit 4 ✅ has component 1, unpinned
        unit4_unpinned = self.create_unit_with_components([
            self.component_1, self.component_2, self.component_2_v1,
        ], key="u4")
        # Units 5/6 don't contain it
        _unit5_no = self.create_unit_with_components([self.component_2_v1, self.component_2], key="u5")
        _unit6_no = self.create_unit_with_components([], key="u6")
        # To test unique results, unit 7 ✅ contains several copies of component 1. Also tests matching against
        # components that aren't in the first position.
        unit7_several = self.create_unit_with_components([
            self.component_2, self.component_1, self.component_1_v1, self.component_1,
        ], key="u7")

        # No need to publish anything as the get_containers_with_entity() API only considers drafts (for now).

        with self.assertNumQueries(1):
            result = [
                c.unit for c in
                content_api.get_containers_with_entity(self.component_1.pk).select_related("unit")
            ]
        assert result == [
            unit1_1pinned,
            unit2_1pinned_v2,
            unit4_unpinned,
            unit7_several,  # This should only appear once, not several times.
        ]

        # Test retrieving only "unpinned", for cases like potential deletion of a component, where we wouldn't care
        # about pinned uses anyways (they would be unaffected by a delete).

        with self.assertNumQueries(1):
            result2 = [
                c.unit for c in
                content_api.get_containers_with_entity(self.component_1.pk, ignore_pinned=True).select_related("unit")
            ]
        assert result2 == [unit4_unpinned, unit7_several]

    def test_get_components_in_unit_queries(self):
        """
        Test the query count of get_components_in_unit()
        This also tests the generic method get_entities_in_container()
        """
        unit = self.create_unit_with_components([
            self.component_1,
            self.component_2,
            self.component_2_v1,
        ])
        with self.assertNumQueries(3):
            result = content_api.get_components_in_unit(unit, published=False)
        assert result == [
            Entry(self.component_1.versioning.draft),
            Entry(self.component_2.versioning.draft),
            Entry(self.component_2.versioning.draft, pinned=True),
        ]
        content_api.publish_all_drafts(self.learning_package.id)
        with self.assertNumQueries(3):
            result = content_api.get_components_in_unit(unit, published=True)
        assert result == [
            Entry(self.component_1.versioning.draft),
            Entry(self.component_2.versioning.draft),
            Entry(self.component_2.versioning.draft, pinned=True),
        ]

    def test_add_remove_container_children(self):
        """
        Test adding and removing children components from containers.
        """
        unit, unit_version = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            key="unit:key",
            title="Unit",
            components=[self.component_1],
            created=self.now,
            created_by=None,
        )
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(self.component_1.versioning.draft),
        ]
        component_3, _ = self.create_component(
            key="Query Counting (3)",
            title="Querying Counting Problem (3)",
        )
        # Add component_2 and component_3
        unit_version_v2 = content_api.create_next_unit_version(
            unit=unit,
            title=unit_version.title,
            components=[self.component_2, component_3],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        unit.refresh_from_db()
        assert unit_version_v2.version_num == 2
        assert unit_version_v2 in unit.versioning.versions.all()
        # Verify that component_2 and component_3 is added to end
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(self.component_1.versioning.draft),
            Entry(self.component_2.versioning.draft),
            Entry(component_3.versioning.draft),
        ]

        # Remove component_1
        content_api.create_next_unit_version(
            unit=unit,
            title=unit_version.title,
            components=[self.component_1],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )
        unit.refresh_from_db()
        # Verify that component_1 is removed
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(self.component_2.versioning.draft),
            Entry(component_3.versioning.draft),
        ]

    def test_get_container_children_count(self):
        """
        Test get_container_children_count()
        """
        unit = self.create_unit_with_components([self.component_1])
        assert content_api.get_container_children_count(unit.container, published=False) == 1
        # publish
        content_api.publish_all_drafts(self.learning_package.id)
        unit_version = unit.versioning.draft
        content_api.create_next_unit_version(
            unit=unit,
            title=unit_version.title,
            components=[self.component_2],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        unit.refresh_from_db()
        # Should have two components in draft version and 1 in published version
        assert content_api.get_container_children_count(unit.container, published=False) == 2
        assert content_api.get_container_children_count(unit.container, published=True) == 1
        # publish
        content_api.publish_all_drafts(self.learning_package.id)
        unit.refresh_from_db()
        assert content_api.get_container_children_count(unit.container, published=True) == 2
        # Soft delete component_1
        content_api.soft_delete_draft(self.component_1.pk)
        unit.refresh_from_db()
        # Should contain only 1 child
        assert content_api.get_container_children_count(unit.container, published=False) == 1
        content_api.publish_all_drafts(self.learning_package.id)
        unit.refresh_from_db()
        assert content_api.get_container_children_count(unit.container, published=True) == 1

    # Tests TODO:
    # Test that I can get a [PublishLog] history of a given unit and all its children, including children that aren't
    #     currently in the unit and excluding children that are only in other units.
    # Test that I can get a [PublishLog] history of a given unit and its children, that includes changes made to the
    #     child components while they were part of the unit but excludes changes made to those children while they were
    #     not part of the unit. 🫣
