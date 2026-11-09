"""
Basic tests for the subsections API.
"""

from typing import cast

import pytest
from django.core.exceptions import ValidationError

import openedx_content.api as content_api
from openedx_content.models_api import Subsection, SubsectionVersion, Unit, UnitVersion

from ..components.test_api import ComponentTestCase

Entry = content_api.SubsectionListEntry


class SubsectionsTestCase(ComponentTestCase):
    """Test cases for Subsections (containers of units)"""

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
        self.unit_1, self.unit_1_v1 = content_api.create_unit_and_version(
            learning_package_id=self.learning_package.id,
            container_code="unit1",
            title="Unit 1",
            components=[self.component_1, self.component_2],
            created=self.now,
            created_by=None,
        )

    def create_subsection_with_units(
        self,
        units: list[Unit | UnitVersion],
        *,
        title="Subsection",
        container_code="subsection-key",
    ) -> Subsection:
        """Helper method to quickly create a unit with some units"""
        subsection, _subsection_v1 = content_api.create_subsection_and_version(
            learning_package_id=self.learning_package.id,
            container_code=container_code,
            title=title,
            units=units,
            created=self.now,
            created_by=None,
        )
        return subsection

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
            container_code="subsection-key",
            title="Subsection",
            created=self.now,
            created_by=None,
        )
        assert isinstance(subsection, Subsection)
        assert isinstance(subsection_version, SubsectionVersion)
        assert subsection, subsection_version
        assert subsection_version.version_num == 1
        assert subsection_version in subsection.versioning.versions.all()
        assert subsection.versioning.has_unpublished_changes
        assert subsection.versioning.draft == subsection_version
        assert subsection.versioning.published is None
        assert subsection.publishable_entity.can_stand_alone

    def test_create_next_subsection_version_with_unpinned_unit(self):
        """Test creating a subsection version with an unpinned unit.

        Expected results:
        1. A new subsection version is created.
        2. The subsection version number is 2.
        3. The subsection version is in the subsection's versions.
        4. The unit is in the draft subsection version's unit list and is unpinned.
        """
        subsection = self.create_subsection_with_units([])
        subsection_version_v2 = content_api.create_next_subsection_version(
            subsection,
            title="Subsection",
            units=[self.unit_1],
            created=self.now,
            created_by=None,
        )
        assert isinstance(subsection_version_v2, SubsectionVersion)
        assert subsection_version_v2.version_num == 2
        assert subsection_version_v2 in subsection.versioning.versions.all()
        assert content_api.get_units_in_subsection(subsection, published=False) == [
            Entry(self.unit_1_v1),
        ]
        with pytest.raises(SubsectionVersion.DoesNotExist):
            # There is no published version of the subsection:
            content_api.get_units_in_subsection(subsection, published=True)

    def test_get_subsection(self) -> None:
        """Test `get_subsection()`"""
        subsection = self.create_subsection_with_units([self.unit_1])

        subsection_retrieved = content_api.get_subsection(subsection.id)
        assert isinstance(subsection_retrieved, Subsection)
        assert subsection_retrieved == subsection

    def test_get_subsection_nonexistent(self) -> None:
        """Test `get_subsection()` when the subsection doesn't exist"""
        FAKE_ID = cast(Subsection.ID, -500)
        with pytest.raises(Subsection.DoesNotExist):
            content_api.get_subsection(FAKE_ID)

    def test_get_subsection_other_container_type(self) -> None:
        """Test `get_subsection()` when the provided PK is for a non-Subsection container"""
        with pytest.raises(Subsection.DoesNotExist):
            content_api.get_subsection(self.unit_1.id)  # type: ignore[arg-type]

    def test_subsection_queries(self) -> None:
        """
        Test the number of queries needed for each part of the subsections API
        """
        with self.assertNumQueries(43):
            subsection = self.create_subsection_with_units([self.unit_1, self.unit_1_v1])
        with self.assertNumQueries(57):
            content_api.publish_from_drafts(
                self.learning_package.id,
                draft_qset=content_api.get_all_drafts(self.learning_package.id).filter(entity=subsection.id),
            )
        with self.assertNumQueries(4):
            result = content_api.get_units_in_subsection(subsection, published=True)
        assert result == [
            Entry(self.unit_1_v1),
            Entry(self.unit_1_v1, pinned=True),
        ]

    def test_create_subsection_with_invalid_children(self):
        """
        Verify that only units can be added to subsections, and a specific exception is raised.
        """
        # Create a subsection:
        subsection = self.create_subsection_with_units([])
        subsection_version = subsection.versioning.draft

        # Try adding a Component to a Subsection
        with pytest.raises(
            ValidationError,
            match='The entity "xblock.v1:problem:Query_Counting" cannot be added to a "subsection" container.',
        ) as err:
            content_api.create_next_subsection_version(
                subsection,
                units=[self.component_1],
                created=self.now,
                created_by=None,
            )
        assert "(found non-Container child)" in str(err.value.__cause__)

        # Check that a new version was not created:
        subsection.refresh_from_db()
        assert content_api.get_subsection(subsection.id).versioning.draft == subsection_version
        assert subsection.versioning.draft == subsection_version

        # Also check that `create_subsection_with_units()` has the same restriction
        # (not just `create_next_subsection_version()`)
        with pytest.raises(
            ValidationError,
            match='The entity "xblock.v1:problem:Query_Counting" cannot be added to a "subsection" container.',
        ):
            self.create_subsection_with_units([self.component_1], container_code="unit-key3", title="Unit 3")

    def test_is_registered(self):
        assert Subsection in content_api.get_all_container_subclasses()

    def test_olx_tag_name(self):
        assert content_api.get_container_subclass("subsection") is Subsection
        assert content_api.get_container_subclass("subsection").olx_tag_name == "sequential"
