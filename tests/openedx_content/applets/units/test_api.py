"""
Basic tests for the units API.
"""

from typing import cast

import pytest
from django.core.exceptions import ValidationError

import openedx_content.api as content_api
from openedx_content.models_api import Component, ComponentVersion, Unit, UnitVersion
from tests.test_django_app.models import TestContainer

from ..components.test_api import ComponentTestCase

Entry = content_api.UnitListEntry


class UnitsTestCase(ComponentTestCase):
    """Test cases for Units (containers of components)"""

    def setUp(self) -> None:
        super().setUp()
        self.component_1, self.component_1_v1 = self.create_component(
            component_code="Query_Counting",
            title="Querying Counting Problem",
        )
        self.component_2, self.component_2_v1 = self.create_component(
            component_code="Query_Counting_2",
            title="Querying Counting Problem (2)",
        )

    def create_unit_with_components(
        self,
        components: list[Component | ComponentVersion],
        *,
        title="Unit",
        container_code="unit-key",
    ) -> Unit:
        """Helper method to quickly create a unit with some components"""
        unit, _unit_v1 = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            container_code=container_code,
            title=title,
            components=components,
            created=self.now,
            created_by=None,
        )
        return unit

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
            container_code="unit-key",
            title="Unit",
            created=self.now,
            created_by=None,
        )
        assert isinstance(unit, Unit)
        assert isinstance(unit_version, UnitVersion)
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
        unit = self.create_unit_with_components([])
        unit_version_v2 = content_api.create_next_unit_version(
            unit,
            title="Unit",
            components=[self.component_1, self.component_2],
            created=self.now,
            created_by=None,
        )
        assert isinstance(unit_version_v2, UnitVersion)
        assert unit_version_v2.version_num == 2
        assert unit_version_v2 in unit.versioning.versions.all()
        assert content_api.get_components_in_unit(unit, published=False) == [
            Entry(self.component_1.versioning.draft),
            Entry(self.component_2.versioning.draft),
        ]
        with pytest.raises(UnitVersion.DoesNotExist):
            # There is no published version of the unit:
            content_api.get_components_in_unit(unit, published=True)

    def test_get_unit(self) -> None:
        """Test `get_unit()`"""
        unit = self.create_unit_with_components([self.component_1])

        unit_retrieved = content_api.get_unit(unit.id)
        assert isinstance(unit_retrieved, Unit)
        assert unit_retrieved == unit

    def test_get_unit_nonexistent(self) -> None:
        """Test `get_unit()` when the unit doesn't exist"""
        FAKE_ID = cast(Unit.ID, -500)
        with pytest.raises(Unit.DoesNotExist):
            content_api.get_unit(FAKE_ID)

    def test_get_unit_other_container_type(self) -> None:
        """Test `get_unit()` when the provided ID is for a non-Unit container"""
        other_container = content_api.create_container(
            self.learning_package.id,
            container_code="test",
            created=self.now,
            created_by=None,
            container_cls=TestContainer,
        )
        with pytest.raises(Unit.DoesNotExist):
            content_api.get_unit(other_container.id)  # type: ignore[arg-type]

    def test_unit_queries(self) -> None:
        """
        Test the number of queries needed for each part of the units API
        """
        with self.assertNumQueries(41):
            unit = self.create_unit_with_components([self.component_1, self.component_2_v1])
        with self.assertNumQueries(28):
            content_api.publish_from_drafts(
                self.learning_package.id,
                draft_qset=content_api.get_all_drafts(self.learning_package.id).filter(entity=unit.id),
            )
        with self.assertNumQueries(3):
            result = content_api.get_components_in_unit(unit, published=True)
        assert result == [
            Entry(self.component_1_v1),
            Entry(self.component_2_v1, pinned=True),
        ]

    def test_create_unit_with_invalid_children(self):
        """
        Verify that only components can be added to units, and a specific exception is raised.
        """
        # Create two units:
        unit = self.create_unit_with_components([])
        unit_version = unit.versioning.draft
        unit2 = self.create_unit_with_components([], container_code="unit-key2", title="Unit 2")

        # Try adding a Unit to a Unit
        with pytest.raises(
            ValidationError, match='The entity "unit-key2" cannot be added to a "unit" container.'
        ) as err:
            content_api.create_next_unit_version(
                unit,
                components=[unit2],
                created=self.now,
                created_by=None,
            )
        assert "Only Components can be added as children of a Unit" in str(err.value.__cause__)

        # Try adding a generic entity to a Unit
        pe = content_api.create_publishable_entity(self.learning_package.id, "pe", created=self.now, created_by=None)
        pev = content_api.create_publishable_entity_version(
            pe.id, version_num=1, title="t", created=self.now, created_by=None
        )
        with pytest.raises(ValidationError, match='The entity "pe" cannot be added to a "unit" container.') as err:
            content_api.create_next_unit_version(
                unit,
                components=[pev],
                created=self.now,
                created_by=None,
            )
        assert "Only Components can be added as children of a Unit" in str(err.value.__cause__)

        # Check that a new version was not created:
        unit.refresh_from_db()
        assert content_api.get_unit(unit.id).versioning.draft == unit_version
        assert unit.versioning.draft == unit_version

        # Also check that `create_unit_and_version()` has the same restriction (not just `create_next_unit_version()`)
        with pytest.raises(ValidationError, match='The entity "unit-key2" cannot be added to a "unit" container.'):
            self.create_unit_with_components([unit2], container_code="unit-key3", title="Unit 3")

    def test_is_registered(self):
        assert Unit in content_api.get_all_container_subclasses()

    def test_olx_tag_name(self):
        assert content_api.get_container_subclass("unit") is Unit
        assert content_api.get_container_subclass("unit").olx_tag_name == "vertical"
