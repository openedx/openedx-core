"""
Basic tests for the subsections API.
"""
import ddt  # type: ignore[import]
import pytest
from django.core.exceptions import ValidationError

import openedx_content.api as content_api
from openedx_content import models_api as authoring_models

from ..subsections.test_api import SubSectionTestCase

Entry = content_api.SectionListEntry


# TODO: Turn SubSectionTestCase into SubSectionTestMixin and remove the
# test-inherits-tests pylint warning below.
# https://github.com/openedx/openedx-core/issues/308
@ddt.ddt
class SectionTestCase(SubSectionTestCase):  # pylint: disable=test-inherits-tests
    """ Test cases for Sections (containers of subsections) """

    def setUp(self) -> None:
        super().setUp()
        self.subsection_1, self.subsection_1_v1 = self.create_subsection(
            key="Subsection (1)",
            title="Subsection (1)",
        )
        self.subsection_2, self.subsection_2_v1 = self.create_subsection(
            key="Subsection (2)",
            title="Subsection (2)",
        )

    def create_subsection(self, *, title: str = "Test Subsection", key: str = "subsection:1") -> tuple[
        authoring_models.Subsection, authoring_models.SubsectionVersion
    ]:
        """ Helper method to quickly create a subsection """
        return content_api.create_subsection_and_version(
            self.learning_package.id,
            key=key,
            title=title,
            created=self.now,
            created_by=None,
        )

    def create_section_with_subsections(
        self,
        subsections: list[authoring_models.Subsection | authoring_models.SubsectionVersion],
        *,
        title="Subsection",
        key="subsection:key",
    ) -> authoring_models.Section:
        """ Helper method to quickly create a section with some subsections """
        section, _section_v1 = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            key=key,
            title=title,
            subsections=subsections,
            created=self.now,
            created_by=None,
        )
        return section

    def modify_subsection(
        self,
        subsection: authoring_models.Subsection,
        *,
        title="Modified Subsection",
        timestamp=None,
    ) -> authoring_models.SubsectionVersion:
        """
        Helper method to modify a subsection for the purposes of testing subsections/drafts/pinning/publishing/etc.
        """
        return content_api.create_next_subsection_version(
            subsection,
            title=title,
            created=timestamp or self.now,
            created_by=None,
        )

    def publish_subsection(self, subsection: authoring_models.Subsection):
        """
        Helper method to publish a single subsection.
        """
        content_api.publish_from_drafts(
            self.learning_package.pk,
            draft_qset=content_api.get_all_drafts(self.learning_package.pk).filter(
                entity=subsection.publishable_entity,
            ),
        )

    def test_get_section(self):
        """
        Test get_section()
        """
        section = self.create_section_with_subsections([self.subsection_1, self.subsection_2])
        with self.assertNumQueries(1):
            result = content_api.get_section(section.pk)
        assert result == section
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result.versioning.has_unpublished_changes

    def test_get_section_version(self):
        """
        Test get_section_version()
        """
        section = self.create_section_with_subsections([])
        draft = section.versioning.draft
        with self.assertNumQueries(1):
            result = content_api.get_section_version(draft.pk)
        assert result == draft

    def test_get_latest_section_version(self):
        """
        Test test_get_latest_section_version()
        """
        section = self.create_section_with_subsections([])
        draft = section.versioning.draft
        with self.assertNumQueries(2):
            result = content_api.get_latest_section_version(section.pk)
        assert result == draft

    def test_get_containers(self):
        """
        Test get_containers()
        """
        section = self.create_section_with_subsections([])
        with self.assertNumQueries(1):
            result = list(content_api.get_containers(self.learning_package.id))
        self.assertCountEqual(result, [
            self.unit_1.container,
            self.unit_2.container,
            self.subsection_1.container,
            self.subsection_2.container,
            section.container,
        ])
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result[0].versioning.has_unpublished_changes

    def test_get_containers_deleted(self):
        """
        Test that get_containers() does not return soft-deleted sections.
        """
        section = self.create_section_with_subsections([])
        content_api.soft_delete_draft(section.pk)

        with self.assertNumQueries(1):
            result = list(content_api.get_containers(self.learning_package.id, include_deleted=True))

        assert result == [
            self.unit_1.container,
            self.unit_2.container,
            self.subsection_1.container,
            self.subsection_2.container,
            section.container,
        ]

        with self.assertNumQueries(1):
            result = list(content_api.get_containers(self.learning_package.id))

        assert result == [
            self.unit_1.container,
            self.unit_2.container,
            self.subsection_1.container,
            self.subsection_2.container,
        ]

    def test_get_container(self):
        """
        Test get_container()
        """
        section = self.create_section_with_subsections([self.subsection_1, self.subsection_2])
        with self.assertNumQueries(1):
            result = content_api.get_container(section.pk)
        assert result == section.container
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result.versioning.has_unpublished_changes

    def test_get_container_by_key(self):
        """
        Test get_container_by_key()
        """
        section = self.create_section_with_subsections([])
        with self.assertNumQueries(1):
            result = content_api.get_container_by_key(
                self.learning_package.id,
                key=section.publishable_entity.key,
            )
        assert result == section.container
        # Versioning data should be pre-loaded via select_related()
        with self.assertNumQueries(0):
            assert result.versioning.has_unpublished_changes

    def test_section_container_versioning(self):
        """
        Test that the .versioning helper of a Sebsection returns a SectionVersion, and
        same for the generic Container equivalent.
        """
        section = self.create_section_with_subsections([self.subsection_1, self.subsection_2])
        container = section.container
        container_version = container.versioning.draft
        assert isinstance(container_version, authoring_models.ContainerVersion)
        section_version = section.versioning.draft
        assert isinstance(section_version, authoring_models.SectionVersion)
        assert section_version.container_version == container_version
        assert section_version.container_version.container == container
        assert section_version.section == section

    def test_create_section_queries(self):
        """
        Test how many database queries are required to create a section
        """
        # The exact numbers here aren't too important - this is just to alert us if anything significant changes.
        with self.assertNumQueries(28):
            _empty_section = self.create_section_with_subsections([])
        with self.assertNumQueries(35):
            # And try with a non-empty section:
            self.create_section_with_subsections([self.subsection_1, self.subsection_2_v1], key="u2")

    def test_create_section_with_invalid_children(self):
        """
        Verify that only subsections can be added to sections, and a specific
        exception is raised.
        """
        # Create two sections:
        section, section_version = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            key="section:key",
            title="Section",
            created=self.now,
            created_by=None,
        )
        assert section.versioning.draft == section_version
        section2, _s2v1 = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            key="section:key2",
            title="Section 2",
            created=self.now,
            created_by=None,
        )
        # Try adding a Section to a Section
        with pytest.raises(TypeError, match="Section subsections must be either Subsection or SubsectionVersion."):
            content_api.create_next_section_version(
                section=section,
                title="Section Containing a Section",
                subsections=[section2],
                created=self.now,
                created_by=None,
            )
        # Check that a new version was not created:
        section.refresh_from_db()
        assert content_api.get_section(section.pk).versioning.draft == section_version
        assert section.versioning.draft == section_version

    def test_adding_external_subsections(self):
        """
        Test that subsections from another learning package cannot be added to a
        section.
        """
        learning_package2 = content_api.create_learning_package(key="other-package", title="Other Package")
        section, _section_version = content_api.create_section_and_version(
            learning_package_id=learning_package2.pk,
            key="section:key",
            title="Section",
            created=self.now,
            created_by=None,
        )
        assert self.subsection_1.container.publishable_entity.learning_package != learning_package2
        # Try adding a a subsection from LP 1 (self.learning_package) to a section from LP 2
        with pytest.raises(ValidationError, match="Container entities must be from the same learning package."):
            content_api.create_next_section_version(
                section=section,
                title="Section Containing an External Subsection",
                subsections=[self.subsection_1],
                created=self.now,
                created_by=None,
            )

    def test_create_empty_section_and_version(self):
        """Test creating a section with no subsections.

        Expected results:
        1. A section and section version are created.
        2. The section version number is 1.
        3. The section is a draft with unpublished changes.
        4. There is no published version of the section.
        """
        section, section_version = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            key="section:key",
            title="Section",
            created=self.now,
            created_by=None,
        )
        assert section, section_version
        assert section_version.version_num == 1
        assert section_version in section.versioning.versions.all()
        assert section.versioning.has_unpublished_changes
        assert section.versioning.draft == section_version
        assert section.versioning.published is None
        assert section.publishable_entity.can_stand_alone

    def test_create_next_section_version_with_two_unpinned_subsections(self):
        """Test creating a section version with two unpinned subsections.

        Expected results:
        1. A new section version is created.
        2. The section version number is 2.
        3. The section version is in the section's versions.
        4. The subsections are in the draft section version's subsection list and are unpinned.
        """
        section, _section_version = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            key="section:key",
            title="Section",
            created=self.now,
            created_by=None,
        )
        section_version_v2 = content_api.create_next_section_version(
            section=section,
            title="Section",
            subsections=[self.subsection_1, self.subsection_2],
            created=self.now,
            created_by=None,
        )
        assert section_version_v2.version_num == 2
        assert section_version_v2 in section.versioning.versions.all()
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(self.subsection_1.versioning.draft),
            Entry(self.subsection_2.versioning.draft),
        ]
        with pytest.raises(authoring_models.ContainerVersion.DoesNotExist):
            # There is no published version of the section:
            content_api.get_subsections_in_section(section, published=True)

    def test_create_next_section_version_with_unpinned_and_pinned_subsections(self):
        """
        Test creating a section version with one unpinned and one pinned 📌 subsection.
        """
        section, _section_version = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            key="section:key",
            title="Section",
            created=self.now,
            created_by=None,
        )
        section_version_v2 = content_api.create_next_section_version(
            section=section,
            title="Section",
            subsections=[
                self.subsection_1,
                self.subsection_2_v1
            ],  # Note the "v1" pinning 📌 the second one to version 1
            created=self.now,
            created_by=None,
        )
        assert section_version_v2.version_num == 2
        assert section_version_v2 in section.versioning.versions.all()
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(self.subsection_1_v1),
            Entry(self.subsection_2_v1, pinned=True),  # Pinned 📌 to v1
        ]
        with pytest.raises(authoring_models.ContainerVersion.DoesNotExist):
            # There is no published version of the section:
            content_api.get_subsections_in_section(section, published=True)

    def test_create_next_section_version_forcing_version_num(self):
        """
        Test creating a section version while forcing the next version number.
        """
        section, _section_version = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            key="section:key",
            title="Section",
            created=self.now,
            created_by=None,
        )
        section_version_v2 = content_api.create_next_section_version(
            section=section,
            title="Section",
            subsections=[self.subsection_1, self.subsection_2],
            created=self.now,
            created_by=None,
            force_version_num=5,  # Forcing the next version number to be 5 (instead of the usual 2)
        )
        assert section_version_v2.version_num == 5

    def test_auto_publish_children(self):
        """
        Test that publishing a section publishes its child subsections automatically.
        """
        # Create a draft section with two draft subsections
        section = self.create_section_with_subsections([self.subsection_1, self.subsection_2])
        # Also create another subsection that's not in the section at all:
        other_subsection, _os_v1 = self.create_subsection(
            title="A draft subsection not in the section", key="subsection:3"
        )

        assert content_api.contains_unpublished_changes(section.pk)
        assert self.subsection_1.versioning.published is None
        assert self.subsection_2.versioning.published is None

        # Publish ONLY the section. This should however also auto-publish subsections 1 & 2 since they're children
        content_api.publish_from_drafts(
            self.learning_package.pk,
            draft_qset=content_api.get_all_drafts(self.learning_package.pk).filter(entity=section.publishable_entity),
        )
        # Now all changes to the section and to subsection 1 are published:
        section.refresh_from_db()
        self.subsection_1.refresh_from_db()
        assert section.versioning.has_unpublished_changes is False  # Shallow check
        assert self.subsection_1.versioning.has_unpublished_changes is False
        assert content_api.contains_unpublished_changes(section.pk) is False  # Deep check
        assert self.subsection_1.versioning.published == self.subsection_1_v1  # v1 is now the published version.

        # But our other subsection that's outside the section is not affected:
        other_subsection.refresh_from_db()
        assert other_subsection.versioning.has_unpublished_changes
        assert other_subsection.versioning.published is None

    def test_no_publish_parent(self):
        """
        Test that publishing a subsection does NOT publish changes to its parent section
        """
        # Create a draft section with two draft subsections
        section = self.create_section_with_subsections([self.subsection_1, self.subsection_2])
        assert section.versioning.has_unpublished_changes
        # Publish ONLY one of its child subsections
        self.publish_subsection(self.subsection_1)
        self.subsection_1.refresh_from_db()  # Clear cache on '.versioning'
        assert self.subsection_1.versioning.has_unpublished_changes is False

        # The section that contains that subsection should still be unpublished:
        section.refresh_from_db()  # Clear cache on '.versioning'
        assert section.versioning.has_unpublished_changes
        assert section.versioning.published is None
        with pytest.raises(authoring_models.ContainerVersion.DoesNotExist):
            # There is no published version of the section:
            content_api.get_subsections_in_section(section, published=True)

    def test_add_subsection_after_publish(self):
        """
        Adding a subsection to a published section will create a new version and
        show that the section has unpublished changes.
        """
        section, section_version = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            key="section:key",
            title="Section",
            created=self.now,
            created_by=None,
        )
        assert section.versioning.draft == section_version
        assert section.versioning.published is None
        assert section.versioning.has_unpublished_changes
        # Publish the empty section:
        content_api.publish_all_drafts(self.learning_package.id)
        section.refresh_from_db()  # Reloading the section is necessary
        assert section.versioning.has_unpublished_changes is False  # Shallow check for the section itself, not children
        assert content_api.contains_unpublished_changes(section.pk) is False  # Deeper check

        # Add a published subsection (unpinned):
        assert self.subsection_1.versioning.has_unpublished_changes is False
        section_version_v2 = content_api.create_next_section_version(
            section=section,
            title=section_version.title,
            subsections=[self.subsection_1],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        # Now the section should have unpublished changes:
        section.refresh_from_db()  # Reloading the section is necessary
        assert section.versioning.has_unpublished_changes  # Shallow check - adding a child is a change to the section
        assert content_api.contains_unpublished_changes(section.pk)  # Deeper check
        assert section.versioning.draft == section_version_v2
        assert section.versioning.published == section_version

    def test_modify_unpinned_subsection_after_publish(self):
        """
        Modifying an unpinned subsection in a published section will NOT create a
        new version nor show that the section has unpublished changes (but it will
        "contain" unpublished changes). The modifications will appear in the
        published version of the section only after the subsection is published.
        """
        # Create a section with one unpinned draft subsection:
        assert self.subsection_1.versioning.has_unpublished_changes
        section = self.create_section_with_subsections([self.subsection_1])
        assert section.versioning.has_unpublished_changes

        # Publish the section and the subsection:
        content_api.publish_all_drafts(self.learning_package.id)
        section.refresh_from_db()  # Reloading the section is necessary if we accessed 'versioning' before publish
        self.subsection_1.refresh_from_db()
        assert section.versioning.has_unpublished_changes is False  # Shallow check
        assert content_api.contains_unpublished_changes(section.pk) is False  # Deeper check
        assert self.subsection_1.versioning.has_unpublished_changes is False

        # Now modify the subsection by changing its title (it remains a draft):
        subsection_1_v2 = self.modify_subsection(self.subsection_1, title="Modified Counting Problem with new title")

        # The subsection now has unpublished changes; the section doesn't directly but does contain
        section.refresh_from_db()  # Reloading the section is necessary, or 'section.versioning' will be outdated
        self.subsection_1.refresh_from_db()
        assert section.versioning.has_unpublished_changes is False  # Shallow check should be false - section unchanged
        assert content_api.contains_unpublished_changes(section.pk)  # But section DOES contain changes
        assert self.subsection_1.versioning.has_unpublished_changes

        # Since the subsection changes haven't been published, they should only appear in the draft section
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(subsection_1_v2),  # new version
        ]
        assert content_api.get_subsections_in_section(section, published=True) == [
            Entry(self.subsection_1_v1),  # old version
        ]

        # But if we publish the subsection, the changes will appear in the published version of the section.
        self.publish_subsection(self.subsection_1)
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(subsection_1_v2),  # new version
        ]
        assert content_api.get_subsections_in_section(section, published=True) == [
            Entry(subsection_1_v2),  # new version
        ]
        assert content_api.contains_unpublished_changes(section.pk) is False  # No longer contains unpublished changes

    def test_modify_pinned_subsection(self):
        """
        When a pinned 📌 subsection in section is modified and/or published, it will
        have no effect on either the draft nor published version of the section,
        which will continue to use the pinned version.
        """
        # Create a section with one subsection (pinned 📌 to v1):
        section = self.create_section_with_subsections([self.subsection_1_v1])

        # Publish the section and the subsection:
        content_api.publish_all_drafts(self.learning_package.id)
        expected_section_contents = [
            Entry(self.subsection_1_v1, pinned=True),  # pinned 📌 to v1
        ]
        assert content_api.get_subsections_in_section(section, published=True) == expected_section_contents

        # Now modify the subsection by changing its title (it remains a draft):
        self.modify_subsection(self.subsection_1, title="Modified Counting Problem with new title")

        # The subsection now has unpublished changes; the section is entirely unaffected
        section.refresh_from_db()  # Reloading the section is necessary, or 'section.versioning' will be outdated
        self.subsection_1.refresh_from_db()
        assert section.versioning.has_unpublished_changes is False  # Shallow check
        assert content_api.contains_unpublished_changes(section.pk) is False  # Deep check
        assert self.subsection_1.versioning.has_unpublished_changes is True

        # Neither the draft nor the published version of the section is affected
        assert content_api.get_subsections_in_section(section, published=False) == expected_section_contents
        assert content_api.get_subsections_in_section(section, published=True) == expected_section_contents
        # Even if we publish the subsection, the section stays pinned to the specified version:
        self.publish_subsection(self.subsection_1)
        assert content_api.get_subsections_in_section(section, published=False) == expected_section_contents
        assert content_api.get_subsections_in_section(section, published=True) == expected_section_contents

    def test_create_two_sections_with_same_subsections(self):
        """
        Test creating two sections with different combinations of the same two
        subsections in each section.
        """
        # Create a section with subsection 2 unpinned, subsection 2 pinned 📌, and subsection 1:
        section1 = self.create_section_with_subsections(
            [self.subsection_2, self.subsection_2_v1, self.subsection_1], key="u1"
        )
        # Create a second section with subsection 1 pinned 📌, subsection 2, and subsection 1 unpinned:
        section2 = self.create_section_with_subsections(
            [self.subsection_1_v1, self.subsection_2, self.subsection_1], key="u2"
        )

        # Check that the contents are as expected:
        assert [
            row.subsection_version for row in content_api.get_subsections_in_section(section1, published=False)
        ] == [self.subsection_2_v1, self.subsection_2_v1, self.subsection_1_v1,]
        assert [
            row.subsection_version for row in content_api.get_subsections_in_section(section2, published=False)
        ] == [self.subsection_1_v1, self.subsection_2_v1, self.subsection_1_v1,]

        # Modify subsection 1
        subsection_1_v2 = self.modify_subsection(self.subsection_1, title="subsection 1 v2")
        # Publish changes
        content_api.publish_all_drafts(self.learning_package.id)
        # Modify subsection 2 - only in the draft
        subsection_2_v2 = self.modify_subsection(self.subsection_2, title="subsection 2 DRAFT")

        # Check that the draft contents are as expected:
        assert content_api.get_subsections_in_section(section1, published=False) == [
            Entry(subsection_2_v2),  # v2 in the draft version
            Entry(self.subsection_2_v1, pinned=True),  # pinned 📌 to v1
            Entry(subsection_1_v2),  # v2
        ]
        assert content_api.get_subsections_in_section(section2, published=False) == [
            Entry(self.subsection_1_v1, pinned=True),  # pinned 📌 to v1
            Entry(subsection_2_v2),  # v2 in the draft version
            Entry(subsection_1_v2),  # v2
        ]

        # Check that the published contents are as expected:
        assert content_api.get_subsections_in_section(section1, published=True) == [
            Entry(self.subsection_2_v1),  # v1 in the published version
            Entry(self.subsection_2_v1, pinned=True),  # pinned 📌 to v1
            Entry(subsection_1_v2),  # v2
        ]
        assert content_api.get_subsections_in_section(section2, published=True) == [
            Entry(self.subsection_1_v1, pinned=True),  # pinned 📌 to v1
            Entry(self.subsection_2_v1),  # v1 in the published version
            Entry(subsection_1_v2),  # v2
        ]

    def test_publishing_shared_subsection(self):
        """
        A complex test case involving two sections with a shared subsection and
        other non-shared subsections.

        Section 1: subsections C1, C2, C3
        Section 2: subsections C2, C4, C5
        Everything is "unpinned".
        """
        # 1️⃣ Create the sections and publish them:
        (s1, s1_v1), (s2, _s2_v1), (s3, s3_v1), (s4, s4_v1), (s5, s5_v1) = [
            self.create_subsection(key=f"C{i}", title=f"Subsection {i}") for i in range(1, 6)
        ]
        section1 = self.create_section_with_subsections([s1, s2, s3], title="Section 1", key="section:1")
        section2 = self.create_section_with_subsections([s2, s4, s5], title="Section 2", key="section:2")
        content_api.publish_all_drafts(self.learning_package.id)
        assert content_api.contains_unpublished_changes(section1.pk) is False
        assert content_api.contains_unpublished_changes(section2.pk) is False

        # 2️⃣ Then the author edits S2 inside of Section 1 making S2v2.
        s2_v2 = self.modify_subsection(s2, title="U2 version 2")
        # This makes S1, S2 both show up as Sections that CONTAIN unpublished changes, because they share the subsection
        assert content_api.contains_unpublished_changes(section1.pk)
        assert content_api.contains_unpublished_changes(section2.pk)
        # (But the sections themselves are unchanged:)
        section1.refresh_from_db()
        section2.refresh_from_db()
        assert section1.versioning.has_unpublished_changes is False
        assert section2.versioning.has_unpublished_changes is False

        # 3️⃣ In addition to this, the author also modifies another subsection in Section 2 (U5)
        s5_v2 = self.modify_subsection(s5, title="S5 version 2")

        # 4️⃣ The author then publishes Section 1, and therefore everything in it.
        content_api.publish_from_drafts(
            self.learning_package.pk,
            draft_qset=content_api.get_all_drafts(self.learning_package.pk).filter(
                # Note: we only publish the section; the publishing API should auto-publish its subsections too.
                entity_id=section1.publishable_entity.id,
            ),
        )

        # Result: Section 1 will show the newly published version of U2:
        assert content_api.get_subsections_in_section(section1, published=True) == [
            Entry(s1_v1),
            Entry(s2_v2),  # new published version of U2
            Entry(s3_v1),
        ]

        # Result: someone looking at Section 2 should see the newly published subsection 2,
        # because publishing it anywhere publishes it everywhere.
        # But publishing U2 and Section 1 does not affect the other subsections in Section 2.
        # (Publish propagates downward, not upward)
        assert content_api.get_subsections_in_section(section2, published=True) == [
            Entry(s2_v2),  # new published version of U2
            Entry(s4_v1),  # still original version of U4 (it was never modified)
            Entry(s5_v1),  # still original version of U5 (it hasn't been published)
        ]

        # Result: Section 2 CONTAINS unpublished changes because of the modified U5.
        # Section 1 doesn't contain unpub changes.
        assert content_api.contains_unpublished_changes(section1.pk) is False
        assert content_api.contains_unpublished_changes(section2.pk)

        # 5️⃣ Publish subsection U5, which should be the only thing unpublished in the learning package
        self.publish_subsection(s5)
        # Result: Section 2 shows the new version of C5 and no longer contains unpublished changes:
        assert content_api.get_subsections_in_section(section2, published=True) == [
            Entry(s2_v2),  # new published version of U2
            Entry(s4_v1),  # still original version of U4 (it was never modified)
            Entry(s5_v2),  # new published version of U5
        ]
        assert content_api.contains_unpublished_changes(section2.pk) is False

    def test_query_count_of_contains_unpublished_changes(self):
        """
        Checking for unpublished changes in a section should require a fixed number
        of queries, not get more expensive as the section gets larger.
        """
        # Add 2 subsections (unpinned)
        subsection_count = 2
        subsections = []
        for i in range(subsection_count):
            subsection, _version = self.create_subsection(
                key=f"Subsection {i}",
                title=f"Subsection {i}",
            )
            subsections.append(subsection)
        section = self.create_section_with_subsections(subsections)
        content_api.publish_all_drafts(self.learning_package.id)
        section.refresh_from_db()
        with self.assertNumQueries(1):
            assert content_api.contains_unpublished_changes(section.pk) is False

        # Modify the most recently created subsection:
        self.modify_subsection(subsection, title="Modified Subsection")
        with self.assertNumQueries(1):
            assert content_api.contains_unpublished_changes(section.pk) is True

    def test_metadata_change_doesnt_create_entity_list(self):
        """
        Test that changing a container's metadata like title will create a new
        version, but can re-use the same EntityList. API consumers generally
        shouldn't depend on this behavior; it's an optimization.
        """
        section = self.create_section_with_subsections([self.subsection_1, self.subsection_2_v1])

        orig_version_num = section.versioning.draft.version_num
        orig_entity_list_id = section.versioning.draft.entity_list.pk

        content_api.create_next_section_version(section, title="New Title", created=self.now)

        section.refresh_from_db()
        new_version_num = section.versioning.draft.version_num
        new_entity_list_id = section.versioning.draft.entity_list.pk

        assert new_version_num > orig_version_num
        assert new_entity_list_id == orig_entity_list_id

    def test_removing_subsection(self):
        """ Test removing a subsection from a section (but not deleting it) """
        section = self.create_section_with_subsections([self.subsection_1, self.subsection_2])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now remove subsection 2
        content_api.create_next_section_version(
            section=section,
            title="Revised with subsection 2 deleted",
            subsections=[self.subsection_2],
            created=self.now,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )

        # Now it should not be listed in the section:
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(self.subsection_1_v1),
        ]
        section.refresh_from_db()
        assert section.versioning.has_unpublished_changes  # The section itself and its subsection list have change
        assert content_api.contains_unpublished_changes(section.pk)
        # The published version of the section is not yet affected:
        assert content_api.get_subsections_in_section(section, published=True) == [
            Entry(self.subsection_1_v1),
            Entry(self.subsection_2_v1),
        ]

        # But when we publish the new section version with the removal, the published version is affected:
        content_api.publish_all_drafts(self.learning_package.id)
        # FIXME: Refreshing the section is necessary here because get_entities_in_published_container() accesses
        # container.versioning.published, and .versioning is cached with the old version. But this seems like
        # a footgun? We could avoid this if get_entities_in_published_container() took only an ID instead of an object,
        # but that would involve additional database lookup(s).
        section.refresh_from_db()
        assert content_api.contains_unpublished_changes(section.pk) is False
        assert content_api.get_subsections_in_section(section, published=True) == [
            Entry(self.subsection_1_v1),
        ]

    def test_soft_deleting_subsection(self):
        """ Test soft deleting a subsection that's in a section (but not removing it) """
        section = self.create_section_with_subsections([self.subsection_1, self.subsection_2])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now soft delete subsection 2
        content_api.soft_delete_draft(self.subsection_2.pk)

        # Now it should not be listed in the section:
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(self.subsection_1_v1),
            # subsection 2 is soft deleted from the draft.
            # TODO: should we return some kind of placeholder here, to indicate that a subsection is still listed in the
            # section's subsection list but has been soft deleted, and will be fully deleted when published,
            # or restored if reverted?
        ]
        assert section.versioning.has_unpublished_changes is False  # The section and its subsection list is not changed
        assert content_api.contains_unpublished_changes(section.pk)  # But it CONTAINS unpublished change (deletion)
        # The published version of the section is not yet affected:
        assert content_api.get_subsections_in_section(section, published=True) == [
            Entry(self.subsection_1_v1),
            Entry(self.subsection_2_v1),
        ]

        # But when we publish the deletion, the published version is affected:
        content_api.publish_all_drafts(self.learning_package.id)
        assert content_api.contains_unpublished_changes(section.pk) is False
        assert content_api.get_subsections_in_section(section, published=True) == [
            Entry(self.subsection_1_v1),
        ]

    def test_soft_deleting_and_removing_subsection(self):
        """ Test soft deleting a subsection that's in a section AND removing it """
        section = self.create_section_with_subsections([self.subsection_1, self.subsection_2])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now soft delete subsection 2
        content_api.soft_delete_draft(self.subsection_2.pk)
        # And remove it from the section:
        content_api.create_next_section_version(
            section=section,
            title="Revised with subsection 2 deleted",
            subsections=[self.subsection_2],
            created=self.now,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )

        # Now it should not be listed in the section:
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(self.subsection_1_v1),
        ]
        assert section.versioning.has_unpublished_changes is True
        assert content_api.contains_unpublished_changes(section.pk)
        # The published version of the section is not yet affected:
        assert content_api.get_subsections_in_section(section, published=True) == [
            Entry(self.subsection_1_v1),
            Entry(self.subsection_2_v1),
        ]

        # But when we publish the deletion, the published version is affected:
        content_api.publish_all_drafts(self.learning_package.id)
        assert content_api.contains_unpublished_changes(section.pk) is False
        assert content_api.get_subsections_in_section(section, published=True) == [
            Entry(self.subsection_1_v1),
        ]

    def test_soft_deleting_pinned_subsection(self):
        """ Test soft deleting a pinned 📌 subsection that's in a section """
        section = self.create_section_with_subsections([self.subsection_1_v1, self.subsection_2_v1])
        content_api.publish_all_drafts(self.learning_package.id)

        # Now soft delete subsection 2
        content_api.soft_delete_draft(self.subsection_2.pk)

        # Now it should still be listed in the section:
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(self.subsection_1_v1, pinned=True),
            Entry(self.subsection_2_v1, pinned=True),
        ]
        assert section.versioning.has_unpublished_changes is False  # The section and its subsection list is not changed
        assert content_api.contains_unpublished_changes(section.pk) is False  # nor does it contain changes
        # The published version of the section is also not affected:
        assert content_api.get_subsections_in_section(section, published=True) == [
            Entry(self.subsection_1_v1, pinned=True),
            Entry(self.subsection_2_v1, pinned=True),
        ]

    def test_soft_delete_section(self):
        """
        I can delete a section without deleting the subsections it contains.

        See https://github.com/openedx/frontend-app-authoring/issues/1693
        """
        # Create two sections, one of which we will soon delete:
        section_to_delete = self.create_section_with_subsections([self.subsection_1, self.subsection_2])
        other_section = self.create_section_with_subsections([self.subsection_1], key="other")

        # Publish everything:
        content_api.publish_all_drafts(self.learning_package.id)
        # Delete the section:
        content_api.soft_delete_draft(section_to_delete.publishable_entity_id)
        section_to_delete.refresh_from_db()
        # Now draft section is [soft] deleted, but the subsections, published section, and other section is unaffected:
        assert section_to_delete.versioning.draft is None  # Section is soft deleted.
        assert section_to_delete.versioning.published is not None
        self.subsection_1.refresh_from_db()
        assert self.subsection_1.versioning.draft is not None
        assert content_api.get_subsections_in_section(other_section, published=False) == [Entry(self.subsection_1_v1)]

        # Publish everything:
        content_api.publish_all_drafts(self.learning_package.id)
        # Now the section's published version is also deleted, but nothing else is affected.
        section_to_delete.refresh_from_db()
        assert section_to_delete.versioning.draft is None  # Section is soft deleted.
        assert section_to_delete.versioning.published is None
        self.subsection_1.refresh_from_db()
        assert self.subsection_1.versioning.draft is not None
        assert self.subsection_1.versioning.published is not None
        assert content_api.get_subsections_in_section(other_section, published=False) == [Entry(self.subsection_1_v1)]
        assert content_api.get_subsections_in_section(other_section, published=True) == [Entry(self.subsection_1_v1)]

    def test_snapshots_of_published_section(self):
        """
        Test that we can access snapshots of the historic published version of
        sections and their contents.
        """
        # At first the section has one subsection (unpinned):
        section = self.create_section_with_subsections([self.subsection_1])
        self.modify_subsection(self.subsection_1, title="Subsection 1 as of checkpoint 1")
        before_publish = content_api.get_subsections_in_published_section_as_of(section, 0)
        assert before_publish is None

        # Publish everything, creating Checkpoint 1
        checkpoint_1 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 1")

        ########################################################################

        # Now we update the title of the subsection.
        self.modify_subsection(self.subsection_1, title="Subsection 1 as of checkpoint 2")
        # Publish everything, creating Checkpoint 2
        checkpoint_2 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 2")
        ########################################################################

        # Now add a second subsection to the section:
        self.modify_subsection(self.subsection_1, title="Subsection 1 as of checkpoint 3")
        self.modify_subsection(self.subsection_2, title="Subsection 2 as of checkpoint 3")
        content_api.create_next_section_version(
            section=section,
            title="Section title in checkpoint 3",
            subsections=[self.subsection_1, self.subsection_2],
            created=self.now,
        )
        # Publish everything, creating Checkpoint 3
        checkpoint_3 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 3")
        ########################################################################

        # Now add a third subsection to the section, a pinned 📌 version of subsection 1.
        # This will test pinned versions and also test adding at the beginning rather than the end of the section.
        content_api.create_next_section_version(
            section=section,
            title="Section title in checkpoint 4",
            subsections=[self.subsection_1_v1, self.subsection_1, self.subsection_2],
            created=self.now,
        )
        # Publish everything, creating Checkpoint 4
        checkpoint_4 = content_api.publish_all_drafts(self.learning_package.id, message="checkpoint 4")
        ########################################################################

        # Modify the drafts, but don't publish:
        self.modify_subsection(self.subsection_1, title="Subsection 1 draft")
        self.modify_subsection(self.subsection_2, title="Subsection 2 draft")

        # Now fetch the snapshots:
        as_of_checkpoint_1 = content_api.get_subsections_in_published_section_as_of(section, checkpoint_1.pk)
        assert [cv.subsection_version.title for cv in as_of_checkpoint_1] == [
            "Subsection 1 as of checkpoint 1",
        ]
        as_of_checkpoint_2 = content_api.get_subsections_in_published_section_as_of(section, checkpoint_2.pk)
        assert [cv.subsection_version.title for cv in as_of_checkpoint_2] == [
            "Subsection 1 as of checkpoint 2",
        ]
        as_of_checkpoint_3 = content_api.get_subsections_in_published_section_as_of(section, checkpoint_3.pk)
        assert [cv.subsection_version.title for cv in as_of_checkpoint_3] == [
            "Subsection 1 as of checkpoint 3",
            "Subsection 2 as of checkpoint 3",
        ]
        as_of_checkpoint_4 = content_api.get_subsections_in_published_section_as_of(section, checkpoint_4.pk)
        assert [cv.subsection_version.title for cv in as_of_checkpoint_4] == [
            "Subsection (1)",  # Pinned. This title is self.subsection_1_v1.title (original v1 title)
            "Subsection 1 as of checkpoint 3",  # we didn't modify these subsections so they're same as in snapshot 3
            "Subsection 2 as of checkpoint 3",  # we didn't modify these subsections so they're same as in snapshot 3
        ]

    def test_sections_containing(self):
        """
        Test that we can efficiently get a list of all the [draft] sections
        containing a given subsection.
        """
        subsection_1_v2 = self.modify_subsection(self.subsection_1, title="modified subsection 1")

        # Create a few sections, some of which contain subsection 1 and others which don't:
        # Note: it is important that some of these sections contain other subsections, to ensure complex JOINs required
        # for this query are working correctly, especially in the case of ignore_pinned=True.
        # Section 1 ✅ has subsection 1, pinned 📌 to V1
        section1_1pinned = self.create_section_with_subsections([self.subsection_1_v1, self.subsection_2], key="s1")
        # Section 2 ✅ has subsection 1, pinned 📌 to V2
        section2_1pinned_v2 = self.create_section_with_subsections([subsection_1_v2, self.subsection_2_v1], key="s2")
        # Section 3 doesn't contain it
        _section3_no = self.create_section_with_subsections([self.subsection_2], key="s3")
        # Section 4 ✅ has subsection 1, unpinned
        section4_unpinned = self.create_section_with_subsections([
            self.subsection_1, self.subsection_2, self.subsection_2_v1,
        ], key="s4")
        # Sections 5/6 don't contain it
        _section5_no = self.create_section_with_subsections([self.subsection_2_v1, self.subsection_2], key="s5")
        _section6_no = self.create_section_with_subsections([], key="s6")

        # No need to publish anything as the get_containers_with_entity() API only considers drafts (for now).

        with self.assertNumQueries(1):
            result = [
                c.section for c in
                content_api.get_containers_with_entity(self.subsection_1.pk).select_related("section")
            ]
        assert result == [
            section1_1pinned,
            section2_1pinned_v2,
            section4_unpinned,
        ]

        # Test retrieving only "unpinned", for cases like potential deletion of a subsection, where we wouldn't care
        # about pinned uses anyways (they would be unaffected by a delete).

        with self.assertNumQueries(1):
            result2 = [
                c.section for c in
                content_api.get_containers_with_entity(
                    self.subsection_1.pk, ignore_pinned=True
                ).select_related("section")
            ]
        assert result2 == [section4_unpinned]

    def test_get_subsections_in_section_queries(self):
        """
        Test the query count of get_subsections_in_section()
        This also tests the generic method get_entities_in_container()
        """
        section = self.create_section_with_subsections([
            self.subsection_1,
            self.subsection_2,
            self.subsection_2_v1,
        ])
        with self.assertNumQueries(4):
            result = content_api.get_subsections_in_section(section, published=False)
        assert result == [
            Entry(self.subsection_1.versioning.draft),
            Entry(self.subsection_2.versioning.draft),
            Entry(self.subsection_2.versioning.draft, pinned=True),
        ]
        content_api.publish_all_drafts(self.learning_package.id)
        with self.assertNumQueries(4):
            result = content_api.get_subsections_in_section(section, published=True)
        assert result == [
            Entry(self.subsection_1.versioning.draft),
            Entry(self.subsection_2.versioning.draft),
            Entry(self.subsection_2.versioning.draft, pinned=True),
        ]

    def test_add_remove_container_children(self):
        """
        Test adding and removing children subsections from sections.
        """
        section, section_version = content_api.create_section_and_version(
            learning_package_id=self.learning_package.id,
            key="section:key",
            title="Section",
            subsections=[self.subsection_1],
            created=self.now,
            created_by=None,
        )
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(self.subsection_1.versioning.draft),
        ]
        subsection_3, _ = self.create_subsection(
            key="Subsection (3)",
            title="Subsection (3)",
        )
        # Add subsection_2 and subsection_3
        section_version_v2 = content_api.create_next_section_version(
            section=section,
            title=section_version.title,
            subsections=[self.subsection_2, subsection_3],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        section.refresh_from_db()
        assert section_version_v2.version_num == 2
        assert section_version_v2 in section.versioning.versions.all()
        # Verify that subsection_2 and subsection_3 is added to end
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(self.subsection_1.versioning.draft),
            Entry(self.subsection_2.versioning.draft),
            Entry(subsection_3.versioning.draft),
        ]

        # Remove subsection_1
        content_api.create_next_section_version(
            section=section,
            title=section_version.title,
            subsections=[self.subsection_1],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )
        section.refresh_from_db()
        # Verify that subsection_1 is removed
        assert content_api.get_subsections_in_section(section, published=False) == [
            Entry(self.subsection_2.versioning.draft),
            Entry(subsection_3.versioning.draft),
        ]

    def test_get_container_children_count(self):
        """
        Test get_container_children_count()
        """
        section = self.create_section_with_subsections([self.subsection_1])
        assert content_api.get_container_children_count(section.container, published=False) == 1
        # publish
        content_api.publish_all_drafts(self.learning_package.id)
        section_version = section.versioning.draft
        content_api.create_next_section_version(
            section=section,
            title=section_version.title,
            subsections=[self.subsection_2],
            created=self.now,
            created_by=None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        section.refresh_from_db()
        # Should have two subsections in draft version and 1 in published version
        assert content_api.get_container_children_count(section.container, published=False) == 2
        assert content_api.get_container_children_count(section.container, published=True) == 1
        # publish
        content_api.publish_all_drafts(self.learning_package.id)
        section.refresh_from_db()
        assert content_api.get_container_children_count(section.container, published=True) == 2
        # Soft delete subsection_1
        content_api.soft_delete_draft(self.subsection_1.pk)
        section.refresh_from_db()
        # Should contain only 1 child
        assert content_api.get_container_children_count(section.container, published=False) == 1
        content_api.publish_all_drafts(self.learning_package.id)
        section.refresh_from_db()
        assert content_api.get_container_children_count(section.container, published=True) == 1

    # Tests TODO:
    # Test that I can get a [PublishLog] history of a given section and all its children, including children that aren't
    #     currently in the section and excluding children that are only in other sections.
    # Test that I can get a [PublishLog] history of a given section and its children, that includes changes made to the
    #     child subsections while they were part of section but excludes changes made to those children while they were
    #     not part of the section. 🫣
