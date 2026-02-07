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

from typing import Any

from django.db import models, transaction
from django.db.models import F, QuerySet, Value
from django.db.models.functions import Coalesce, Concat, Lower
from django.utils.text import slugify
from django.utils.translation import gettext as _

from .data import TagDataQuerySet
from .models import ObjectTag, Tag, Taxonomy
from .models.utils import ConcatNull, StringAgg

# Export this as part of the API
TagDoesNotExist = Tag.DoesNotExist


def create_taxonomy(  # pylint: disable=too-many-positional-arguments
    name: str,
    description: str | None = None,
    enabled=True,
    allow_multiple=True,
    allow_free_text=False,
    taxonomy_class: type[Taxonomy] | None = None,
    export_id: str | None = None,
) -> Taxonomy:
    """
    Creates, saves, and returns a new Taxonomy with the given attributes.
    """
    if not export_id:
        export_id = f"{Taxonomy.objects.count() + 1}-{slugify(name, allow_unicode=True)}"

    taxonomy = Taxonomy(
        name=name,
        description=description or "",
        enabled=enabled,
        allow_multiple=allow_multiple,
        allow_free_text=allow_free_text,
        export_id=export_id,
    )
    if taxonomy_class:
        taxonomy.taxonomy_class = taxonomy_class

    taxonomy.full_clean()
    taxonomy.save()
    return taxonomy.cast()


def get_taxonomy(taxonomy_id: int) -> Taxonomy | None:
    """
    Returns a Taxonomy cast to the appropriate subclass which has the given ID.
    """
    taxonomy = Taxonomy.objects.filter(pk=taxonomy_id).first()
    return taxonomy.cast() if taxonomy else None


def get_taxonomy_by_export_id(taxonomy_export_id: str) -> Taxonomy | None:
    """
    Returns a Taxonomy cast to the appropriate subclass which has the given export ID.
    """
    taxonomy = Taxonomy.objects.filter(export_id=taxonomy_export_id).first()
    return taxonomy.cast() if taxonomy else None


def get_taxonomies(enabled=True) -> QuerySet[Taxonomy]:
    """
    Returns a queryset containing the enabled taxonomies, sorted by name.

    We return a QuerySet here for ease of use with Django Rest Framework and other query-based use cases.
    So be sure to use `Taxonomy.cast()` to cast these instances to the appropriate subclass before use.

    If you want the disabled taxonomies, pass enabled=False.
    If you want all taxonomies (both enabled and disabled), pass enabled=None.
    """
    queryset = Taxonomy.objects.order_by("name", "id")
    if enabled is None:
        return queryset.all()
    return queryset.filter(enabled=enabled)


def get_tags(taxonomy: Taxonomy) -> TagDataQuerySet:
    """
    Returns a QuerySet of all the tags in the given taxonomy.

    Note that if the taxonomy is dynamic or free-text, only tags that have
    already been applied to some object will be returned.
    """
    return taxonomy.cast().get_filtered_tags()


def get_root_tags(taxonomy: Taxonomy) -> TagDataQuerySet:
    """
    Returns a list of the root tags for the given taxonomy.

    Note that if the taxonomy allows free-text tags, then the returned list will be empty.
    """
    return taxonomy.cast().get_filtered_tags(depth=1)


def search_tags(
    taxonomy: Taxonomy,
    search_term: str,
    exclude_object_id: str | None = None,
    include_counts: bool = False,
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
            taxonomy.objecttag_set.filter(object_id=exclude_object_id).values_list(
                "_value", flat=True
            )
        )
    qs = taxonomy.cast().get_filtered_tags(
        search_term=search_term,
        excluded_values=excluded_values,
        include_counts=include_counts,
    )
    return qs


