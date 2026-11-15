"""
Models that implement sections
"""

from typing import NewType, cast, override

from django.core.exceptions import ValidationError
from django.db import models

from ..containers.api import get_container_subclass_of
from ..containers.models import Container, ContainerVersion
from ..publishing.models import PublishableEntity
from ..subsections.models import Subsection

__all__ = [
    "Section",
    "SectionVersion",
]


@Container.register_subclass
class Section(Container):
    """
    A Section is type of :class:`Container` that holds :class:`Subsection`
    children.

    Via :class:`Container` and its :class:`PublishableEntityMixin`, Sections
    are also publishable entities and can be added to other containers.
    """

    SectionID = NewType("SectionID", Container.ID)
    type ID = SectionID

    type_code = "section"
    olx_tag_name = "chapter"  # Serializes to OLX as `<chapter>...</chapter>`.
    validate_entity_select_related = ("container__container_type",)

    container = models.OneToOneField(
        Container,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )

    @property
    def id(self) -> ID:
        return cast(Section.ID, self.publishable_entity_id)

    @override
    @classmethod
    def validate_entity(cls, entity: PublishableEntity) -> None:
        """Check if the given entity is allowed as a child of a Section"""
        # Sections only allow Subsections as children, so the entity must be 1:1 with Container:
        if not hasattr(entity, "container"):
            raise ValidationError("Only Units can be added as children of a Subsection (found non-Container child)")
        if get_container_subclass_of(entity.container) is not Subsection:
            raise ValidationError("Only Subsection can be added as children of a Section")


class SectionVersion(ContainerVersion):
    """
    A SectionVersion is a specific version of a :class:`Section`.

    Via :class:`ContainerVersion` and its :class:`EntityList`, it defines the
    list of :class:`Subsection` children in this version of the
    :class:`Section`.
    """

    container_version = models.OneToOneField(
        ContainerVersion,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )

    @property
    def section(self) -> Section:
        """Convenience accessor to the Section this version is associated with"""
        return self.container_version.container.section  # pylint: disable=no-member
