"""
Entity List models
"""
from functools import cached_property

from django.db import models

from .publishable_entity import PublishableEntity, PublishableEntityVersion


class EntityList(models.Model):
    """
    EntityLists are a common structure to hold parent-child relations.

    EntityLists are not PublishableEntities in and of themselves. That's because
    sometimes we'll want the same kind of data structure for things that we
    dynamically generate for individual students (e.g. Variants). EntityLists are
    anonymous in a sense–they're pointed to by ContainerVersions and
    other models, rather than being looked up by their own identifiers.
    """

    @cached_property
    def rows(self):
        """
        Convenience method to iterate rows.

        I'd normally make this the reverse lookup name for the EntityListRow ->
        EntityList foreign key relation, but we already have references to
        entitylistrow_set in various places, and I thought this would be better
        than breaking compatibility.
        """
        return self.entitylistrow_set.order_by("order_num")


class EntityListRow(models.Model):
    """
    Each EntityListRow points to a PublishableEntity, optionally at a specific
    version.

    There is a row in this table for each member of an EntityList. The order_num
    field is used to determine the order of the members in the list.
    """

    entity_list = models.ForeignKey(EntityList, on_delete=models.CASCADE)

    # This ordering should be treated as immutable–if the ordering needs to
    # change, we create a new EntityList and copy things over.
    order_num = models.PositiveIntegerField()

    # Simple case would use these fields with our convention that null versions
    # means "get the latest draft or published as appropriate". These entities
    # could be Selectors, in which case we'd need to do more work to find the right
    # variant. The publishing app itself doesn't know anything about Selectors
    # however, and just treats it as another PublishableEntity.
    entity = models.ForeignKey(PublishableEntity, on_delete=models.RESTRICT)

    # The version references point to the specific PublishableEntityVersion that
    # this EntityList has for this PublishableEntity for both the draft and
    # published states. However, we don't want to have to create new EntityList
    # every time that a member is updated, because that would waste a lot of
    # space and make it difficult to figure out when the metadata of something
    # like a Unit *actually* changed, vs. when its child members were being
    # updated. Doing so could also potentially lead to race conditions when
    # updating multiple layers of containers.
    #
    # So our approach to this is to use a value of None (null) to represent an
    # unpinned reference to a PublishableEntity. It's shorthand for "just use
    # the latest draft or published version of this, as appropriate".
    entity_version = models.ForeignKey(
        PublishableEntityVersion,
        on_delete=models.RESTRICT,
        null=True,
        related_name="+",  # Do we need the reverse relation?
    )

    def is_pinned(self):
        return self.entity_version_id is not None

    def is_unpinned(self):
        return self.entity_version_id is None

    class Meta:
        ordering = ["order_num"]
        constraints = [
            # If (entity_list, order_num) is not unique, it likely indicates a race condition - so force uniqueness.
            models.UniqueConstraint(
                fields=["entity_list", "order_num"],
                name="oel_publishing_elist_row_order",
            ),
        ]
