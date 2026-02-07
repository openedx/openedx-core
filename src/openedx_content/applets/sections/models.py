"""
Models that implement sections
"""
from django.db import models

from ..publishing.models import Container, ContainerVersion

__all__ = [
    "Section",
    "SectionVersion",
]


class Section(Container):
    """
    A Section is type of Container that holds Units.

    Via Container and its PublishableEntityMixin, Sections are also publishable
    entities and can be added to other containers.
    """
    container = models.OneToOneField(
        Container,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )


class SectionVersion(ContainerVersion):
    """
    A SectionVersion is a specific version of a Section.

    Via ContainerVersion and its EntityList, it defines the list of Units
    in this version of the Section.
    """
    container_version = models.OneToOneField(
        ContainerVersion,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )

    @property
    def section(self):
        """ Convenience accessor to the Section this version is associated with """
        return self.container_version.container.section  # pylint: disable=no-member

    # Note: the 'publishable_entity_version' field is inherited and will appear on this model, but does not exist
    # in the underlying database table. It only exists in the ContainerVersion table.
    # You can verify this by running 'python manage.py sqlmigrate oel_sections 0001_initial'
