"""Subsections API.

This module provides functions to manage subsections.
"""
from dataclasses import dataclass
from datetime import datetime

from django.db.transaction import atomic

from ..publishing import api as publishing_api
from ..units.models import Unit, UnitVersion
from .models import Subsection, SubsectionVersion

# 🛑 UNSTABLE: All APIs related to containers are unstable until we've figured
#              out our approach to dynamic content (randomized, A/B tests, etc.)
__all__ = [
    "create_subsection",
    "create_subsection_version",
    "create_next_subsection_version",
    "create_subsection_and_version",
    "get_subsection",
    "get_subsection_version",
    "get_latest_subsection_version",
    "SubsectionListEntry",
    "get_units_in_subsection",
    "get_units_in_published_subsection_as_of",
]


def create_subsection(
    learning_package_id: int,
    key: str,
    created: datetime,
    created_by: int | None,
    *,
    can_stand_alone: bool = True,
) -> Subsection:
    """
    [ 🛑 UNSTABLE ] Create a new subsection.

    Args:
        learning_package_id: The learning package ID.
        key: The key.
        created: The creation date.
        created_by: The user who created the subsection.
        can_stand_alone: Set to False when created as part of containers
    """
    return publishing_api.create_container(
        learning_package_id,
        key,
        created,
        created_by,
        can_stand_alone=can_stand_alone,
        container_cls=Subsection,
    )


def create_subsection_version(
    subsection: Subsection,
    version_num: int,
    *,
    title: str,
    entity_rows: list[publishing_api.ContainerEntityRow],
    created: datetime,
    created_by: int | None = None,
) -> SubsectionVersion:
    """
    [ 🛑 UNSTABLE ] Create a new subsection version.

    This is a very low-level API, likely only needed for import/export. In
    general, you will use `create_subsection_and_version()` and
    `create_next_subsection_version()` instead.

    Args:
        subsection_pk: The subsection ID.
        version_num: The version number.
        title: The title.
        entity_rows: child entities/versions
        created: The creation date.
        created_by: The user who created the subsection.
    """
    return publishing_api.create_container_version(
        subsection.pk,
        version_num,
        title=title,
        entity_rows=entity_rows,
        created=created,
        created_by=created_by,
        container_version_cls=SubsectionVersion,
    )


def _pub_entities_for_units(
    units: list[Unit | UnitVersion] | None,
) -> list[publishing_api.ContainerEntityRow] | None:
    """
    Helper method: given a list of Unit | UnitVersion, return the
    list of ContainerEntityRows needed for the base container APIs.

    UnitVersion is passed when we want to pin a specific version, otherwise
    Unit is used for unpinned.
    """
    if units is None:
        # When these are None, that means don't change the entities in the list.
        return None
    for u in units:
        if not isinstance(u, (Unit, UnitVersion)):
            raise TypeError("Subsection units must be either Unit or UnitVersion.")
    return [
        (
            publishing_api.ContainerEntityRow(
                entity_pk=u.container.publishable_entity_id,
                version_pk=None,
            ) if isinstance(u, Unit)
            else publishing_api.ContainerEntityRow(
                entity_pk=u.unit.container.publishable_entity_id,
                version_pk=u.container_version.publishable_entity_version_id,
            )
        )
        for u in units
    ]


def create_next_subsection_version(
    subsection: Subsection,
    *,
    title: str | None = None,
    units: list[Unit | UnitVersion] | None = None,
    created: datetime,
    created_by: int | None = None,
    entities_action: publishing_api.ChildrenEntitiesAction = publishing_api.ChildrenEntitiesAction.REPLACE,
    force_version_num: int | None = None,
) -> SubsectionVersion:
    """
    [ 🛑 UNSTABLE ] Create the next subsection version.

    Args:
        subsection_pk: The subsection ID.
        title: The title. Leave as None to keep the current title.
        units: The units, as a list of Units (unpinned) and/or UnitVersions (pinned). Passing None
           will leave the existing units unchanged.
        created: The creation date.
        created_by: The user who created the subsection.
        force_version_num (int, optional): If provided, overrides the automatic version number increment and sets
            this version's number explicitly. Use this if you need to restore or import a version with a specific
            version number, such as during data migration or when synchronizing with external systems.

    Returns:
        The newly created subsection version.

    Why use force_version_num?
        Normally, the version number is incremented automatically from the latest version.
        If you need to set a specific version number (for example, when restoring from backup,
        importing legacy data, or synchronizing with another system),
        use force_version_num to override the default behavior.

    Why not use create_component_version?
        The main reason is that we want to reuse the logic for adding entities to this container.
    """
    entity_rows = _pub_entities_for_units(units)
    subsection_version = publishing_api.create_next_container_version(
        subsection.pk,
        title=title,
        entity_rows=entity_rows,
        created=created,
        created_by=created_by,
        container_version_cls=SubsectionVersion,
        entities_action=entities_action,
        force_version_num=force_version_num,
    )
    return subsection_version


