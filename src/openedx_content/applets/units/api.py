"""Units API.

This module provides functions to manage units.
"""
from dataclasses import dataclass
from datetime import datetime

from django.db.transaction import atomic

from ..components.models import Component, ComponentVersion
from ..publishing import api as publishing_api
from .models import Unit, UnitVersion

# 🛑 UNSTABLE: All APIs related to containers are unstable until we've figured
#              out our approach to dynamic content (randomized, A/B tests, etc.)
__all__ = [
    "create_unit",
    "create_unit_version",
    "create_next_unit_version",
    "create_unit_and_version",
    "get_unit",
    "get_unit_version",
    "get_latest_unit_version",
    "UnitListEntry",
    "get_components_in_unit",
    "get_components_in_published_unit_as_of",
]


def create_unit(
    learning_package_id: int,
    key: str,
    created: datetime,
    created_by: int | None,
    *,
    can_stand_alone: bool = True,
) -> Unit:
    """
    [ 🛑 UNSTABLE ] Create a new unit.

    Args:
        learning_package_id: The learning package ID.
        key: The key.
        created: The creation date.
        created_by: The user who created the unit.
        can_stand_alone: Set to False when created as part of containers
    """
    return publishing_api.create_container(
        learning_package_id,
        key,
        created,
        created_by,
        can_stand_alone=can_stand_alone,
        container_cls=Unit,
    )


def create_unit_version(
    unit: Unit,
    version_num: int,
    *,
    title: str,
    entity_rows: list[publishing_api.ContainerEntityRow],
    created: datetime,
    created_by: int | None = None,
) -> UnitVersion:
    """
    [ 🛑 UNSTABLE ] Create a new unit version.

    This is a very low-level API, likely only needed for import/export. In
    general, you will use `create_unit_and_version()` and
    `create_next_unit_version()` instead.

    Args:
        unit: The unit object.
        version_num: The version number.
        title: The title.
        entity_rows: child entities/versions
        created: The creation date.
        created_by: The user who created the unit.
        force_version_num (int, optional): If provided, overrides the automatic version number increment and sets
            this version's number explicitly. Use this if you need to restore or import a version with a specific
            version number, such as during data migration or when synchronizing with external systems.

    Returns:
        UnitVersion: The newly created UnitVersion instance.

    Why use force_version_num?
        Normally, the version number is incremented automatically from the latest version.
        If you need to set a specific version number (for example, when restoring from backup,
        importing legacy data, or synchronizing with another system),
        use force_version_num to override the default behavior.

    Why not use create_component_version?
        The main reason is that we want to reuse the logic for adding entities to this container.
    """
    return publishing_api.create_container_version(
        unit.pk,
        version_num,
        title=title,
        entity_rows=entity_rows,
        created=created,
        created_by=created_by,
        container_version_cls=UnitVersion,
    )


def _pub_entities_for_components(
    components: list[Component | ComponentVersion] | None,
) -> list[publishing_api.ContainerEntityRow] | None:
    """
    Helper method: given a list of Component | ComponentVersion, return the
    list of ContainerEntityRows needed for the base container APIs.

    ComponentVersion is passed when we want to pin a specific version, otherwise
    Component is used for unpinned.
    """
    if components is None:
        # When these are None, that means don't change the entities in the list.
        return None
    for c in components:
        if not isinstance(c, (Component, ComponentVersion)):
            raise TypeError("Unit components must be either Component or ComponentVersion.")
    return [
        (
            publishing_api.ContainerEntityRow(
                entity_pk=c.publishable_entity_id,
                version_pk=None,
            ) if isinstance(c, Component)
            else  # isinstance(c, ComponentVersion)
            publishing_api.ContainerEntityRow(
                entity_pk=c.component.publishable_entity_id,
                version_pk=c.pk,
            )
        )
        for c in components
    ]


def create_next_unit_version(
    unit: Unit,
    *,
    title: str | None = None,
    components: list[Component | ComponentVersion] | None = None,
    created: datetime,
    created_by: int | None = None,
    entities_action: publishing_api.ChildrenEntitiesAction = publishing_api.ChildrenEntitiesAction.REPLACE,
    force_version_num: int | None = None,
) -> UnitVersion:
    """
    [ 🛑 UNSTABLE ] Create the next unit version.

    Args:
        unit_pk: The unit ID.
        title: The title. Leave as None to keep the current title.
        components: The components, as a list of Components (unpinned) and/or ComponentVersions (pinned). Passing None
           will leave the existing components unchanged.
        created: The creation date.
        created_by: The user who created the unit.
    """
    entity_rows = _pub_entities_for_components(components)
    unit_version = publishing_api.create_next_container_version(
        unit.pk,
        title=title,
        entity_rows=entity_rows,
        created=created,
        created_by=created_by,
        container_version_cls=UnitVersion,
        entities_action=entities_action,
        force_version_num=force_version_num,
    )
    return unit_version


