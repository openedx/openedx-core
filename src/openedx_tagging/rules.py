"""
Django rules-based permissions for tagging
"""
from __future__ import annotations

from typing import Callable, Union

import django.contrib.auth.models
import rules
from attrs import define

from .models import Tag, Taxonomy

UserType = Union[
    django.contrib.auth.models.User, django.contrib.auth.models.AnonymousUser
]


# Global staff are taxonomy admins.
# (Superusers can already do anything)
is_taxonomy_admin: Callable[[UserType], bool] = rules.is_staff


@define
class ObjectTagPermissionItem:
    """
    Pair of taxonomy and object_id used for permission checking.
    """

    taxonomy: Taxonomy
    object_id: str


@rules.predicate
def can_view_taxonomy(user: UserType, taxonomy: Taxonomy | None = None) -> bool:
    """
    Anyone can view an enabled taxonomy or list all taxonomies,
    but only taxonomy admins can view a disabled taxonomy.
    """
    return not taxonomy or taxonomy.cast().enabled or is_taxonomy_admin(user)


@rules.predicate
def can_change_taxonomy(user: UserType, taxonomy: Taxonomy | None = None) -> bool:
    """
    Even taxonomy admins cannot change system taxonomies.
    """
    return is_taxonomy_admin(user) and (
        not taxonomy or bool(taxonomy and not taxonomy.cast().system_defined)
    )


@rules.predicate
def can_view_tag(user: UserType, tag: Tag | None = None) -> bool:
    """
    User can view tags for any taxonomy they can view.
    """
    taxonomy = tag.taxonomy.cast() if (tag and tag.taxonomy) else None
    return user.has_perm(
        "oel_tagging.view_taxonomy",
        taxonomy,
    )


@rules.predicate
def can_change_tag(user: UserType, tag: Tag | None = None) -> bool:
    """
    Users can change tags for any taxonomy they can modify.
    """
    taxonomy = tag.taxonomy.cast() if (tag and tag.taxonomy) else None
    return user.has_perm(
        "oel_tagging.change_taxonomy",
        taxonomy,
    )


@rules.predicate
def can_view_object_tag_taxonomy(user: UserType, taxonomy: Taxonomy) -> bool:
    """
    Only enabled taxonomy and users with permission to view this taxonomy can view object tags
    from that taxonomy.

    This rule is different from can_view_taxonomy because it checks if the taxonomy is enabled.
    """
    if not taxonomy:
        return True

    return taxonomy.cast().enabled and can_view_taxonomy(user, taxonomy)


@rules.predicate
def can_view_object_tag_objectid(_user: UserType, _object_id: str) -> bool:
    """
    Everybody can view object tags from any objects.

    This rule could be defined in other apps for proper permission checking.
    """
    return True


@rules.predicate
def can_view_object_tag(
    user: UserType, perm_obj: ObjectTagPermissionItem | None = None
) -> bool:
    """
    Checks if the user has permissions to view tags on the given taxonomy and object_id.
    """

    # The following code allows METHOD permission (GET) in the viewset for everyone
    if perm_obj is None:
        return True

    # Checks the permission for the taxonomy
    taxonomy_perm = user.has_perm(
        "oel_tagging.view_objecttag_taxonomy", perm_obj.taxonomy  # type: ignore[arg-type]
    )
    if not taxonomy_perm:
        return False

    # Checks the permission for the object_id
    objectid_perm = user.has_perm(
        "oel_tagging.view_objecttag_objectid",
        # The obj arg expects an object, but we are passing a string
        perm_obj.object_id,  # type: ignore[arg-type]
    )
    return objectid_perm


@rules.predicate
def can_change_object_tag_objectid(_user: UserType, _object_id: str) -> bool:
    """
    Nobody can create or modify object tags without checking the permission for the tagged object.

    This rule should be defined in other apps for proper permission checking.
    """
    return False


@rules.predicate
def can_remove_object_tag_objectid(_user: UserType, _object_id: str) -> bool:
    """
    Nobody can remove object tags without checking the permission for the tagged object.

    This rule could be defined in other apps for proper permission checking.
    """
    return can_change_object_tag_objectid(_user, _object_id)


@rules.predicate
def can_change_object_tag(
    user: UserType, perm_obj: ObjectTagPermissionItem | None = None
) -> bool:
    """
    Checks if the user has permissions to create or modify tags on the given taxonomy and object_id.
    """

    # The following code allows METHOD permission (PUT) in the viewset for everyone
    if perm_obj is None:
        return True

    # Checks the permission for the taxonomy
    taxonomy_perm = user.has_perm(
        "oel_tagging.change_objecttag_taxonomy", perm_obj.taxonomy  # type: ignore[arg-type]
    )
    if not taxonomy_perm:
        return False

    # Checks the permission for the object_id
    objectid_perm = user.has_perm(
        "oel_tagging.change_objecttag_objectid",
        # The obj arg expects an object, but we are passing a string
        perm_obj.object_id,  # type: ignore[arg-type]
    )

    return objectid_perm


# Taxonomy
rules.add_perm("oel_tagging.add_taxonomy", can_change_taxonomy)
rules.add_perm("oel_tagging.change_taxonomy", can_change_taxonomy)
rules.add_perm("oel_tagging.delete_taxonomy", can_change_taxonomy)
rules.add_perm("oel_tagging.view_taxonomy", can_view_taxonomy)

# Tag
rules.add_perm("oel_tagging.add_tag", can_change_tag)
rules.add_perm("oel_tagging.change_tag", can_change_tag)
rules.add_perm("oel_tagging.delete_tag", can_change_tag)
rules.add_perm("oel_tagging.view_tag", can_view_tag)
# Special Case for listing Tags, we check if we can view the Taxonomy since
# that is what is passed in rather than a Tag object
rules.add_perm("oel_tagging.list_tag", can_view_taxonomy)

# ObjectTag
rules.add_perm("oel_tagging.add_objecttag", can_change_object_tag)
rules.add_perm("oel_tagging.change_objecttag", can_change_object_tag)
rules.add_perm("oel_tagging.delete_objecttag", can_change_object_tag)
rules.add_perm("oel_tagging.view_objecttag", can_view_object_tag)
# If a user "can tag object", they can delete or create ObjectTags using the given Taxonomy + object_id.
rules.add_perm("oel_tagging.can_tag_object", can_change_object_tag)

# Users can tag objects using tags from any taxonomy that they have permission to view
rules.add_perm("oel_tagging.view_objecttag_objectid", can_view_object_tag_objectid)
rules.add_perm("oel_tagging.view_objecttag_taxonomy", can_view_object_tag_taxonomy)
rules.add_perm("oel_tagging.change_objecttag_taxonomy", can_view_object_tag_taxonomy)
rules.add_perm("oel_tagging.change_objecttag_objectid", can_change_object_tag_objectid)
rules.add_perm("oel_tagging.remove_objecttag_objectid", can_remove_object_tag_objectid)
