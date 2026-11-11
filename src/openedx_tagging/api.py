""""
Tagging API

Anyone using the openedx_tagging app should use these APIs instead of creating
or modifying the models directly, since there might be other related model
changes that you may not know about.

No permissions/rules are enforced by these methods -- these must be enforced in the views.

Please look at the models.py file for more information about the kinds of data
are stored in this app.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from enum import Enum
from typing import Any, Counter, cast

from django.db import models, router, transaction
from django.db.models import F, QuerySet, Value
from django.db.models.functions import Coalesce, Concat, Lower
from django.utils.text import slugify
from django.utils.translation import gettext as _

from .data import TagDataQuerySet
from .models import ObjectTag, Tag, Taxonomy

# Export this as part of the API
TagDoesNotExist = Tag.DoesNotExist
ObjectTagDoesNotExist = ObjectTag.DoesNotExist

# Maximum number of tags allowed on any one object
OBJECT_MAX_TAGS = 100


class TaxonomyType(Enum):
    """
    Valid values for a taxonomy's type on create.
    """

    TAGS = "tags"
    COMPETENCY = "competency"


def create_taxonomy(  # pylint: disable=too-many-positional-arguments
    name: str,
    description: str | None = None,
    enabled=True,
    allow_multiple=True,
    allow_free_text=False,
    read_only=False,
    export_id: str | None = None,
    *,
    taxonomy_cls: type[Taxonomy] = Taxonomy,
) -> Taxonomy:
    """
    Creates, saves, and returns a new Taxonomy with the given attributes.

    If `export_id` is not given, one is auto-generated from the current
    Taxonomy count and a slug of `name`.

    Pass `taxonomy_cls` to create a subclass instance instead (e.g. a
    multi-table-inheritance child): building it fresh with every field, in one
    full_clean()+save(), writes both tables correctly without a separate step.
    """
    if not export_id:
        export_id = f"{Taxonomy.objects.count() + 1}-{slugify(name, allow_unicode=True)}"

    taxonomy = taxonomy_cls(
        name=name,
        description=description or "",
        enabled=enabled,
        allow_multiple=allow_multiple,
        allow_free_text=allow_free_text,
        read_only=read_only,
        export_id=export_id,
    )

    taxonomy.full_clean()
    taxonomy.save()
    return taxonomy


def get_taxonomy(taxonomy_id: int) -> Taxonomy | None:
    """
    Returns the Taxonomy which has the given ID, or None if not found or archived.
    """
    return Taxonomy.objects.unarchived().filter(pk=taxonomy_id).first()


def get_taxonomy_by_export_id(taxonomy_export_id: str) -> Taxonomy | None:
    """
    Returns the Taxonomy which has the given export ID, or None if not found or archived.
    """
    return Taxonomy.objects.unarchived().filter(export_id=taxonomy_export_id).first()


def get_taxonomies(enabled=True) -> QuerySet[Taxonomy]:
    """
    Returns a queryset containing the enabled, unarchived taxonomies, sorted by name.

    We return a QuerySet here for ease of use with Django Rest Framework and other query-based use cases.

    If you want the disabled taxonomies, pass enabled=False.
    If you want all taxonomies (both enabled and disabled), pass enabled=None.
    """
    queryset = Taxonomy.objects.unarchived().order_by("name", "id")
    if enabled is None:
        return queryset.all()
    return queryset.filter(enabled=enabled)


def get_tags(taxonomy: Taxonomy) -> TagDataQuerySet:
    """
    Returns a QuerySet of all the unarchived tags in the given taxonomy.

    Note that if the taxonomy is dynamic or free-text, only tags that have
    already been applied to some object will be returned.
    """
    return taxonomy.get_filtered_tags()


def get_root_tags(taxonomy: Taxonomy) -> TagDataQuerySet:
    """
    Returns a list of the root tags for the given taxonomy.

    Note that if the taxonomy allows free-text tags, then the returned list will be empty.
    """
    return taxonomy.get_filtered_tags(depth=1)


def search_tags(
    taxonomy: Taxonomy,
    search_term: str,
    exclude_object_id: str | None = None,
) -> TagDataQuerySet:
    """
    Returns a list of all tags that contains `search_term` of the given
    taxonomy, as well as their ancestors (so they can be displayed in a tree).

    If exclude_object_id is set, any tags applied to that object will be
    excluded from the results, e.g. to power an autocomplete search when adding
    additional tags to an object.
    """
    excluded_values = None
    if exclude_object_id:
        # Fetch tags that the object already has to exclude them from the result.
        # Note: this adds a fair bit of complexity. In the future, maybe we can just do this filtering on the frontend?
        excluded_values = list(
            taxonomy.objecttag_set.unarchived().filter(object_id=exclude_object_id).values_list(
                "_value", flat=True
            )
        )
    qs = taxonomy.get_filtered_tags(
        search_term=search_term,
        excluded_values=excluded_values,
    )
    return qs


def get_children_tags(
    taxonomy: Taxonomy,
    parent_tag_value: str,
) -> TagDataQuerySet:
    """
    Returns a QuerySet of unarchived children tags for the given parent tag.

    Raises Tag.DoesNotExist if the parent tag does not exist, or Tag.IsArchived if it is archived.

    Note that if the taxonomy allows free-text tags, then the returned list will be empty.
    """
    return taxonomy.get_filtered_tags(parent_tag_value=parent_tag_value, depth=1)


def get_ancestor_tags(tag: Tag) -> QuerySet[Tag]:
    """
    Return every ancestor of ``tag`` (its parent, grandparent, ... up to the root).

    Returns an empty queryset if ``tag``'s taxonomy is unset or allows free text, since
    free-text tags have no taxonomy-defined hierarchy.
    """
    if tag.taxonomy is None or tag.taxonomy.allow_free_text:
        return Tag.objects.none()
    return Tag.objects.filter(taxonomy=tag.taxonomy, value__in=tag.get_lineage()[:-1])


def get_descendant_tags(tag: Tag) -> QuerySet[Tag]:
    """
    Return every descendant of ``tag`` at any depth, excluding ``tag`` itself.

    Returns an empty queryset if ``tag``'s taxonomy is unset or allows free text, since
    free-text tags have no taxonomy-defined hierarchy.
    """
    if tag.taxonomy is None or tag.taxonomy.allow_free_text:
        return Tag.objects.none()
    return Tag.objects.filter(taxonomy=tag.taxonomy, depth__gt=tag.depth, lineage__startswith=tag.lineage)


def resync_object_tags(object_tags: QuerySet | None = None) -> int:
    """
    Reconciles ObjectTag entries with any changes made to their associated taxonomies and tags.

    By default, we iterate over all ObjectTags. Pass a filtered ObjectTags queryset to limit which tags are resynced.
    """
    if not object_tags:
        object_tags = ObjectTag.objects.select_related("tag", "taxonomy")

    num_changed = 0
    for object_tag in object_tags:
        changed = object_tag.resync()
        if changed:
            object_tag.save()
            num_changed += 1

    return num_changed


def get_object_tags(
    object_id: str,
    taxonomy_id: int | None = None,
    include_deleted: bool = False,
) -> QuerySet[ObjectTag]:
    """
    Returns a Queryset of object tags for a given object.

    Archived object tags are excluded, as are those whose tag or taxonomy is archived.

    Pass taxonomy_id to limit the returned object_tags to a specific taxonomy.
    """
    filters = {"taxonomy_id": taxonomy_id} if taxonomy_id else {}
    base_qs = (
        ObjectTag.objects
        .unarchived()
        .filter(object_id=object_id, **filters)
        .exclude(taxonomy__enabled=False)  # Exclude if the whole taxonomy is disabled
    )
    if not include_deleted:
        # Exclude if the whole taxonomy was deleted
        base_qs = base_qs.exclude(taxonomy=None)
        # Exclude if just the tag is deleted
        base_qs = base_qs.exclude(tag=None, taxonomy__allow_free_text=False)
    tags = (
        base_qs
        # Preload related objects, including data for the "get_lineage" method on ObjectTag/Tag:
        .select_related("taxonomy", "tag")
        # Sort the tags within each taxonomy in "tree order". See Taxonomy._get_filtered_tags_deep for details on this.
        # tag__lineage is a case-insensitive column storing the full ancestor path, e.g. "Root\tParent\tThis\t".
        # Free-text and deleted tags (tag_id IS NULL) fall back to their cached _value.
        .annotate(sort_key=Coalesce(
            Lower(F("tag__lineage")),
            Lower(Concat(F("_value"), Value("\t"))),
            output_field=models.TextField(),
        ))
        .annotate(taxonomy_name=Coalesce(F("taxonomy__name"), F("_export_id")))
        # Sort first by taxonomy name, then by tag value in tree order:
        .order_by("taxonomy_name", "sort_key")
    )
    return tags


def get_object_tag_counts(object_id_pattern: str, count_implicit=False) -> dict[str, int]:
    """
    Given an object ID, a "starts with" glob pattern like
    "course-v1:foo+bar+baz@*", or a list of "comma,separated,IDs", return a
    dict of matching object IDs and how many tags each object has.

    Deleted tags, disabled taxonomies, and archived object tags (including those
    whose tag or taxonomy is archived) are excluded from the counts, even if
    ObjectTag data about them is present.

    count_implicit: if True, this means to count all ancestor tags (implicit
    tags) of the explict tags that are associated with the object.
    """
    # Note: in the future we may add an option to exclude system taxonomies from the count.
    qs: Any = ObjectTag.objects.unarchived()
    if object_id_pattern.endswith("*"):
        qs = qs.filter(object_id__startswith=object_id_pattern[0:len(object_id_pattern) - 1])
    elif "*" in object_id_pattern:
        raise ValueError("Wildcard matches are only supported if the * is at the end.")
    else:
        qs = qs.filter(object_id__in=object_id_pattern.split(","))
    # Don't include deleted tags or disabled taxonomies:
    qs = qs.exclude(taxonomy_id=None)  # The whole taxonomy was deleted
    qs = qs.exclude(taxonomy__enabled=False)  # The whole taxonomy is disabled
    qs = qs.exclude(tag_id=None, taxonomy__allow_free_text=False)  # The taxonomy exists but the tag is deleted
    if count_implicit:
        # Use tag__lineage to count implicit (ancestor) tags at any depth.
        # Each tag's lineage encodes its full ancestry path, e.g. "root\tchild\tgrandchild\t".
        # Every prefix of that path (at each \t boundary) uniquely identifies one tag in the chain,
        # so collecting prefixes into a set naturally deduplicates shared ancestors across multiple
        # tags on the same object.
        qs = qs.annotate(sort_key=F("tag__lineage")).values("object_id", "sort_key")
        result: dict = {}
        for row in qs:
            object_id = row["object_id"]
            if object_id not in result:
                result[object_id] = {"free_text": 0, "paths": set()}
            sort_key = row["sort_key"]
            if sort_key is None:
                # Free-text tag: no Tag record, so no sort_key
                result[object_id]["free_text"] += 1
            else:
                # Add the sort_key prefix for each ancestor level
                parts = sort_key.rstrip("\t").split("\t")
                for i in range(1, len(parts) + 1):
                    result[object_id]["paths"].add("\t".join(parts[:i]))
        return {
            object_id: data["free_text"] + len(data["paths"])
            for object_id, data in result.items()
        }
    else:
        qs = qs.values("object_id").annotate(num_tags=models.Count("id")).order_by("object_id")
        return {row["object_id"]: row["num_tags"] for row in qs}


def delete_object_tags(object_id: str, *, keep_hidden: bool = False) -> None:
    """
    Delete the ObjectTag entries for a given object.

    Locked associations (``deletion_locked=True``) are archived instead of deleted, whatever ``keep_hidden`` is.

    :param object_id: The object whose associations are deleted.
    :param keep_hidden: If False, all of the object's associations are deleted, including archived ones and ones whose
        tag or taxonomy is archived. If True, only the associations that :meth:`ObjectTagQuerySet.unarchived` returns
        are deleted, and the hidden ones are left untouched: they are not deleted, not archived again, and their
        ``deletion_locked`` value is unchanged.
    """
    if not keep_hidden:
        ObjectTag.objects.filter(object_id=object_id).delete()
        return

    pks = list(ObjectTag.objects.filter(object_id=object_id).unarchived().values_list("pk", flat=True))
    # Delete by primary key: the queryset delete locks the rows it selects, and unarchived() joins the tag and taxonomy
    # tables, so deleting the joined queryset would lock shared tag and taxonomy rows on MariaDB.
    ObjectTag.objects.filter(pk__in=pks).delete()


def _check_new_tag_count(
    new_tag_count: int,
    taxonomy: Taxonomy | None,
    object_id: str,
    taxonomy_export_id: str | None = None,
) -> None:
    """
    Checks if the new count of tags for the object is equal or less than OBJECT_MAX_TAGS

    Archived associations don't count toward the limit.
    """
    # Exclude to avoid counting the tags that are going to be updated
    active_tags = ObjectTag.objects.unarchived().filter(object_id=object_id)
    if taxonomy:
        current_count = active_tags.exclude(taxonomy_id=taxonomy.id).count()
    else:
        current_count = active_tags.exclude(_export_id=taxonomy_export_id).count()

    _check_object_tag_limit(current_count, new_tag_count, object_id)


def _check_object_tag_limit(current_count: int, new_tag_count: int, object_id: str) -> None:
    """
    Raises ValueError if ``new_tag_count`` tags on top of ``current_count`` would exceed OBJECT_MAX_TAGS.
    """
    if current_count + new_tag_count > OBJECT_MAX_TAGS:
        raise ValueError(
            _("Cannot add more than {limit} tags to ({object_id}).").format(object_id=object_id, limit=OBJECT_MAX_TAGS)
        )


def _check_taxonomy_allows_tag_count(taxonomy: Taxonomy, tag_count: int) -> None:
    """
    Raises ValueError if ``tag_count`` tags on one object is more than the taxonomy allows.
    """
    if not taxonomy.allow_multiple and tag_count > 1:
        raise ValueError(_("Taxonomy ({name}) only allows one tag per object.").format(name=taxonomy.name))


def _get_current_tags(
    taxonomy: Taxonomy | None,
    tags: list[str],
    object_id: str,
    taxonomy_export_id: str | None = None,
) -> list[ObjectTag]:
    """
    Returns the current object tags of the related object_id with taxonomy
    """
    if taxonomy:
        _check_taxonomy_allows_tag_count(taxonomy, len(tags))
        current_tags = list(
           ObjectTag.objects.filter(taxonomy=taxonomy, object_id=object_id)
        )
    else:
        current_tags = list(
            ObjectTag.objects.filter(_export_id=taxonomy_export_id, object_id=object_id)
        )
    return current_tags


def _pop_current_tag(
    current_tags: list[ObjectTag],
    unarchive_pks: list[int],
    updated_tags: list[ObjectTag],
    value: str,
    tag_id: int | None = None,
) -> bool:
    """
    Find the existing association for a re-applied tag, so it can be reused instead of recreated.

    Matches on ``tag_id`` when one is given. Otherwise matches an association with no tag whose value equals
    ``value`` under the database's collation for ``_value``, so it agrees with the unique constraint. If several
    match, which only the no-taxonomy path allows, it prefers an exact spelling, then a locked one, then the lowest
    pk. The match is removed from ``current_tags``, added to ``unarchive_pks`` if it is archived, and added to
    ``updated_tags`` if its stored value differs from ``value``.

    :return: ``True`` if an existing association matched, ``False`` if the caller needs to create one.
    """
    if tag_id is not None:
        matches = [t for t in current_tags if t.tag_id == tag_id]
    else:
        candidates = [t for t in current_tags if t.tag_id is None]
        # An identical spelling is equal under any collation, so only ask the database when there isn't one.
        matches = [t for t in candidates if t.value == value]
        if not matches and candidates:
            equal_pks = set(
                ObjectTag.objects.using(router.db_for_write(ObjectTag))
                .filter(pk__in=[t.pk for t in candidates], _value=value)
                .values_list("pk", flat=True)
            )
            matches = [t for t in candidates if t.pk in equal_pks]
    if not matches:
        return False
    # Only value matches can be ambiguous, and they have no tag, so .value doesn't query for one.
    object_tag = matches[0] if tag_id is not None else min(
        matches, key=lambda t: (t.value != value, not t.deletion_locked, t.pk)
    )
    current_tags.remove(object_tag)
    if object_tag.archived:
        unarchive_pks.append(object_tag.pk)
        # Also clear it in memory, so a later save() of this instance can't write archived=True back.
        object_tag.archived = False
    if object_tag._value != value:  # pylint: disable=protected-access
        # The ObjectTag's cached '_value' is out of sync with the Tag or the casing, so update it:
        object_tag._value = value  # pylint: disable=protected-access
        updated_tags.append(object_tag)
    return True


def _save_object_tags(removed: list[ObjectTag], unarchive_pks: list[int], updated: list[ObjectTag]) -> None:
    """
    Apply the changes that :func:`tag_object` worked out, all in one transaction to avoid partial updates.
    """
    with transaction.atomic():
        # Remove first to reduce the chances of UNIQUE constraint edge cases. Locked associations get archived.
        if removed:
            ObjectTag.objects.filter(pk__in=[object_tag.pk for object_tag in removed]).delete()
        if unarchive_pks:
            ObjectTag.objects.filter(pk__in=unarchive_pks).update(archived=False)
        for object_tag in updated:
            object_tag.full_clean()  # Run validation
            object_tag.save()


def tag_object(
    object_id: str,
    taxonomy: Taxonomy | None,
    tags: list[str],
    create_invalid: bool = False,
    taxonomy_export_id: str | None = None,
) -> None:
    """
    Replaces the existing ObjectTag entries for the given taxonomy + object_id
    with the given list of tags.

    tags: A list of the values of the tags from this taxonomy to apply.

    Raised Tag.DoesNotExist if the proposed tags are invalid for this taxonomy.
    Preserves existing (valid) tags, adds new (valid) tags, and removes omitted
    (or invalid) tags.
    create_invalid: You can create invalid tags and avoid the previous behavior using.

    taxonomy_export_id: You can create object tags without taxonomy using this param
    and `taxonomy` as None. You need to use the taxonomy.export_id, so you can resync
    this object tag if the taxonomy is created in the future.

    Re-applying a tag whose association is archived un-archives that same row and leaves ``deletion_locked``
    unchanged. Omitted associations that are locked (``deletion_locked=True``) are archived instead of deleted.
    Omitted associations that are archived, or whose tag or taxonomy is archived (the ones
    :meth:`ObjectTagQuerySet.unarchived` excludes), are left alone, because a caller that builds its list from the
    filtered read paths never saw them.
    """
    if not isinstance(tags, list):
        raise ValueError(_("Tags must be a list, not {type}.").format(type=type(tags).__name__))

    tags = list(dict.fromkeys(tags))  # Remove duplicates preserving order

    if not taxonomy and not taxonomy_export_id:
        raise ValueError("`taxonomy_export_id` can't be None if `taxonomy` is None")

    _check_new_tag_count(len(tags), taxonomy, object_id, taxonomy_export_id)
    current_tags = _get_current_tags(
        taxonomy,
        tags,
        object_id,
        taxonomy_export_id
    )

    updated_tags: list[ObjectTag] = []
    unarchive_pks: list[int] = []
    if taxonomy and taxonomy.allow_free_text:
        for tag_value in tags:
            if not _pop_current_tag(current_tags, unarchive_pks, updated_tags, value=tag_value):
                object_tag = ObjectTag(taxonomy=taxonomy, object_id=object_id, _value=tag_value)
                updated_tags.append(object_tag)
    else:
        # Handle closed taxonomies:
        for tag_value in tags:
            tag = None
            # When export, sometimes, the value has a space at the beginning and end.
            tag_value = tag_value.strip()
            if taxonomy:
                try:
                    tag = taxonomy.tag_for_value(tag_value)  # Will raise Tag.DoesNotExist if the value is invalid.
                except Tag.DoesNotExist as e:
                    if not create_invalid:
                        raise e

            if tag:
                # Tag exists in the taxonomy
                # If this tag is already applied, the helper also fixes the row's cached '_value' if it's out of sync.
                if not _pop_current_tag(current_tags, unarchive_pks, updated_tags, value=tag.value, tag_id=tag.id):
                    # We are newly applying this tag:
                    object_tag = ObjectTag(taxonomy=taxonomy, object_id=object_id, tag=tag)
                    updated_tags.append(object_tag)
            elif taxonomy:
                # Tag doesn't exist in the taxonomy and `create_invalid` is True
                if not _pop_current_tag(current_tags, unarchive_pks, updated_tags, value=tag_value):
                    object_tag = ObjectTag(taxonomy=taxonomy, object_id=object_id, _value=tag_value)
                    updated_tags.append(object_tag)
            else:
                # Taxonomy is None (also tag doesn't exist)
                if taxonomy_export_id and not _pop_current_tag(
                    current_tags, unarchive_pks, updated_tags, value=tag_value
                ):
                    # `taxonomy_export_id` will always be true, since it is verified at the beginning of the function.
                    # This condition is placed by the type checks.
                    object_tag = ObjectTag(
                        taxonomy=None,
                        object_id=object_id,
                        _value=tag_value,
                        _export_id=taxonomy_export_id
                    )
                    updated_tags.append(object_tag)

    # The read paths hide a row that is archived, or whose tag or taxonomy is archived, so a caller that builds its
    # list from them never saw such a row, and removing it would delete or re-archive something the caller can't see.
    # Only the rows that `unarchived()` returns are removed. A row hidden for another reason, such as a disabled
    # taxonomy, is still removed.
    active_pks = set(
        ObjectTag.objects.filter(pk__in=[t.pk for t in current_tags]).unarchived().values_list("pk", flat=True)
    )
    removed_tags = [t for t in current_tags if t.pk in active_pks]
    _save_object_tags(removed_tags, unarchive_pks, updated_tags)


def add_tag_to_object(object_id: str, tag: Tag) -> ObjectTag:
    """
    Adds one existing ``tag`` to ``object_id``, and returns its ObjectTag.

    UNSTABLE: this function is new, so its shape may change without a deprecation cycle.

    Unlike :func:`tag_object`, no other ObjectTag of the object is read for replacement or changed,
    so a concurrent write of another tag on the same object, even one in the same taxonomy, is not
    undone by this call. If ``tag`` is already applied to the object, its existing ObjectTag is
    returned and nothing is written; an archived ObjectTag for the same object and tag is revived in
    place (``archived=False``) and returned, keeping its ``deletion_locked`` flag, because the unique
    constraint allows one row per object and tag and a new row would drop that lock.

    Raises ValueError if the taxonomy of ``tag`` allows only one tag per object and the object
    already has a different tag in it, or if the object already has the maximum number of tags
    (``OBJECT_MAX_TAGS``). Both use the same messages as :func:`tag_object`, and as there, archived
    ObjectTags do not count toward either limit. A revive meets the same limits as a new tag, since
    the object may have been tagged since the archive. Raises ValueError if ``tag`` does not belong
    to a taxonomy. Raises Django's ``ValidationError`` if the new ObjectTag fails ``full_clean()``.

    The checks and the insert are not atomic, so a caller that needs them to hold against concurrent
    writers must serialize those writers itself.
    """
    taxonomy = tag.taxonomy
    if taxonomy is None:
        raise ValueError(_("Tag ({value}) does not belong to a taxonomy.").format(value=tag.value))

    object_tag = ObjectTag.objects.filter(object_id=object_id, taxonomy_id=taxonomy.id, tag_id=tag.id).first()
    if object_tag is not None and not object_tag.archived:
        return object_tag

    # An active row would be the one after the active rows the object already has. Archived rows
    # don't count toward either limit, as in tag_object(), and the row about to be revived is
    # archived, so it never counts against itself.
    active_tags = ObjectTag.objects.unarchived().filter(object_id=object_id)
    if not taxonomy.allow_multiple:
        _check_taxonomy_allows_tag_count(taxonomy, active_tags.filter(taxonomy_id=taxonomy.id).count() + 1)
    _check_object_tag_limit(active_tags.count(), 1, object_id)

    if object_tag is not None:
        object_tag.archived = False
        object_tag.save(update_fields=["archived"])
        return object_tag
    object_tag = ObjectTag(taxonomy=taxonomy, object_id=object_id, tag=tag)
    object_tag.full_clean()
    object_tag.save()
    return object_tag


def add_tag_to_taxonomy(
    taxonomy: Taxonomy,
    tag: str,
    parent_tag_value: str | None = None,
    external_id: str | None = None
) -> Tag:
    """
    Adds a new Tag to provided Taxonomy. If a Tag already exists in the
    Taxonomy, an exception is raised, otherwise the newly created
    Tag is returned.

    If `external_id` is omitted, one is generated from the tag's name.
    """
    new_tag = taxonomy.add_tag(tag, parent_tag_value, external_id)

    # Resync all related ObjectTags after creating new Tag to
    # to ensure any existing ObjectTags with the same value will
    # be linked to the new Tag
    object_tags = taxonomy.objecttag_set.all()
    resync_object_tags(object_tags)

    return new_tag


def update_tag_in_taxonomy(taxonomy: Taxonomy, tag: str, new_value: str):
    """
    Update a Tag that belongs to a Taxonomy. The related ObjectTags are
    updated accordingly.

    Currently only supports updating the Tag value.
    """
    updated_tag = taxonomy.update_tag(tag, new_value)

    # Resync all related ObjectTags to update to the new Tag value
    object_tags = taxonomy.objecttag_set.all()
    resync_object_tags(object_tags)

    return updated_tag


def delete_tags_from_taxonomy(
    taxonomy: Taxonomy,
    tags: list[str],
    with_subtags: bool
):
    """
    Delete Tags that belong to a Taxonomy. If any of the Tags have children and
    the `with_subtags` is not set to `True` it will fail, otherwise
    the sub-tags will be deleted as well.
    """
    taxonomy.delete_tags(tags, with_subtags)


def copy_tags(source_object_id: str, dest_object_id: str):
    """
    Copy all tags from one object to another.

    This keeps all not-copied tags and delete all
    previous copied tags of the dest object.
    If there are not-copied tags that also are in 'source_object_id',
    then they become copied.

    Locked copied tags (``deletion_locked=True``) are archived instead of deleted. An archived destination
    tag that the source still has is un-archived.
    """
    source_object_tags = get_object_tags(
        source_object_id,
    )
    copied_tags = ObjectTag.objects.filter(
        object_id=dest_object_id,
        is_copied=True,
    )

    with transaction.atomic():
        # Delete all copied tags of destination
        copied_tags.delete()

        # Copy an create object_tags in destination
        for object_tag in source_object_tags:
            lookup: dict[str, Any] = {
                "object_id": dest_object_id,
                "taxonomy_id": cast(int, object_tag.taxonomy_id),
            }
            if object_tag.tag_id:
                # Match closed tags by id, since the destination's cached _value can be stale.
                lookup["tag_id"] = object_tag.tag_id
            else:
                # Free-text associations all have a null tag_id, so match on the value, as the unique constraint does.
                lookup["_value"] = object_tag.value
            ObjectTag.objects.update_or_create(
                **lookup,
                defaults={"is_copied": True, "archived": False},
                # Note: _value (for closed tags) and _export_id are set automatically
            )


def unmark_copied_tags(object_id: str) -> None:
    """
    Update copied object tags on the given object to mark them as "not copied".
    """
    ObjectTag.objects.filter(object_id=object_id).update(is_copied=False)


def _extract_object_tag_ids(object_tag_ids: int | Iterable[int]) -> set[int]:
    """
    Return ``object_tag_ids`` as a set, raising TypeError for anything that is not an int or an iterable of ints.
    """
    if isinstance(object_tag_ids, (str, bytes)) or not isinstance(object_tag_ids, (int, Iterable)):
        raise TypeError(f"ObjectTag ids must be ints, got {object_tag_ids!r} ({type(object_tag_ids).__name__})")
    ids = {object_tag_ids} if isinstance(object_tag_ids, int) else set(object_tag_ids)
    for object_tag_id in ids:
        if isinstance(object_tag_id, bool) or not isinstance(object_tag_id, int):
            raise TypeError(f"ObjectTag ids must be ints, got {object_tag_id!r} ({type(object_tag_id).__name__})")
    return ids


def lock_object_tags_for_deletion(object_tag_ids: int | Iterable[int]) -> None:
    """
    Lock the given ObjectTags against deletion.

    A locked association is archived rather than deleted, because something outside the tagging app depends on it.
    Locks are permanent; there is no unlock.

    :param object_tag_ids: One ObjectTag primary key, or an iterable of them.
    :raises TypeError: If any id is not an int. Strings, bytes, and bools are not coerced.
    :raises ObjectTagDoesNotExist: If any id does not resolve to an ObjectTag, or is deleted while this function
        runs.
    """
    ids = _extract_object_tag_ids(object_tag_ids)
    if not ids:
        return
    locked_by_id = dict(ObjectTag.objects.filter(pk__in=ids).values_list("pk", "deletion_locked"))
    missing = ids - locked_by_id.keys()
    if missing:
        raise ObjectTag.DoesNotExist(f"ObjectTag ids not found: {sorted(missing)}")
    unlocked = [object_tag_id for object_tag_id, locked in locked_by_id.items() if not locked]
    if unlocked:
        with transaction.atomic():
            updated = ObjectTag.objects.filter(pk__in=unlocked, deletion_locked=False).update(deletion_locked=True)
            if updated != len(unlocked):
                # A shortfall means a row was deleted, or another caller locked it first. Only the former is an error.
                surviving = set(ObjectTag.objects.filter(pk__in=unlocked).values_list("pk", flat=True))
                deleted = set(unlocked) - surviving
                if deleted:
                    raise ObjectTag.DoesNotExist(
                        f"ObjectTag ids deleted before they could be locked: {sorted(deleted)}"
                    )


def get_tags_locked_for_deletion(tag_ids: Iterable[int]) -> set[int]:
    """
    Return the subset of ``tag_ids`` that have at least one deletion-locked association.

    Archived associations count: a locked association keeps protecting its tag after it is archived.

    :param tag_ids: The tag ids to check. Unknown ids are omitted from the result.
    """
    ids = set(tag_ids)
    locked = ObjectTag.objects.filter(tag_id__in=ids, deletion_locked=True)
    return set(locked.values_list("tag_id", flat=True).distinct())


def is_taxonomy_locked_for_deletion(taxonomy_id: int | None) -> bool:
    """
    Return whether any association in the taxonomy is deletion-locked.

    Archived associations count: a locked association keeps protecting its taxonomy after it is archived.

    :param taxonomy_id: The taxonomy id to check. ``None`` returns ``False``, so that associations left behind by a
        deleted taxonomy, whose ``taxonomy_id`` is NULL, are not mistaken for a locked one.
    """
    if taxonomy_id is None:
        return False
    return ObjectTag.objects.filter(taxonomy_id=taxonomy_id, deletion_locked=True).exists()


def add_usage_counts(taxonomy: Taxonomy, tag_data: TagDataQuerySet) -> TagDataQuerySet:
    """
    Add usage counts to the query result.

    Not a simple raw count of each tags usage. A tag can be directly
    applied to an object, which can be a course, library, module,
    or something else.

    A tag can also be indirectly applied when some of its children
    are applied to an object, it is considered automatically applied.
    So, if the tags "Chemistry" and "Physics" are applied once
    each to different objects, their parent tag "Natural Science" is
    considered indirectly applied to 2 objects.

    Deduplication: A tag can only be applied to a single object once.
    So if two child tags are applied to the same object, e.g.
    "Chemistry" and "Physics" are applied to the same course, the
    parent tag, "Natural Science" is only applied to it once,
    because no tag can be applied to the same object twice.

    Archived object tags, and those whose tag or taxonomy is archived, are not counted.

    For performance reasons, we call this function with the list result of the
    QuerySet so we can then add the counts in-memory rather than annotate to a
    QuerySet which would require a very expensive annotation to join the
    in-memory data to the original QuerySet.
    """

    object_tags = taxonomy.objecttag_set.unarchived().values_list("object_id", "tag__lineage")
    tag_counts: Counter[str] = Counter()
    object_tag_lineage_seen: defaultdict[str, set] = defaultdict(set)

    for object_id, tag_lineage in object_tags:
        # split the lineages to get a dict of {tag.value: [lineages]}
        lineage_tags = list(tag_lineage.split('\t')) if tag_lineage else []
        # de-duplicate based on if the lineage is already 'seen' per object
        unseen_tags = [t for t in lineage_tags if t not in object_tag_lineage_seen[object_id]]

        tag_counts.update(unseen_tags)
        object_tag_lineage_seen[object_id].update(unseen_tags)

    # In-memory 'annotation'; this is faster than using annotate() on the QuerySet.
    for row in tag_data:
        row["usage_count"] = tag_counts.get(row["value"], 0)

    return tag_data
