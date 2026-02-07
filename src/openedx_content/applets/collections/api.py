"""
Collections API (warning: UNSTABLE, in progress API)
"""
from __future__ import annotations

from datetime import datetime, timezone

from django.core.exceptions import ValidationError
from django.db.models import QuerySet

from ..publishing import api as publishing_api
from ..publishing.models import PublishableEntity
from .models import Collection, CollectionPublishableEntity

# The public API that will be re-exported by openedx_content.api
# is listed in the __all__ entries below. Internal helper functions that are
# private to this module should start with an underscore. If a function does not
# start with an underscore AND it is not in __all__, that function is considered
# to be callable only by other apps in the authoring package.
__all__ = [
    "add_to_collection",
    "create_collection",
    "delete_collection",
    "get_collection",
    "get_collections",
    "get_entity_collections",
    "remove_from_collection",
    "restore_collection",
    "update_collection",
    "set_collections",
]


def create_collection(
    learning_package_id: int,
    key: str,
    *,
    title: str,
    created_by: int | None,
    description: str = "",
    enabled: bool = True,
) -> Collection:
    """
    Create a new Collection
    """
    collection = Collection.objects.create(
        learning_package_id=learning_package_id,
        key=key,
        title=title,
        created_by_id=created_by,
        description=description,
        enabled=enabled,
    )
    return collection


def get_collection(learning_package_id: int, collection_key: str) -> Collection:
    """
    Get a Collection by ID
    """
    return Collection.objects.get_by_key(learning_package_id, collection_key)


def update_collection(
    learning_package_id: int,
    key: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> Collection:
    """
    Update a Collection identified by the learning_package_id + key.
    """
    collection = get_collection(learning_package_id, key)

    # If no changes were requested, there's nothing to update, so just return
    # the Collection as-is
    if all(field is None for field in [title, description]):
        return collection

    if title is not None:
        collection.title = title
    if description is not None:
        collection.description = description

    collection.save()
    return collection


def delete_collection(
    learning_package_id: int,
    key: str,
    *,
    hard_delete=False,
) -> Collection:
    """
    Disables or deletes a collection identified by the given learning_package + key.

    By default (hard_delete=False), the collection is "soft deleted", i.e disabled.
    Soft-deleted collections can be re-enabled using restore_collection.
    """
    collection = get_collection(learning_package_id, key)

    if hard_delete:
        collection.delete()
    else:
        collection.enabled = False
        collection.save()
    return collection


def restore_collection(
    learning_package_id: int,
    key: str,
) -> Collection:
    """
    Undo a "soft delete" by re-enabling a Collection.
    """
    collection = get_collection(learning_package_id, key)

    collection.enabled = True
    collection.save()
    return collection


def add_to_collection(
    learning_package_id: int,
    key: str,
    entities_qset: QuerySet[PublishableEntity],
    created_by: int | None = None,
) -> Collection:
    """
    Adds a QuerySet of PublishableEntities to a Collection.

    These Entities must belong to the same LearningPackage as the Collection, or a ValidationError will be raised.

    PublishableEntities already in the Collection are silently ignored.

    The Collection object's modified date is updated.

    Returns the updated Collection object.
    """
    # Disallow adding entities outside the collection's learning package
    invalid_entity = entities_qset.exclude(learning_package_id=learning_package_id).first()
    if invalid_entity:
        raise ValidationError(
            f"Cannot add entity {invalid_entity.pk} in learning package {invalid_entity.learning_package_id} "
            f"to collection {key} in learning package {learning_package_id}."
        )

    collection = get_collection(learning_package_id, key)
    collection.entities.add(
        *entities_qset.all(),
        through_defaults={"created_by_id": created_by},
    )
    collection.modified = datetime.now(tz=timezone.utc)
    collection.save()

    return collection


def remove_from_collection(
    learning_package_id: int,
    key: str,
    entities_qset: QuerySet[PublishableEntity],
) -> Collection:
    """
    Removes a QuerySet of PublishableEntities from a Collection.

    PublishableEntities are deleted (in bulk).

    The Collection's modified date is updated (even if nothing was removed).

    Returns the updated Collection.
    """
    collection = get_collection(learning_package_id, key)

    collection.entities.remove(*entities_qset.all())
    collection.modified = datetime.now(tz=timezone.utc)
    collection.save()

    return collection


def get_entity_collections(learning_package_id: int, entity_key: str) -> QuerySet[Collection]:
    """
    Get all collections in the given learning package which contain this entity.

    Only enabled collections are returned.
    """
    entity = publishing_api.get_publishable_entity_by_key(
        learning_package_id,
        key=entity_key,
    )
    return entity.collections.filter(enabled=True).order_by("pk")


def get_collections(learning_package_id: int, enabled: bool | None = True) -> QuerySet[Collection]:
    """
    Get all collections for a given learning package

    Enabled collections are returned by default.
    """
    qs = Collection.objects.filter(learning_package_id=learning_package_id)
    if enabled is not None:
        qs = qs.filter(enabled=enabled)
    return qs.select_related("learning_package").order_by('pk')


def set_collections(
    publishable_entity: PublishableEntity,
    collection_qset: QuerySet[Collection],
    created_by: int | None = None,
) -> set[Collection]:
    """
    Set collections for a given publishable entity.

    These Collections must belong to the same LearningPackage as the PublishableEntity,
    or a ValidationError will be raised.

    Modified date of all collections related to entity is updated.

    Returns the updated collections.
    """
    # Disallow adding entities outside the collection's learning package
    if collection_qset.exclude(learning_package_id=publishable_entity.learning_package_id).count():
        raise ValidationError(
            "Collection entities must be from the same learning package as the collection.",
        )
    current_relations = CollectionPublishableEntity.objects.filter(
        entity=publishable_entity
    ).select_related('collection')
    # Clear other collections for given entity and add only new collections from collection_qset
    removed_collections = set(
        r.collection for r in current_relations.exclude(collection__in=collection_qset)
    )
    new_collections = set(collection_qset.exclude(
        id__in=current_relations.values_list('collection', flat=True)
    ))
    # Triggers a m2m_changed signal
    publishable_entity.collections.set(
        objs=collection_qset,
        through_defaults={"created_by_id": created_by},
    )
    # Update modified date via update to avoid triggering post_save signal for all collections, which can be very slow.
    affected_collection = removed_collections | new_collections
    Collection.objects.filter(
        id__in=[collection.id for collection in affected_collection]
    ).update(modified=datetime.now(tz=timezone.utc))

    return affected_collection
