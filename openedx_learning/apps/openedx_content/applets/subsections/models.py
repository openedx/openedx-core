"""
Models that implement subsections
"""
from django.db import models

from ..publishing.models import Container, ContainerVersion

__all__ = [
    "Subsection",
    "SubsectionVersion",
]


class Subsection(Container):
    """
    A Subsection is type of Container that holds Units.

    Via Container and its PublishableEntityMixin, Subsections are also publishable
    entities and can be added to other containers.
    """
    container = models.OneToOneField(
        Container,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )


class SubsectionVersion(ContainerVersion):
    """
    A SubsectionVersion is a specific version of a Subsection.

    Via ContainerVersion and its EntityList, it defines the list of Units
    in this version of the Subsection.
    """
    container_version = models.OneToOneField(
        ContainerVersion,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )

    @property
    def subsection(self):
        """ Convenience accessor to the Subsection this version is associated with """
        return self.container_version.container.subsection  # pylint: disable=no-member

    # Note: the 'publishable_entity_version' field is inherited and will appear on this model, but does not exist
    # in the underlying database table. It only exists in the ContainerVersion table.
    # You can verify this by running 'python manage.py sqlmigrate oel_subsections 0001_initial'
