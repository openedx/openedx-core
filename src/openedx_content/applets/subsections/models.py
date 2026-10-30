"""
Models that implement subsections
"""

from typing import NewType, cast, override

from django.core.exceptions import ValidationError
from django.db import models

from ..containers.api import get_container_subclass_of
from ..containers.models import Container, ContainerVersion
from ..publishing.models import PublishableEntity
from ..units.models import Unit

__all__ = [
    "Subsection",
    "SubsectionVersion",
]


@Container.register_subclass
class Subsection(Container):
    """
    A Subsection is type of :class:`Container` that holds :class:`Unit`
    children.

    Via :class:`Container` and its :class:`PublishableEntityMixin`,
    Subsections are also publishable entities and can be added to other
    containers.
    """

    SubsectionID = NewType("SubsectionID", Container.ID)
    type ID = SubsectionID

    type_code = "subsection"
    olx_tag_name = "sequential"  # Serializes to OLX as `<sequential>...</sequential>`.
    validate_entity_select_related = ("container__container_type",)

    container = models.OneToOneField(
        Container,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )

    @property
    def id(self) -> ID:
        return cast(Subsection.ID, self.publishable_entity_id)

    @override
    @classmethod
    def validate_entity(cls, entity: PublishableEntity) -> None:
        """Check if the given entity is allowed as a child of a Subsection"""
        # Subsections only allow Units as children, so the entity must be 1:1 with Container:
        if not hasattr(entity, "container"):
            raise ValidationError("Only Units can be added as children of a Subsection (found non-Container child)")
        if get_container_subclass_of(entity.container) is not Unit:
            raise ValidationError("Only Units can be added as children of a Subsection")


class SubsectionVersion(ContainerVersion):
    """
    A SubsectionVersion is a specific version of a :class:`Subsection`.

    Via :class:`ContainerVersion` and its :class:`EntityList`, it defines the
    list of :class:`Unit` children in this version of the
    :class:`Subsection`.
    """

    container_version = models.OneToOneField(
        ContainerVersion,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )

    @property
    def subsection(self) -> Subsection:
        """Convenience accessor to the Subsection this version is associated with"""
        return self.container_version.container.subsection  # pylint: disable=no-member