def create_unit_and_version(
    learning_package_id: int,
    key: str,
    *,
    title: str,
    components: list[Component | ComponentVersion] | None = None,
    created: datetime,
    created_by: int | None = None,
    can_stand_alone: bool = True,
) -> tuple[Unit, UnitVersion]:
    """
    [ 🛑 UNSTABLE ] Create a new unit and its version.

    Args:
        learning_package_id: The learning package ID.
        key: The key.
        created: The creation date.
        created_by: The user who created the unit.
        can_stand_alone: Set to False when created as part of containers
    """
    entity_rows = _pub_entities_for_components(components)
    with atomic(savepoint=False):
        unit = create_unit(
            learning_package_id,
            key,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        unit_version = create_unit_version(
            unit,
            1,
            title=title,
            entity_rows=entity_rows or [],
            created=created,
            created_by=created_by,
        )
    return unit, unit_version


def get_unit(unit_pk: int) -> Unit:
    """
    [ 🛑 UNSTABLE ] Get a unit.

    Args:
        unit_pk: The unit ID.
    """
    return Unit.objects.get(pk=unit_pk)


def get_unit_version(unit_version_pk: int) -> UnitVersion:
    """
    [ 🛑 UNSTABLE ] Get a unit version.

    Args:
        unit_version_pk: The unit version ID.
    """
    return UnitVersion.objects.get(pk=unit_version_pk)


def get_latest_unit_version(unit_pk: int) -> UnitVersion:
    """
    [ 🛑 UNSTABLE ] Get the latest unit version.

    Args:
        unit_pk: The unit ID.
    """
    return Unit.objects.get(pk=unit_pk).versioning.latest


@dataclass(frozen=True)
class UnitListEntry:
    """
    [ 🛑 UNSTABLE ]
    Data about a single entity in a container, e.g. a component in a unit.
    """
    component_version: ComponentVersion
    pinned: bool = False

    @property
    def component(self):
        return self.component_version.component


def get_components_in_unit(
    unit: Unit,
    *,
    published: bool,
) -> list[UnitListEntry]:
    """
    [ 🛑 UNSTABLE ]
    Get the list of entities and their versions in the draft or published
    version of the given Unit.

    Args:
        unit: The Unit, e.g. returned by `get_unit()`
        published: `True` if we want the published version of the unit, or
            `False` for the draft version.
    """
    assert isinstance(unit, Unit)
    components = []
    entries = publishing_api.get_entities_in_container(
        unit,
        published=published,
        select_related_version="componentversion",
    )
    for entry in entries:
        # Convert from generic PublishableEntityVersion to ComponentVersion:
        component_version = entry.entity_version.componentversion
        assert isinstance(component_version, ComponentVersion)
        components.append(UnitListEntry(component_version=component_version, pinned=entry.pinned))
    return components


def get_components_in_published_unit_as_of(
    unit: Unit,
    publish_log_id: int,
) -> list[UnitListEntry] | None:
    """
    [ 🛑 UNSTABLE ]
    Get the list of entities and their versions in the published version of the
    given container as of the given PublishLog version (which is essentially a
    version for the entire learning package).

    TODO: This API should be updated to also return the UnitVersion so we can
          see the unit title and any other metadata from that point in time.
    TODO: accept a publish log UUID, not just int ID?
    TODO: move the implementation to be a generic 'containers' implementation
          that this units function merely wraps.
    TODO: optimize, perhaps by having the publishlog store a record of all
          ancestors of every modified PublishableEntity in the publish.
    """
    assert isinstance(unit, Unit)
    unit_pub_entity_version = publishing_api.get_published_version_as_of(unit.publishable_entity_id, publish_log_id)
    if unit_pub_entity_version is None:
        return None  # This unit was not published as of the given PublishLog ID.
    container_version = unit_pub_entity_version.containerversion

    entity_list = []
    rows = container_version.entity_list.entitylistrow_set.order_by("order_num")
    for row in rows:
        if row.entity_version is not None:
            component_version = row.entity_version.componentversion
            assert isinstance(component_version, ComponentVersion)
            entity_list.append(UnitListEntry(component_version=component_version, pinned=True))
        else:
            # Unpinned component - figure out what its latest published version was.
            # This is not optimized. It could be done in one query per unit rather than one query per component.
            pub_entity_version = publishing_api.get_published_version_as_of(row.entity_id, publish_log_id)
            if pub_entity_version:
                entity_list.append(UnitListEntry(component_version=pub_entity_version.componentversion, pinned=False))
    return entity_list
