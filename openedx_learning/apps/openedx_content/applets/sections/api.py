"""Sections API.

This module provides functions to manage sections.
"""
from dataclasses import dataclass
from datetime import datetime

from django.db.transaction import atomic

from ..publishing import api as publishing_api
from ..subsections.models import Subsection, SubsectionVersion
from .models import Section, SectionVersion

# 🛑 UNSTABLE: All APIs related to containers are unstable until we've figured
#              out our approach to dynamic content (randomized, A/B tests, etc.)
__all__ = [
    "create_section",
    "create_section_version",
    "create_next_section_version",
    "create_section_and_version",
    "get_section",
    "get_section_version",
    "get_latest_section_version",
    "SectionListEntry",
    "get_subsections_in_section",
    "get_subsections_in_published_section_as_of",
]


def create_section(
    learning_package_id: int,
    key: str,
    created: datetime,
    created_by: int | None,
    *,
    can_stand_alone: bool = True,
) -> Section:
    """
    [ 🛑 UNSTABLE ] Create a new section.

    Args:
        learning_package_id: The learning package ID.
        key: The key.
        created: The creation date.
        created_by: The user who created the section.
        can_stand_alone: Set to False when created as part of containers
    """
    return publishing_api.create_container(
        learning_package_id,
        key,
        created,
        created_by,
        can_stand_alone=can_stand_alone,
        container_cls=Section,
    )


def create_section_version(
    section: Section,
    version_num: int,
    *,
    title: str,
    entity_rows: list[publishing_api.ContainerEntityRow],
    created: datetime,
    created_by: int | None = None,
) -> SectionVersion:
    """
    [ 🛑 UNSTABLE ] Create a new section version.

    This is a very low-level API, likely only needed for import/export. In
    general, you will use `create_section_and_version()` and
    `create_next_section_version()` instead.

    Args:
        section_pk: The section ID.
        version_num: The version number.
        title: The title.
        entity_rows: child entities/versions
        created: The creation date.
        created_by: The user who created the section.
    """
    return publishing_api.create_container_version(
        section.pk,
        version_num,
        title=title,
        entity_rows=entity_rows,
        created=created,
        created_by=created_by,
        container_version_cls=SectionVersion,
    )


def _pub_entities_for_subsections(
    subsections: list[Subsection | SubsectionVersion] | None,
) -> list[publishing_api.ContainerEntityRow] | None:
    """
    Helper method: given a list of Subsection | SubsectionVersion, return the
    lists of publishable_entities_pks and entity_version_pks needed for the
    base container APIs.

    SubsectionVersion is passed when we want to pin a specific version, otherwise
    Subsection is used for unpinned.
    """
    if subsections is None:
        # When these are None, that means don't change the entities in the list.
        return None
    for u in subsections:
        if not isinstance(u, (Subsection, SubsectionVersion)):
            raise TypeError("Section subsections must be either Subsection or SubsectionVersion.")
    return [
        (
            publishing_api.ContainerEntityRow(
                entity_pk=s.container.publishable_entity_id,
                version_pk=None,
            ) if isinstance(s, Subsection)
            else publishing_api.ContainerEntityRow(
                entity_pk=s.subsection.container.publishable_entity_id,
                version_pk=s.container_version.publishable_entity_version_id,
            )
        )
        for s in subsections
    ]


def create_next_section_version(
    section: Section,
    *,
    title: str | None = None,
    subsections: list[Subsection | SubsectionVersion] | None = None,
    created: datetime,
    created_by: int | None = None,
    entities_action: publishing_api.ChildrenEntitiesAction = publishing_api.ChildrenEntitiesAction.REPLACE,
    force_version_num: int | None = None,
) -> SectionVersion:
    """
    [ 🛑 UNSTABLE ] Create the next section version.

    Args:
        section_pk: The section ID.
        title: The title. Leave as None to keep the current title.
        subsections: The subsections, as a list of Subsections (unpinned) and/or SubsectionVersions (pinned).
            Passing None will leave the existing subsections unchanged.
        created: The creation date.
        created_by: The user who created the section.
        force_version_num (int, optional): If provided, overrides the automatic version number increment and sets
            this version's number explicitly. Use this if you need to restore or import a version with a specific
            version number, such as during data migration or when synchronizing with external systems.

    Returns:
        The newly created SectionVersion.

    Why use force_version_num?
        Normally, the version number is incremented automatically from the latest version.
        If you need to set a specific version number (for example, when restoring from backup,
        importing legacy data, or synchronizing with another system),
        use force_version_num to override the default behavior.

    Why not use create_component_version?
        The main reason is that we want to reuse the logic for adding entities to this container.
    """
    entity_rows = _pub_entities_for_subsections(subsections)
    section_version = publishing_api.create_next_container_version(
        section.pk,
        title=title,
        entity_rows=entity_rows,
        created=created,
        created_by=created_by,
        container_version_cls=SectionVersion,
        entities_action=entities_action,
        force_version_num=force_version_num,
    )
    return section_version


