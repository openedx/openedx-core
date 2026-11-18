"""Subsections API.

This module provides functions to manage subsections.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ..containers import api as containers_api
from ..containers.models import ContainerVersion
from ..publishing.models import LearningPackage
from ..units.models import Unit, UnitVersion
from .models import Subsection, SubsectionVersion

__all__ = [
    "get_subsection",
    "create_subsection_and_version",
    "create_next_subsection_version",
    "SubsectionListEntry",
    "get_units_in_subsection",
]


def get_subsection(subsection_id: Subsection.ID, /):
    """Get a subsection"""
    return Subsection.objects.select_related("container").get(pk=subsection_id)


def create_subsection_and_version(
    learning_package_id: LearningPackage.ID,
    container_code: str,
    *,
    title: str,
    units: Iterable[Unit | UnitVersion] | None = None,
    created: datetime,
    created_by: int | None = None,
    can_stand_alone: bool = True,
) -> tuple[Subsection, SubsectionVersion]:
    """
    See documentation of `content_api.create_container_and_version()`

    The only real purpose of this function is to rename `entities` to `units`, and to specify that the version
    returned is a `SubsectionVersion`. In the future, if `SubsectionVersion` gets some fields that aren't on
    `ContainerVersion`, this function would be more important.
    """
    subsection, sv = containers_api.create_container_and_version(
        learning_package_id,
        container_code=container_code,
        title=title,
        entities=units,
        created=created,
        created_by=created_by,
        can_stand_alone=can_stand_alone,
        container_cls=Subsection,
    )
    assert isinstance(sv, SubsectionVersion)
    return subsection, sv


def create_next_subsection_version(
    subsection: Subsection | Subsection.ID,
    *,
    title: str | None = None,
    units: Iterable[Unit | UnitVersion] | None = None,
    created: datetime,
    created_by: int | None,
) -> SubsectionVersion:
    """
    See documentation of content_api.create_next_container_version()

    The only real purpose of this function is to rename `entities` to `units`, and to specify that the version
    returned is a `SubsectionVersion`. In the future, if `SubsectionVersion` gets some fields that aren't on
    `ContainerVersion`, this function would be more important.
    """
    if isinstance(subsection, int):
        subsection = get_subsection(subsection)
    assert isinstance(subsection, Subsection)
    sv = containers_api.create_next_container_version(
        subsection,
        title=title,
        entities=units,
        created=created,
        created_by=created_by,
        # For now, `entities_action` and `force_version_num` are unsupported but we could add them in the future.
    )
    assert isinstance(sv, SubsectionVersion)
    return sv


@dataclass(frozen=True)
class SubsectionListEntry:
    """
    Data about a single unit in a subsection.
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
    Get the list of entities and their versions in the draft or published
    version of the given Subsection.

    Args:
        subsection: The Subsection, e.g. returned by `get_subsection()`
        published: `True` if we want the published version of the subsection, or
            `False` for the draft version.
    """
    assert isinstance(subsection, Subsection)
    units = []
    try:
        entries = containers_api.get_entities_in_container(
            subsection,
            published=published,
            select_related_version="containerversion__unitversion",
        )
    except ContainerVersion.DoesNotExist as exc:
        raise SubsectionVersion.DoesNotExist() from exc  # Make the exception more specific
    for entry in entries:
        # Convert from generic PublishableEntityVersion to UnitVersion:
        unit_version = entry.entity_version.containerversion.unitversion
        assert isinstance(unit_version, UnitVersion)
        units.append(SubsectionListEntry(unit_version=unit_version, pinned=entry.pinned))
    return units
