"""Units API.

This module provides functions to manage units.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ..components.models import Component, ComponentVersion
from ..containers import api as containers_api
from ..containers.models import ContainerVersion
from ..publishing.models import LearningPackage
from .models import Unit, UnitVersion

__all__ = [
    "get_unit",
    "create_unit_and_version",
    "create_next_unit_version",
    "UnitListEntry",
    "get_components_in_unit",
]


def get_unit(unit_id: Unit.ID, /):
    """Get a unit"""
    return Unit.objects.select_related("container").get(pk=unit_id)


def create_unit_and_version(
    learning_package_id: LearningPackage.ID,
    container_code: str,
    *,
    title: str,
    components: Iterable[Component | ComponentVersion] | None = None,
    created: datetime,
    created_by: int | None = None,
    can_stand_alone: bool = True,
) -> tuple[Unit, UnitVersion]:
    """
    See documentation of `content_api.create_container_and_version()`

    The only real purpose of this function is to rename `entities` to `components`, and to specify that the version
    returned is a `UnitVersion`. In the future, if `UnitVersion` gets some fields that aren't on `ContainerVersion`,
    this function would be more important.
    """
    unit, uv = containers_api.create_container_and_version(
        learning_package_id,
        container_code=container_code,
        title=title,
        entities=components,
        created=created,
        created_by=created_by,
        can_stand_alone=can_stand_alone,
        container_cls=Unit,
    )
    assert isinstance(uv, UnitVersion)
    return unit, uv


def create_next_unit_version(
    unit: Unit | Unit.ID,
    *,
    title: str | None = None,
    components: Iterable[Component | ComponentVersion] | None = None,
    created: datetime,
    created_by: int | None,
) -> UnitVersion:
    """
    See documentation of content_api.create_next_container_version()

    The only real purpose of this function is to rename `entities` to `components`, and to specify that the version
    returned is a `UnitVersion`. In the future, if `UnitVersion` gets some fields that aren't on `ContainerVersion`,
    this function would be more important.
    """
    if isinstance(unit, int):
        unit = get_unit(unit)
    assert isinstance(unit, Unit)
    uv = containers_api.create_next_container_version(
        unit,
        title=title,
        entities=components,
        created=created,
        created_by=created_by,
        # For now, `entities_action` and `force_version_num` are unsupported but we could add them in the future.
    )
    assert isinstance(uv, UnitVersion)
    return uv


@dataclass(frozen=True)
class UnitListEntry:
    """
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
    Get the list of entities and their versions in the draft or published
    version of the given Unit.

    Args:
        unit: The Unit, e.g. returned by `get_unit()`
        published: `True` if we want the published version of the unit, or
            `False` for the draft version.
    """
    assert isinstance(unit, Unit)
    components = []
    try:
        entries = containers_api.get_entities_in_container(
            unit,
            published=published,
            select_related_version="componentversion",
        )
    except ContainerVersion.DoesNotExist as exc:
        raise UnitVersion.DoesNotExist() from exc  # Make the exception more specific
    for entry in entries:
        # Convert from generic PublishableEntityVersion to ComponentVersion:
        component_version = entry.entity_version.componentversion
        assert isinstance(component_version, ComponentVersion)
        components.append(UnitListEntry(component_version=component_version, pinned=entry.pinned))
    return components