def get_children_tags(
    taxonomy: Taxonomy,
    parent_tag_value: str,
) -> TagDataQuerySet:
    """
    Returns a QuerySet of children tags for the given parent tag.

    Note that if the taxonomy allows free-text tags, then the returned list will be empty.
    """
    return taxonomy.cast().get_filtered_tags(parent_tag_value=parent_tag_value, depth=1)


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
    object_tag_class: type[ObjectTag] = ObjectTag
) -> QuerySet[ObjectTag]:
    """
    Returns a Queryset of object tags for a given object.

    Pass taxonomy_id to limit the returned object_tags to a specific taxonomy.
    """
    filters = {"taxonomy_id": taxonomy_id} if taxonomy_id else {}
    base_qs = (
        object_tag_class.objects
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
        .select_related("taxonomy", "tag", "tag__parent", "tag__parent__parent")
        # Sort the tags within each taxonomy in "tree order". See Taxonomy._get_filtered_tags_deep for details on this:
        .annotate(sort_key=Lower(Concat(
            ConcatNull(F("tag__parent__parent__parent__value"), Value("\t")),
            ConcatNull(F("tag__parent__parent__value"), Value("\t")),
            ConcatNull(F("tag__parent__value"), Value("\t")),
            Coalesce(F("tag__value"), F("_value")),
            Value("\t"),
            output_field=models.CharField(),
        )))
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

    Deleted tags and disabled taxonomies are excluded from the counts, even if
    ObjectTag data about them is present.
    """
    # Note: in the future we may add an option to exclude system taxonomies from the count.
    qs: Any = ObjectTag.objects
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
        # Counting the implicit tags is tricky, because if two "grandchild" tags have the same implicit parent tag, we
        # need to count that parent tag only once. To do that, we collect all the ancestor tag IDs into an aggregate
        # string, and then count the unique values using python
        qs = qs.values("object_id").annotate(
            num_tags=models.Count("id"),
            tag_ids_str_1=StringAgg("tag_id"),
            tag_ids_str_2=StringAgg("tag__parent_id"),
            tag_ids_str_3=StringAgg("tag__parent__parent_id"),
            tag_ids_str_4=StringAgg("tag__parent__parent__parent_id"),
        ).order_by("object_id")
        result = {}
        for row in qs:
            # ObjectTags for free text taxonomies will be included in "num_tags" count, but not "tag_ids_str_1" since
            # they have no tag ID. We can compute how many free text tags each object has now:
            if row["tag_ids_str_1"]:
                num_free_text_tags = row["num_tags"] - len(row["tag_ids_str_1"].split(","))
            else:
                num_free_text_tags = row["num_tags"]
            # Then we count the total number of *unique* Tags for this object, both implicit and explicit:
            other_tag_ids = set()
            for field in ("tag_ids_str_1", "tag_ids_str_2", "tag_ids_str_3", "tag_ids_str_4"):
                if row[field] is not None:
                    for tag_id in row[field].split(","):
                        other_tag_ids.add(int(tag_id))
            result[row["object_id"]] = num_free_text_tags + len(other_tag_ids)
        return result
    else:
        qs = qs.values("object_id").annotate(num_tags=models.Count("id")).order_by("object_id")
        return {row["object_id"]: row["num_tags"] for row in qs}


def delete_object_tags(object_id: str):
    """
    Delete all ObjectTag entries for a given object.
    """
    tags = ObjectTag.objects.filter(object_id=object_id)

    tags.delete()


def _check_new_tag_count(
    new_tag_count: int,
    taxonomy: Taxonomy | None,
    object_id: str,
    taxonomy_export_id: str | None = None,
) -> None:
    """
    Checks if the new count of tags for the object is equal or less than 100
    """
    # Exclude to avoid counting the tags that are going to be updated
    if taxonomy:
        current_count = ObjectTag.objects.filter(object_id=object_id).exclude(taxonomy_id=taxonomy.id).count()
    else:
        current_count = ObjectTag.objects.filter(object_id=object_id).exclude(_export_id=taxonomy_export_id).count()

    if current_count + new_tag_count > 100:
        raise ValueError(
            _("Cannot add more than 100 tags to ({object_id}).").format(object_id=object_id)
        )


def _get_current_tags(
    taxonomy: Taxonomy | None,
    tags: list[str],
    object_id: str,
    object_tag_class: type[ObjectTag] = ObjectTag,
    taxonomy_export_id: str | None = None,
) -> list[ObjectTag]:
    """
    Returns the current object tags of the related object_id with taxonomy
    """
    ObjectTagClass = object_tag_class
    if taxonomy:
        if not taxonomy.allow_multiple and len(tags) > 1:
            raise ValueError(_("Taxonomy ({name}) only allows one tag per object.").format(name=taxonomy.name))
        current_tags = list(
           ObjectTagClass.objects.filter(taxonomy=taxonomy, object_id=object_id)
        )
    else:
        current_tags = list(
            ObjectTagClass.objects.filter(_export_id=taxonomy_export_id, object_id=object_id)
        )
    return current_tags


def tag_object(  # pylint: disable=too-many-positional-arguments
    object_id: str,
    taxonomy: Taxonomy | None,
    tags: list[str],
    object_tag_class: type[ObjectTag] = ObjectTag,
    create_invalid: bool = False,
    taxonomy_export_id: str | None = None,
) -> None:
    """
    Replaces the existing ObjectTag entries for the given taxonomy + object_id
    with the given list of tags.

    tags: A list of the values of the tags from this taxonomy to apply.

    object_tag_class: Optional. Use a proxy subclass of ObjectTag for additional
        validation. (e.g. only allow tagging certain types of objects.)

    Raised Tag.DoesNotExist if the proposed tags are invalid for this taxonomy.
    Preserves existing (valid) tags, adds new (valid) tags, and removes omitted
    (or invalid) tags.
    create_invalid: You can create invalid tags and avoid the previous behavior using.

    taxonomy_export_id: You can create object tags without taxonomy using this param
    and `taxonomy` as None. You need to use the taxonomy.export_id, so you can resync
    this object tag if the taxonomy is created in the future.
    """
    if not isinstance(tags, list):
        raise ValueError(_("Tags must be a list, not {type}.").format(type=type(tags).__name__))

    ObjectTagClass = object_tag_class
    tags = list(dict.fromkeys(tags))  # Remove duplicates preserving order

    if taxonomy:
        taxonomy = taxonomy.cast()  # Make sure we're using the right subclass. This is a no-op if we are already.
    elif not taxonomy_export_id:
        raise ValueError("`taxonomy_export_id` can't be None if `taxonomy` is None")

    _check_new_tag_count(len(tags), taxonomy, object_id, taxonomy_export_id)
    current_tags = _get_current_tags(
        taxonomy,
        tags,
        object_id,
        object_tag_class,
        taxonomy_export_id
    )

    updated_tags = []
    if taxonomy and taxonomy.allow_free_text:
        for tag_value in tags:
            object_tag_index = next((i for (i, t) in enumerate(current_tags) if t.value == tag_value), -1)
            if object_tag_index >= 0:
                # This tag is already applied.
                object_tag = current_tags.pop(object_tag_index)
            else:
                object_tag = ObjectTagClass(taxonomy=taxonomy, object_id=object_id, _value=tag_value)
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
                object_tag_index = next((i for (i, t) in enumerate(current_tags) if t.tag_id == tag.id), -1)
                if object_tag_index >= 0:
                    # This tag is already applied.
                    object_tag = current_tags.pop(object_tag_index)
                    if object_tag._value != tag.value:  # pylint: disable=protected-access
                        # The ObjectTag's cached '_value' is out of sync with the Tag, so update it:
                        object_tag._value = tag.value  # pylint: disable=protected-access
                        updated_tags.append(object_tag)
                else:
                    # We are newly applying this tag:
                    object_tag = ObjectTagClass(taxonomy=taxonomy, object_id=object_id, tag=tag)
                    updated_tags.append(object_tag)
            elif taxonomy:
                # Tag doesn't exist in the taxonomy and `create_invalid` is True
                object_tag = ObjectTagClass(taxonomy=taxonomy, object_id=object_id, _value=tag_value)
                updated_tags.append(object_tag)
            else:
                # Taxonomy is None (also tag doesn't exist)
                if taxonomy_export_id:
                    # This will always be true, since it is verified at the beginning of the function.
                    # This condition is placed by the type checks.
                    object_tag = ObjectTagClass(
                        taxonomy=None,
                        object_id=object_id,
                        _value=tag_value,
                        _export_id=taxonomy_export_id
                    )
                    updated_tags.append(object_tag)

    # Save all updated tags at once to avoid partial updates
    with transaction.atomic():
        # delete any omitted existing tags. We do this first to reduce chances of UNIQUE constraint edge cases
        for old_tag in current_tags:
            old_tag.delete()
        # add the new tags:
        for object_tag in updated_tags:
            object_tag.full_clean()  # Run validation
            object_tag.save()


def add_tag_to_taxonomy(
    taxonomy: Taxonomy,
    tag: str,
    parent_tag_value: str | None = None,
    external_id: str | None = None
) -> Tag:
    """
    Adds a new Tag to provided Taxonomy. If a Tag already exists in the
    Taxonomy, an exception is raised, otherwise the newly created
    Tag is returned
    """
    taxonomy = taxonomy.cast()
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
    taxonomy = taxonomy.cast()
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
    taxonomy = taxonomy.cast()
    taxonomy.delete_tags(tags, with_subtags)


def copy_tags(source_object_id: str, dest_object_id: str):
    """
    Copy all tags from one object to another.

    This keeps all not-copied tags and delete all
    previous copied tags of the dest object.
    If there are not-copied tags that also are in 'source_object_id',
    then they become copied.
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
            ObjectTag.objects.update_or_create(
                object_id=dest_object_id,
                taxonomy_id=object_tag.taxonomy_id,
                tag_id=object_tag.tag_id,
                defaults={"is_copied": True},
                # Note: _value and _export_id are set automatically
            )


def unmark_copied_tags(object_id: str) -> None:
    """
    Update copied object tags on the given object to mark them as "not copied".
    """
    ObjectTag.objects.filter(object_id=object_id).update(is_copied=False)
