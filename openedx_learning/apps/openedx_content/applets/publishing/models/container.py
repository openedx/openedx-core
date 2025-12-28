"""
Container and ContainerVersion models
"""
from django.core.exceptions import ValidationError
from django.db import models

from .entity_list import EntityList
from .publishable_entity import PublishableEntityMixin, PublishableEntityVersionMixin


class Container(PublishableEntityMixin):
    """
    A Container is a type of PublishableEntity that holds other
    PublishableEntities. For example, a "Unit" Container might hold several
    Components.

    For now, all containers have a static "entity list" that defines which
    containers/components/enities they hold. As we complete the Containers API,
    we will also add support for dynamic containers which may contain different
    entities for different learners or at different times.

    NOTE: We're going to want to eventually have some association between the
    PublishLog and Containers that were affected in a publish because their
    child elements were published.
    """


class ContainerVersion(PublishableEntityVersionMixin):
    """
    A version of a Container.

    By convention, we would only want to create new versions when the Container
    itself changes, and not when the Container's child elements change. For
    example:

    * Something was added to the Container.
    * We re-ordered the rows in the container.
    * Something was removed to the container.
    * The Container's metadata changed, e.g. the title.
    * We pin to different versions of the Container.

    The last looks a bit odd, but it's because *how we've defined the Unit* has
    changed if we decide to explicitly pin a set of versions for the children,
    and then later change our minds and move to a different set. It also just
    makes things easier to reason about if we say that entity_list never
    changes for a given ContainerVersion.
    """

    container = models.ForeignKey(
        Container,
        on_delete=models.CASCADE,
        related_name="versions",
    )

    # The list of entities (frozen and/or unfrozen) in this container
    entity_list = models.ForeignKey(
        EntityList,
        on_delete=models.RESTRICT,
        null=False,
        related_name="container_versions",
    )

    def clean(self):
        """
        Validate this model before saving. Not called normally, but will be
        called if anything is edited via a ModelForm like the Django admin.
        """
        super().clean()
        if self.container_id != self.publishable_entity_version.entity.container.pk:  # pylint: disable=no-member
            raise ValidationError("Inconsistent foreign keys to Container")
