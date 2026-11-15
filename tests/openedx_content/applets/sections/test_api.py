"""
Basic tests for the sections API.
"""

from typing import Any, cast

import pytest
from django.core.exceptions import ValidationError

import openedx_content.api as content_api
from openedx_content.models_api import Section, SectionVersion, Subsection, SubsectionVersion

from ..components.test_api import ComponentTestCase

Entry = content_api.SectionListEntry


class SectionsTestCase(ComponentTestCase):
    """Test cases for Sections (containers of subsections)"""

    def setUp(self) -> None:
        """Create some potential desdendants for use in these tests."""
        super().setUp()
        self.component_1, self.component_1_v1 = self.create_component(
            component_code="component_1",
            title="Great-grandchild component",
        )
        self.component_2, self.component_2_v1 = self.create_component(
            component_code="component_2",
            title="Great-grandchild component",
        )
        common_args: dict[str, Any] = {
            "learning_package_id": self.learning_package.id,
            "created": self.now,
            "created_by": None,
        }
        self.unit_1, self.unit_1_v1 = content_api.create_unit_and_version(
            container_code="unit_1",
            title="Grandchild Unit 1",
            components=[self.component_1, self.component_2],
            **common_args,
        )
        self.unit_2, self.unit_2_v1 = content_api.create_unit_and_version(
            container_code="unit_2",
            title="Grandchild Unit 2",
            components=[self.component_2, self.component_1],  # Backwards order from Unit 1
            **common_args,
        )
        self.subsection_1, self.subsection_1_v1 = content_api.create_subsection_and_version(
            container_code="subsection_1",
            title="Child Subsection 1",
            units=[self.unit_1, self.unit_2],
            **common_args,
        )
        self.subsection_2, self.subsection_2_v1 = content_api.create_subsection_and_version(
            container_code="subsection_2",
            title="Child Subsection 2",
            units=[self.unit_2, self.unit_1],  # Backwards order from subsection 1
            **common_args,
        )

    def create_section_with_subsections(
        self,
        subsections: list[Subsection | SubsectionVersion],
        *,
        title="Section",
        container_code="section-key",
    ) -> Section:
        """Helper method to quickly create a section with some subsections"""
        section, _section_v1 = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            container_code=container_code,
            title=title,
            subsections=subsections,
            created=self.now,
            created_by=None,
        )
        return section

    def test_create_empty_section_and_version(self) -> None:
        """Test creating a section with no units.

        Expected results:
        1. A section and section version are created.
        2. The section version number is 1.
        3. The section is a draft with unpublished changes.
        4. There is no published version of the section.
        """
        section, section_version = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            container_code="section-key",
            title="Section",
            created=self.now,
            created_by=None,
        )
        assert isinstance(section, Section)
        assert isinstance(section_version, SectionVersion)
        assert section, section_version
        assert section_version.version_num == 1
        assert section_version in section.versioning.versions.all()
        assert section.versioning.has_unpublished_changes
        assert section.versioning.draft == section_version
        assert section.versioning.published is None
        assert section.publishable_entity.can_stand_alone

    def test_create_next_section_version_with_unpinned_subsections(self) -> None:
        """Test creating a unit version with an unpinned unit.

        Expected results:
        1. A new section version is created.
        2. The section version number is 2.
        3. The section version is in the section's versions.
        4. The unit is in the draft section version's subsection list and is unpinned.
        """
        section = self.create_section_with_subsections([])
        section_version_v2 = content_api.create_next_section_version(
            section,
            title="Section",
            subsections=[self.subsection_1],
            created=self.now,
            created_by=None,
        )
        assert isinstance(section_version_v2, SectionVersion)
        assert section_version_v2.version_num == 2
        assert section_version_v2 in section.versioning.versions.all()
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(self.subsection_1_v1),
        ]
        with pytest.raises(SectionVersion.DoesNotExist):
            # There is no published version of the subsection:
            content_api.get_subsections_in_section(section, published=True)

    def test_get_section(self) -> None:
        """Test `get_section()`"""
        section = self.create_section_with_subsections([self.subsection_1, self.subsection_2])

        section_retrieved = content_api.get_section(section.id)
        assert isinstance(section_retrieved, Section)
        assert section_retrieved == section

    def test_get_section_nonexistent(self) -> None:
        """Test `get_section()` when the subsection doesn't exist"""
        FAKE_ID = cast(Section.ID, -500)
        with pytest.raises(Section.DoesNotExist):
            content_api.get_section(FAKE_ID)

    def test_get_section_other_container_type(self) -> None:
        """Test `get_section()` when the provided ID is for a non-Subsection container"""
        with pytest.raises(Section.DoesNotExist):
            content_api.get_section(self.unit_1.id)  # type: ignore[arg-type]

    def test_section_queries(self) -> None:
        """
        Test the number of queries needed for each part of the sections API
        """
        with self.assertNumQueries(30):
            section = self.create_section_with_subsections([self.subsection_1, self.subsection_2_v1])
        with self.assertNumQueries(21):
            content_api.publish_from_drafts(
                self.learning_package.id,
                draft_qset=content_api.get_all_drafts(self.learning_package.id).filter(entity=section.id),
            )
        with self.assertNumQueries(4):
            result = content_api.get_subsections_in_section(section, published=True)
        assert result == [
            Entry(self.subsection_1_v1),
            Entry(self.subsection_2_v1, pinned=True),
        ]

    def test_create_section_with_invalid_children(self):
        """
        Verify that only subsections can be added to sections, and a specific exception is raised.
        """
        # Create a section:
        section = self.create_section_with_subsections([])
        section_version = section.versioning.draft
        # Try adding a Unit to a Section
        with pytest.raises(
            ValidationError,
            match='The entity "unit_1" cannot be added to a "section" container.',
        ):
            content_api.create_next_section_version(
                section,
                subsections=[self.unit_1],
                created=self.now,
                created_by=None,
            )
        # Check that a new version was not created:
        section.refresh_from_db()
        assert content_api.get_section(section.id).versioning.draft == section_version
        assert section.versioning.draft == section_version

        # Also check that `create_section_with_subsections()` has the same restriction
        # (not just `create_next_subsection_version()`)
        with pytest.raises(
            ValidationError,
            match='The entity "unit_1" cannot be added to a "section" container.',
        ):
            self.create_section_with_subsections([self.unit_1], container_code="unit-key3", title="Unit 3")

    def test_is_registered(self):
        assert Section in content_api.get_all_container_subclasses()

    def test_olx_tag_name(self):
        assert content_api.get_container_subclass("section") is Section
        assert content_api.get_container_subclass("section").olx_tag_name == "chapter"