def create_subsection_and_version(
    learning_package_id: int,
    key: str,
    *,
    title: str,
    units: list[Unit | UnitVersion] | None = None,
    created: datetime,
    created_by: int | None = None,
    can_stand_alone: bool = True,
) -> tuple[Subsection, SubsectionVersion]:
    """
    [ 🛑 UNSTABLE ] Create a new subsection and its version.

    Args:
        learning_package_id: The learning package ID.
        key: The key.
        created: The creation date.
        created_by: The user who created the subsection.
        can_stand_alone: Set to False when created as part of containers
    """
    entity_rows = _pub_entities_for_units(units)
    with atomic():
        subsection = create_subsection(
            learning_package_id,
            key,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        subsection_version = create_subsection_version(
            subsection,
            1,
            title=title,
            entity_rows=entity_rows or [],
            created=created,
            created_by=created_by,
        )
    return subsection, subsection_version


def get_subsection(subsection_pk: int) -> Subsection:
    """
    [ 🛑 UNSTABLE ] Get a subsection.

    Args:
        subsection_pk: The subsection ID.
    """
    return Subsection.objects.get(pk=subsection_pk)


def get_subsection_version(subsection_version_pk: int) -> SubsectionVersion:
    """
    [ 🛑 UNSTABLE ] Get a subsection version.

    Args:
        subsection_version_pk: The subsection version ID.
    """
    return SubsectionVersion.objects.get(pk=subsection_version_pk)


def get_latest_subsection_version(subsection_pk: int) -> SubsectionVersion:
    """
    [ 🛑 UNSTABLE ] Get the latest subsection version.

    Args:
        subsection_pk: The subsection ID.
    """
    return Subsection.objects.get(pk=subsection_pk).versioning.latest


@dataclass(frozen=True)
class SubsectionListEntry:
    """
    [ 🛑 UNSTABLE ]
    Data about a single entity in a container, e.g. a unit in a subsection.
    """
    unit_version: UnitVersion
    pinned: bool = False

    @property
    def unit(self):
        return self.unit_version.unit


def get_units_in_subsection(
    subsection: Subsection,
    *,
    published: bool,
) -> list[SubsectionListEntry]:
    """
    [ 🛑 UNSTABLE ]
    Get the list of entities and their versions in the draft or published
    version of the given Subsection.

    Args:
        subsection: The Subsection, e.g. returned by `get_subsection()`
        published: `True` if we want the published version of the subsection, or
            `False` for the draft version.
    """
    assert isinstance(subsection, Subsection)
    units = []
    entries = publishing_api.get_entities_in_container(
        subsection,
        published=published,
        select_related_version="containerversion__unitversion",
    )
    for entry in entries:
        # Convert from generic PublishableEntityVersion to UnitVersion:
        unit_version = entry.entity_version.containerversion.unitversion
        assert isinstance(unit_version, UnitVersion)
        units.append(SubsectionListEntry(unit_version=unit_version, pinned=entry.pinned))
    return units


def get_units_in_published_subsection_as_of(
    subsection: Subsection,
    publish_log_id: int,
) -> list[SubsectionListEntry] | None:
    """
    [ 🛑 UNSTABLE ]
    Get the list of entities and their versions in the published version of the
    given container as of the given PublishLog version (which is essentially a
    version for the entire learning package).

    TODO: This API should be updated to also return the SubsectionVersion so we can
          see the subsection title and any other metadata from that point in time.
    TODO: accept a publish log UUID, not just int ID?
    TODO: move the implementation to be a generic 'containers' implementation
          that this subsections function merely wraps.
    TODO: optimize, perhaps by having the publishlog store a record of all
          ancestors of every modified PublishableEntity in the publish.
    """
    assert isinstance(subsection, Subsection)
    subsection_pub_entity_version = publishing_api.get_published_version_as_of(
        subsection.publishable_entity_id, publish_log_id
    )
    if subsection_pub_entity_version is None:
        return None  # This subsection was not published as of the given PublishLog ID.
    container_version = subsection_pub_entity_version.containerversion

    entity_list = []
    rows = container_version.entity_list.entitylistrow_set.order_by("order_num")
    for row in rows:
        if row.entity_version is not None:
            unit_version = row.entity_version.containerversion.unitversion
            assert isinstance(unit_version, UnitVersion)
            entity_list.append(SubsectionListEntry(unit_version=unit_version, pinned=True))
        else:
            # Unpinned unit - figure out what its latest published version was.
            # This is not optimized. It could be done in one query per subsection rather than one query per unit.
            pub_entity_version = publishing_api.get_published_version_as_of(row.entity_id, publish_log_id)
            if pub_entity_version:
                entity_list.append(
                    SubsectionListEntry(unit_version=pub_entity_version.containerversion.unitversion, pinned=False)
                )
    return entity_list
