"""
Public API for Competency-Based Education (CBE).
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey

from openedx_catalog.api import get_course_run
from openedx_catalog.models import CourseRun
from openedx_tagging.api import create_taxonomy, get_object_tags, tag_object
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

from .models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    LogicOperator,
)

__all__ = [
    "associate_competency_criterion",
    "create_competency_criterion",
    "create_competency_taxonomy",
    "get_competency_rule_profiles",
    "is_competency_taxonomy",
    "resolve_competency_tag",
    "create_leaf_group",
    "resolve_supplied_leaf_group",
    "select_competency_taxonomies",
]


def create_competency_taxonomy(  # pylint: disable=too-many-positional-arguments
    name: str,
    description: str | None = None,
    enabled=True,
    allow_multiple=True,
    allow_free_text=False,
    read_only=False,
    export_id: str | None = None,
) -> CompetencyTaxonomy:
    """
    Create, save, and return a new CompetencyTaxonomy with the given attributes.
    """
    taxonomy = create_taxonomy(
        name=name,
        description=description,
        enabled=enabled,
        allow_multiple=allow_multiple,
        allow_free_text=allow_free_text,
        read_only=read_only,
        export_id=export_id,
        taxonomy_cls=CompetencyTaxonomy,
    )
    assert isinstance(taxonomy, CompetencyTaxonomy)
    return taxonomy


def get_competency_rule_profiles() -> QuerySet[CompetencyRuleProfile]:
    """
    Return every live CompetencyRuleProfile, in ascending ``id`` order.

    UNSTABLE: the rule profile family is incomplete, so the create, update, and archive entry
    points still to come may change this function's shape without a deprecation cycle.

    Archived profiles are left out: retirement is archive-only.

    The ordering is part of the contract rather than a cosmetic detail: an unordered queryset
    gives a paginating caller overlapping and skipped pages.
    """
    return CompetencyRuleProfile.objects.filter(archived=False).order_by("id")


def is_competency_taxonomy(taxonomy: Taxonomy) -> bool:
    """
    Return True if ``taxonomy`` is competency-enabled, i.e. has a CompetencyTaxonomy row.

    Costs one query per call unless ``taxonomy`` came from a queryset passed through
    :func:`select_competency_taxonomies`. Returns False for an unsaved ``taxonomy``.
    """
    # "competencytaxonomy" is the accessor Django generates for the multi-table-inheritance
    # link from Taxonomy to CompetencyTaxonomy.
    return hasattr(taxonomy, "competencytaxonomy")


def select_competency_taxonomies(taxonomies: QuerySet[Taxonomy]) -> QuerySet[Taxonomy]:
    """
    Return ``taxonomies`` with each CompetencyTaxonomy row joined in.

    Pair this with :func:`is_competency_taxonomy` when checking more than one taxonomy,
    so the check costs no additional query per row.
    """
    return taxonomies.select_related("competencytaxonomy")


def resolve_competency_tag(tag_id: int) -> Tag:
    """
    Return the competency Tag identified by ``tag_id``.

    Raises Http404 if no such Tag exists, or if it isn't on a CompetencyTaxonomy. Shared by
    the view and :func:`associate_competency_criterion` so both agree on what counts as valid.
    """
    tag = get_object_or_404(Tag, pk=tag_id)
    # Explicit None check narrows the type for mypy and gives a precise 404 reason.
    if tag.taxonomy is None or not is_competency_taxonomy(tag.taxonomy):
        raise Http404("Tag is not on a CompetencyTaxonomy.")
    return tag


def create_leaf_group(
    tag: Tag,
    course_run: CourseRun,
    logic_operator: str | None = None,
) -> CompetencyCriteriaGroup:
    """
    Return a fresh leaf CompetencyCriteriaGroup under ``tag``'s root/course-level groups.

    Gets or creates the root and course-level groups (race-safe by locking ``tag``'s row), then
    always creates a new leaf: a tag/course pair can hold more than one leaf, one per criterion.
    """
    with transaction.atomic():
        # MySQL has no partial unique index to stop two concurrent requests from each creating a
        # root for the same tag, so they are serialized on the tag row instead.
        Tag.objects.select_for_update().get(pk=tag.pk)
        root, _ = CompetencyCriteriaGroup.objects.get_or_create(
            tag=tag,
            parent=None,
            defaults={"name": f"{tag.value} (root)"},
        )
        course_level_group, _ = CompetencyCriteriaGroup.objects.get_or_create(
            tag=tag,
            course=course_run,
            parent=root,
            defaults={"name": f"{tag.value} — {course_run.title}"},
        )
        return CompetencyCriteriaGroup.objects.create(
            tag=tag,
            course=None,
            parent=course_level_group,
            logic_operator=logic_operator or LogicOperator.OR,
        )


def resolve_supplied_leaf_group(group_id: int, tag: Tag, course_run: CourseRun) -> CompetencyCriteriaGroup:
    """
    Return the CompetencyCriteriaGroup identified by ``group_id``, validated as a usable leaf.

    Raises Http404 if it doesn't exist, or ValidationError (keyed "group_id") if it isn't a
    leaf, doesn't belong to ``tag``, or its course doesn't match ``course_run``.
    """
    group = get_object_or_404(CompetencyCriteriaGroup, pk=group_id)
    if group.parent_id is None or group.course_id is not None:
        raise ValidationError({"group_id": _("group_id must reference a leaf CompetencyCriteriaGroup.")})
    if group.tag_id != tag.id:
        raise ValidationError({"group_id": _("group_id must belong to the given competency tag.")})
    if group.parent.course_id != course_run.id:
        raise ValidationError({"group_id": _("group_id must belong to the given course.")})
    return group


def create_competency_criterion(
    group: CompetencyCriteriaGroup,
    object_id: str,
    rule_profile_id: int | None = None,
    rule_type_override: str | None = None,
    rule_payload_override: dict | None = None,
) -> CompetencyCriterion:
    """
    Create and return a CompetencyCriterion under ``group``, tagging ``object_id`` along the way.

    ``tag_object()`` replaces an object's full tag list for a taxonomy rather than appending, so
    this reads the object's existing tags first and unions in the new one, rather than silently
    dropping a sibling competency's tag under the same taxonomy.

    When no rule fields are supplied, resolves to the seeded system-default CompetencyRuleProfile
    rather than persisting null: ADR-0002 Decision 4 always resolves to a concrete profile, and
    the model's own xor-override constraint forbids leaving all three fields null.
    """
    tag = group.tag
    # tag.taxonomy_id is nullable at the model level; already guaranteed set by the caller.
    assert tag.taxonomy_id is not None
    existing_values = [existing_tag.value for existing_tag in get_object_tags(object_id, taxonomy_id=tag.taxonomy_id)]
    if tag.value not in existing_values:
        existing_values.append(tag.value)
    tag_object(object_id, tag.taxonomy, existing_values)
    object_tag = ObjectTag.objects.get(object_id=object_id, taxonomy_id=tag.taxonomy_id, tag_id=tag.id)

    if rule_profile_id is None and rule_type_override is None and rule_payload_override is None:
        rule_profile_id = CompetencyRuleProfile.objects.get(
            organization__isnull=True,
            course__isnull=True,
            competency_taxonomy__isnull=True,
            archived=False,
        ).id

    # #666 inserts its parent-competency dominance/containment check here, before creation.

    return CompetencyCriterion.objects.create(
        group=group,
        object_tag=object_tag,
        rule_profile_id=rule_profile_id,
        rule_type_override=rule_type_override,
        rule_payload_override=rule_payload_override,
    )


def associate_competency_criterion(
    tag_id: int,
    object_id: str,
    *,
    group_id: int | None = None,
    logic_operator: str | None = None,
    competency_rule_profile_id: int | None = None,
    rule_type_override: str | None = None,
    rule_payload_override: dict | None = None,
) -> CompetencyCriterion:
    """
    Associate ``object_id`` with the competency ``tag_id`` names, creating a criterion for it.

    The public entry point for #665's create-criterion endpoint; the caller is expected to have
    already checked ``oel_tagging.can_tag_object``. Rejects re-targeting the same group a
    (tag_id, object_id) pair already uses, and rejects a duplicate via the derive-or-create
    path; a *different* explicit group is a deliberate second association per ADR-0002, not a
    duplicate. Branches on ``group_id`` to resolve or derive/create a leaf, then creates the
    criterion, all inside one transaction so a downstream failure rolls back any group just
    created.

    Raises ValidationError (never DRF's) on rejected input, Http404 if tag_id/group_id don't
    resolve.
    """
    tag = resolve_competency_tag(tag_id)
    # tag.taxonomy is already confirmed set; re-asserted since that doesn't cross function boundaries for mypy.
    assert tag.taxonomy_id is not None

    try:
        usage_key = UsageKey.from_string(object_id)
    except InvalidKeyError as exc:
        raise ValidationError({"object_id": _("object_id is not a valid usage key.")}) from exc

    try:
        course_run = get_course_run(usage_key.course_key)
    except CourseRun.DoesNotExist as exc:
        raise ValidationError({"object_id": _("No course run matches object_id's course.")}) from exc

    existing_object_tag = ObjectTag.objects.filter(
        object_id=object_id, taxonomy_id=tag.taxonomy_id, tag_id=tag.id,
    ).first()
    if existing_object_tag is not None:
        duplicate_criteria = CompetencyCriterion.objects.filter(object_tag=existing_object_tag)
        if group_id is None:
            # Can't know which group slot is intended, so any existing criterion is a duplicate.
            if duplicate_criteria.exists():
                raise ValidationError(
                    {"object_id": _("A CompetencyCriterion already associates this tag with this object_id.")}
                )
        elif duplicate_criteria.filter(group_id=group_id).exists():
            # A different explicit group is a deliberate second association (ADR-0002), not a duplicate.
            raise ValidationError(
                {"group_id": _("A CompetencyCriterion already associates this tag with this object_id in this group.")}
            )

    if group_id is not None and logic_operator is not None:
        raise ValidationError({"logic_operator": _("group_id and logic_operator cannot both be supplied.")})

    with transaction.atomic():
        if group_id is not None:
            group = resolve_supplied_leaf_group(group_id, tag, course_run)
        else:
            group = create_leaf_group(tag, course_run, logic_operator)
        return create_competency_criterion(
            group=group,
            object_id=object_id,
            rule_profile_id=competency_rule_profile_id,
            rule_type_override=rule_type_override,
            rule_payload_override=rule_payload_override,
        )