def create_section_and_version(
    learning_package_id: int,
    key: str,
    *,
    title: str,
    subsections: list[Subsection | SubsectionVersion] | None = None,
    created: datetime,
    created_by: int | None = None,
    can_stand_alone: bool = True,
) -> tuple[Section, SectionVersion]:
    """
    [ 🛑 UNSTABLE ] Create a new section and its version.

    Args:
        learning_package_id: The learning package ID.
        key: The key.
        created: The creation date.
        created_by: The user who created the section.
        can_stand_alone: Set to False when created as part of containers
    """
    entity_rows = _pub_entities_for_subsections(subsections)
    with atomic():
        section = create_section(
            learning_package_id,
            key,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        section_version = create_section_version(
            section,
            1,
            title=title,
            entity_rows=entity_rows or [],
            created=created,
            created_by=created_by,
        )
    return section, section_version


def get_section(section_pk: int) -> Section:
    """
    [ 🛑 UNSTABLE ] Get a section.

    Args:
        section_pk: The section ID.
    """
    return Section.objects.get(pk=section_pk)


def get_section_version(section_version_pk: int) -> SectionVersion:
    """
    [ 🛑 UNSTABLE ] Get a section version.

    Args:
        section_version_pk: The section version ID.
    """
    return SectionVersion.objects.get(pk=section_version_pk)


def get_latest_section_version(section_pk: int) -> SectionVersion:
    """
    [ 🛑 UNSTABLE ] Get the latest section version.

    Args:
        section_pk: The section ID.
    """
    return Section.objects.get(pk=section_pk).versioning.latest


@dataclass(frozen=True)
class SectionListEntry:
    """
    [ 🛑 UNSTABLE ]
    Data about a single entity in a container, e.g. a subsection in a section.
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
    [ 🛑 UNSTABLE ]
    Get the list of entities and their versions in the draft or published
    version of the given Section.

    Args:
        section: The Section, e.g. returned by `get_section()`
        published: `True` if we want the published version of the section, or
            `False` for the draft version.
    """
    assert isinstance(section, Section)
    subsections = []
    entries = publishing_api.get_entities_in_container(
        section,
        published=published,
        select_related_version="containerversion__subsectionversion",
    )
    for entry in entries:
        # Convert from generic PublishableEntityVersion to SubsectionVersion:
        subsection_version = entry.entity_version.containerversion.subsectionversion
        assert isinstance(subsection_version, SubsectionVersion)
        subsections.append(SectionListEntry(subsection_version=subsection_version, pinned=entry.pinned))
    return subsections


def get_subsections_in_published_section_as_of(
    section: Section,
    publish_log_id: int,
) -> list[SectionListEntry] | None:
    """
    [ 🛑 UNSTABLE ]
    Get the list of entities and their versions in the published version of the
    given container as of the given PublishLog version (which is essentially a
    version for the entire learning package).

    TODO: This API should be updated to also return the SectionVersion so we can
          see the section title and any other metadata from that point in time.
    TODO: accept a publish log UUID, not just int ID?
    TODO: move the implementation to be a generic 'containers' implementation
          that this sections function merely wraps.
    TODO: optimize, perhaps by having the publishlog store a record of all
          ancestors of every modified PublishableEntity in the publish.
    """
    assert isinstance(section, Section)
    section_pub_entity_version = publishing_api.get_published_version_as_of(
        section.publishable_entity_id, publish_log_id
    )
    if section_pub_entity_version is None:
        return None  # This section was not published as of the given PublishLog ID.
    container_version = section_pub_entity_version.containerversion

    entity_list = []
    rows = container_version.entity_list.entitylistrow_set.order_by("order_num")
    for row in rows:
        if row.entity_version is not None:
            subsection_version = row.entity_version.containerversion.subsectionversion
            assert isinstance(subsection_version, SubsectionVersion)
            entity_list.append(SectionListEntry(subsection_version=subsection_version, pinned=True))
        else:
            # Unpinned subsection - figure out what its latest published version was.
            # This is not optimized. It could be done in one query per section rather than one query per subsection.
            pub_entity_version = publishing_api.get_published_version_as_of(row.entity_id, publish_log_id)
            if pub_entity_version:
                entity_list.append(SectionListEntry(
                    subsection_version=pub_entity_version.containerversion.subsectionversion, pinned=False
                ))
    return entity_list
