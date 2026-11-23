"""Sections API.

This module provides functions to manage sections.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ..containers import api as containers_api
from ..containers.models import ContainerVersion
from ..publishing.models import LearningPackage
from ..subsections.models import Subsection, SubsectionVersion
from .models import Section, SectionVersion

__all__ = [
    "get_section",
    "create_section_and_version",
    "create_next_section_version",
    "SectionListEntry",
    "get_subsections_in_section",
]


def get_section(section_id: Section.ID, /):
    """Get a section"""
    return Section.objects.select_related("container").get(pk=section_id)


def create_section_and_version(
    learning_package_id: LearningPackage.ID,
    container_code: str,
    *,
    title: str,
    subsections: Iterable[Subsection | SubsectionVersion] | None = None,
    created: datetime,
    created_by: int | None = None,
    can_stand_alone: bool = True,
) -> tuple[Section, SectionVersion]:
    """
    See documentation of `content_api.create_container_and_version()`

    The only real purpose of this function is to rename `entities` to `subsections`, and to specify that the version
    returned is a `SectionVersion`. In the future, if `SectionVersion` gets some fields that aren't on
    `ContainerVersion`, this function would be more important.
    """
    section, sv = containers_api.create_container_and_version(
        learning_package_id,
        container_code=container_code,
        title=title,
        entities=subsections,
        created=created,
        created_by=created_by,
        can_stand_alone=can_stand_alone,
        container_cls=Section,
    )
    assert isinstance(sv, SectionVersion)
    return section, sv


def create_next_section_version(
    section: Section | Section.ID,
    *,
    title: str | None = None,
    subsections: Iterable[Subsection | SubsectionVersion] | None = None,
    created: datetime,
    created_by: int | None,
) -> SectionVersion:
    """
    See documentation of content_api.create_next_container_version()

    The only real purpose of this function is to rename `entities` to `subsections`, and to specify that the version
    returned is a `SectionVersion`. In the future, if `SectionVersion` gets some fields that aren't on
    `ContainerVersion`, this function would be more important.
    """
    if isinstance(section, int):
        section = get_section(section)
    assert isinstance(section, Section)
    sv = containers_api.create_next_container_version(
        section,
        title=title,
        entities=subsections,
        created=created,
        created_by=created_by,
        # For now, `entities_action` and `force_version_num` are unsupported but we could add them in the future.
    )
    assert isinstance(sv, SectionVersion)
    return sv


@dataclass(frozen=True)
class SectionListEntry:
    """
    Data about a single subsection in a section.
    """

    subsection_version: SubsectionVersion
    pinned: bool = False

    @property
    def subsection(self):
        return self.subsection_version.subsection


def get_subsections_in_section(
    section: Section,
    *,
    published: bool,
) -> list[SectionListEntry]:
    """
    Get the list of entities and their versions in the draft or published
    version of the given Section.

    Args:
        section: The section, e.g. returned by `get_section()`
        published: `True` if we want the published version of the section, or
            `False` for the draft version.
    """
    assert isinstance(section, Section)
    subsections = []
    try:
        entries = containers_api.get_entities_in_container(
            section,
            published=published,
            select_related_version="containerversion__subsectionversion",
        )
    except ContainerVersion.DoesNotExist as exc:
        raise SectionVersion.DoesNotExist() from exc  # Make the exception more specific
    for entry in entries:
        # Convert from generic PublishableEntityVersion to SubsectionVersion:
        subsection_version = entry.entity_version.containerversion.subsectionversion
        assert isinstance(subsection_version, SubsectionVersion)
        subsections.append(SectionListEntry(subsection_version=subsection_version, pinned=entry.pinned))
    return subsections
