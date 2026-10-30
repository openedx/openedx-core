"""
Models that implement units
"""
from __future__ import annotations

from typing import NewType, cast, override

from django.core.exceptions import ValidationError
from django.db import models

from ..containers.models import Container, ContainerVersion
from ..publishing.models import PublishableEntity

__all__ = [
    "Unit",
    "UnitVersion",
]


@Container.register_subclass
class Unit(Container):
    """
    A Unit is type of :class:`Container` that holds :class:`Component`
    children.

    Via :class:`Container` and its :class:`PublishableEntityMixin`, Units are
    also publishable entities and can be added to other containers.
    """

    UnitID = NewType("UnitID", Container.ID)
    type ID = UnitID

    type_code = "unit"
    olx_tag_name = "vertical"  # Serializes to OLX as `<unit>...</unit>`.
    validate_entity_select_related = ("component",)

    container = models.OneToOneField(
        Container,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )

    @property
    def id(self) -> ID:
        return cast(Unit.ID, self.publishable_entity_id)

    @override
    @classmethod
    def validate_entity(cls, entity: PublishableEntity) -> None:
        """Check if the given entity is allowed as a child of a Unit"""
        # Units only allow Components as children, so the entity must be 1:1 with Component:
        if not hasattr(entity, "component"):
            raise ValidationError("Only Components can be added as children of a Unit")


class UnitVersion(ContainerVersion):
    """
    A UnitVersion is a specific version of a :class:`Unit`.

    Via :class:`ContainerVersion` and its :class:`EntityList`, it defines the
    list of :class:`Component` children in this version of the :class:`Unit`.
    """

    container_version = models.OneToOneField(
        ContainerVersion,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )

    @property
    def unit(self) -> Unit:
        """Convenience accessor to the Unit this version is associated with"""
        return self.container_version.container.unit  # pylint: disable=no-member
