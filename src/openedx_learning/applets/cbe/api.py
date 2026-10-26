"""
Public API for Competency-Based Education (CBE).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Literal, Sequence, cast

from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import F, Q, QuerySet
from django.db.transaction import TransactionManagementError
from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey

from openedx_catalog.api import get_course_run
from openedx_catalog.models_api import CourseRun
from openedx_tagging.api import create_taxonomy, get_ancestor_tags, get_descendant_tags, get_object_tags, tag_object
from openedx_tagging.models import ObjectTag, Tag, Taxonomy
from openedx_tagging.rules import ObjectTagPermissionItem, UserType

from .data import CriterionDeletionResult, GradedObjectScore
from .models import (
    CompetencyCourseRun,
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    LogicOperator,
    MasteryStatus,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriterionStatus,
)
from .rule_payloads import evaluate_rule

__all__ = [
    "CompetencyCriteriaGroupArchivedError",
    "CompetencyCriteriaTree",
    "CompetencyCriterionArchivedError",
    "CriterionDeletionResult",
    "GradedObjectScore",
    "associate_competency_criterion",
    "bulk_update_competency_criteria",
    "create_competency_criterion",
    "create_competency_taxonomy",
    "delete_or_archive_competency_criterion",
    "get_competency_criteria_tree",
    "get_competency_rule_profiles",
    "get_courses_with_relative_criteria",
    "is_competency_taxonomy",
    "record_graded_object_statuses",
    "resolve_competency_tag",
    "resolve_supplied_leaf_group",
    "select_competency_taxonomies",
    "update_competency_criteria_group",
]

log = logging.getLogger(__name__)


class CompetencyCriterionArchivedError(Exception):
    """
    Raised when a request would edit an archived CompetencyCriterion, or one under an archived group.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this exception, or when it is raised, without a deprecation
    cycle.
    """


class CompetencyCriteriaGroupArchivedError(Exception):
    """
    Raised when a request would edit a CompetencyCriteriaGroup that has been archived.

    UNSTABLE: the criteria-authoring family is incomplete, so its archive behavior may change this
    exception, or when it is raised, without a deprecation cycle.
    """


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

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    Raises Tag.DoesNotExist if no such Tag exists, or if it isn't on a CompetencyTaxonomy.
    Shared by the view and :func:`associate_competency_criterion` so both agree on what counts
    as valid.
    """
    tag = Tag.objects.get(pk=tag_id)
    # Explicit None check narrows the type for mypy.
    if tag.taxonomy is None or not is_competency_taxonomy(tag.taxonomy):
        raise Tag.DoesNotExist("Tag is not on a CompetencyTaxonomy.")
    return tag


@dataclass(frozen=True, kw_only=True, slots=True)
class CompetencyCriteriaTree:
    """
    One competency's non-archived CompetencyCriteriaGroup tree and CompetencyCriterion leaves.

    UNSTABLE: the criteria-authoring family is incomplete, so this shape may change without a
    deprecation cycle. ``groups`` and ``criteria`` are flat lists: each group carries its
    ``parent_id`` and each criterion its ``group_id``, and both always name a group in ``groups``.
    """

    groups: list[CompetencyCriteriaGroup]
    criteria: list[CompetencyCriterion]


def get_competency_criteria_tree(tag: Tag) -> CompetencyCriteriaTree:
    """
    Return the non-archived CompetencyCriteriaGroup tree and CompetencyCriterion leaves for ``tag``.

    A group is returned only if it and every ancestor up to its root are non-archived, and a
    criterion only if it and its group are returned, so the result always forms one tree: no
    returned ``parent_id`` or ``group_id`` names a row that was left out. Groups come back
    root first, then ordered by ``(parent_id, ordering, id)``; criteria by ``(group_id, id)``.

    UNSTABLE: the criteria-authoring family is incomplete, so its group update and archive
    behavior may change this function's shape without a deprecation cycle.
    """
    groups = list(
        CompetencyCriteriaGroup.objects.filter(tag=tag, archived=False)
        .select_related("course")
        .order_by(F("parent_id").asc(nulls_first=True), "ordering", "id")
    )

    # Walk down from the roots instead of relying on row order. A group's parent is fixed once
    # saved, but a QuerySet.update() can still give a group a parent with a larger id, and
    # ordering by parent_id would then visit that child before its parent.
    children_by_parent_id: dict[int | None, list[int]] = {}
    for group in groups:
        children_by_parent_id.setdefault(group.parent_id, []).append(group.id)
    kept_group_ids: set[int] = set()
    pending = list(children_by_parent_id.get(None, []))
    while pending:
        group_id = pending.pop()
        kept_group_ids.add(group_id)
        pending.extend(children_by_parent_id.get(group_id, []))

    criteria = list(
        CompetencyCriterion.objects.filter(group_id__in=kept_group_ids, archived=False)
        .select_related("object_tag")
        .order_by("group_id", "id")
    )

    return CompetencyCriteriaTree(
        groups=[group for group in groups if group.id in kept_group_ids],
        criteria=criteria,
    )


def _create_leaf_group(
    tag: Tag,
    course_run: CourseRun,
    logic_operator: str | None = None,
) -> CompetencyCriteriaGroup:
    """
    Return a fresh leaf CompetencyCriteriaGroup under ``tag``'s root/course-level groups.

    Gets or creates the root and course-level groups, then always creates a new leaf: a
    tag/course pair can hold more than one leaf, one per criterion. The root and course-level
    group names are truncated to fit the name column; they are never shown in the UI.

    The root and course-level lookups need no lock of their own: the unique constraints hold one
    root per tag and one course-level group per tag and course, and ``get_or_create()`` recovers
    from a concurrent insert. The caller's tag lock is for ``_reject_duplicate_criterion()``.
    """
    name_max_length = CompetencyCriteriaGroup._meta.get_field("name").max_length
    # Both get_or_create() lookups match archived rows deliberately, so an archived container is
    # never replaced; rejecting or reactivating it is the archived-group checks' decision.
    root, _ = CompetencyCriteriaGroup.objects.get_or_create(
        tag=tag,
        parent=None,
        defaults={"name": f"{tag.value} (root)"[:name_max_length]},
    )
    course_level_group, _ = CompetencyCriteriaGroup.objects.get_or_create(
        tag=tag,
        course=course_run,
        parent=root,
        defaults={"name": f"{tag.value} — {course_run.title}"[:name_max_length]},
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

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    Raises CompetencyCriteriaGroup.DoesNotExist if it doesn't exist, or ValidationError (keyed
    "group_id") if it isn't a leaf, doesn't belong to ``tag``, or its course doesn't match
    ``course_run``.
    """
    group = CompetencyCriteriaGroup.objects.get(pk=group_id)
    if group.parent_id is None or group.course_id is not None:
        raise ValidationError({"group_id": _("group_id must reference a leaf CompetencyCriteriaGroup.")})
    if group.tag_id != tag.id:
        raise ValidationError({"group_id": _("group_id must belong to the given competency tag.")})
    if group.parent.course_id != course_run.id:
        raise ValidationError({"group_id": _("group_id must belong to the given course.")})
    return group


def get_courses_with_relative_criteria(
    tag_ids: Iterable[int], course: CourseRun | None = None,
) -> dict[CourseRun, Tag]:
    """
    Return every course already blocked for one of ``tag_ids`` by an existing CompetencyCriterion.

    Maps each conflicting CourseRun to the lowest-id tag (of ``tag_ids``) with a criterion there,
    narrowed to ``course`` when given. Archived criteria and criteria under an archived group are ignored.
    """
    groups: QuerySet[CompetencyCriteriaGroup, CompetencyCriteriaGroup] = CompetencyCriteriaGroup.objects.filter(
        tag_id__in=tag_ids,
        archived=False,
        parent__archived=False,
        parent__parent__archived=False,
        # Only a leaf's parent carries a course, so this skips criteria misplaced on other groups.
        parent__course__isnull=False,
        criteria__archived=False,
    )
    if course is not None:
        groups = groups.filter(parent__course=course)
    result: dict[CourseRun, Tag] = {}
    for group in groups.select_related("tag", "parent__course").distinct().order_by("tag_id"):
        assert group.parent is not None, f"CompetencyCriteriaGroup {group.id} has no parent"
        assert group.parent.course is not None, f"CompetencyCriteriaGroup {group.id}'s parent has no course"
        result.setdefault(group.parent.course, group.tag)
    return result


def _validate_containment(group: CompetencyCriteriaGroup, course: CourseRun) -> None:
    """
    Raise ValidationError (keyed "tag_id") if ``group.tag`` conflicts with an existing criterion.

    A conflict is a taxonomy ancestor or descendant of ``group.tag`` that already has a
    CompetencyCriterion in ``course``.
    """
    tag = group.tag
    relative_ids = (
        set(get_ancestor_tags(tag).values_list("id", flat=True))
        | set(get_descendant_tags(tag).values_list("id", flat=True))
    )
    if not relative_ids:
        return
    conflicting_tag = get_courses_with_relative_criteria(relative_ids, course=course).get(course)
    if conflicting_tag is not None:
        raise ValidationError({
            "tag_id": _(
                "Tag '{tag_value}' already has a criterion in course {course_key}, and is a taxonomy "
                "ancestor or descendant of this criterion's tag."
            ).format(tag_value=conflicting_tag.value, course_key=course.course_key),
        })


def _get_applicable_rule_profile(taxonomy_id: int) -> CompetencyRuleProfile | None:
    """
    Return the live profile ADR-0002 Decision 4 assigns a criterion in ``taxonomy_id``, or None if there is none.

    The profile scoped to the taxonomy wins, else the system default. Course- and
    organization-scoped profiles are not resolved here, since a criterion is authored at taxonomy
    scope.
    """
    live = CompetencyRuleProfile.objects.filter(archived=False, organization__isnull=True, course__isnull=True)
    # CompetencyTaxonomy shares its pk with its parent Taxonomy row. Of the at most two rows that
    # match, nulls_last sorts the taxonomy's own profile ahead of the system default.
    return (
        live.filter(Q(competency_taxonomy_id=taxonomy_id) | Q(competency_taxonomy__isnull=True))
        .order_by(F("competency_taxonomy_id").desc(nulls_last=True))
        .first()
    )


def create_competency_criterion(
    group: CompetencyCriteriaGroup,
    object_id: str,
    course_run: CourseRun,
    *,
    rule_profile_id: int | None = None,
    rule_type_override: str | None = None,
    rule_payload_override: dict | None = None,
) -> CompetencyCriterion:
    """
    Create and return a CompetencyCriterion under ``group``, tagging ``object_id`` along the way.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    ``tag_object()`` replaces an object's full tag list for a taxonomy rather than appending, so
    this reads the object's existing tags first and unions in the new one, rather than silently
    dropping a sibling competency's tag under the same taxonomy. Raises ValidationError (keyed
    "object_id") when the taxonomy refuses the tag, e.g. it allows only one tag per object.

    When no rule fields are supplied, resolves to the live profile scoped to the tag's taxonomy,
    else the live system default, rather than persisting null: ADR-0002 Decision 4 always
    resolves to a concrete profile, and the model's own xor-override constraint forbids leaving
    all three fields null.

    ``course_run`` is ``group``'s course. Must run inside a transaction holding the tag and
    course run locks that associate_competency_criterion() takes, so the dominance check below
    can't race another create.
    """
    tag = group.tag
    # tag.taxonomy_id is nullable at the model level; already guaranteed set by the caller.
    assert tag.taxonomy_id is not None
    _validate_containment(group, course_run)

    existing_values = [existing_tag.value for existing_tag in get_object_tags(object_id, taxonomy_id=tag.taxonomy_id)]
    if tag.value not in existing_values:
        existing_values.append(tag.value)
    try:
        tag_object(object_id, tag.taxonomy, existing_values)
    except ValueError as exc:
        raise ValidationError({"object_id": str(exc)}) from exc
    object_tag = ObjectTag.objects.get(object_id=object_id, taxonomy_id=tag.taxonomy_id, tag_id=tag.id)

    if rule_profile_id is None and rule_type_override is None and rule_payload_override is None:
        profile = _get_applicable_rule_profile(tag.taxonomy_id)
        if profile is None:
            raise CompetencyRuleProfile.DoesNotExist("No live rule profile applies to this criterion.")
        rule_profile_id = profile.id

    return CompetencyCriterion.objects.create(
        group=group,
        object_tag=object_tag,
        rule_profile_id=rule_profile_id,
        rule_type_override=rule_type_override,
        rule_payload_override=rule_payload_override,
    )


def _reject_duplicate_criterion(tag: Tag, object_id: str, group_id: int | None) -> None:
    """
    Raise ValidationError if ``object_id`` is already associated with ``tag`` (in ``group_id``, when supplied).

    Must run inside a transaction holding the tag row lock.
    """
    assert tag.taxonomy_id is not None  # narrowing for mypy; callers resolve competency tags only
    existing_object_tag = ObjectTag.objects.filter(
        object_id=object_id, taxonomy_id=tag.taxonomy_id, tag_id=tag.id,
    ).first()
    if existing_object_tag is None:
        return
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


def _validate_rule_fields(
    rule_profile_id: int | None,
    rule_type_override: str | None,
    rule_payload_override: dict | None,
) -> None:
    """
    Raise ValidationError, keyed by the offending field, unless the rule fields form a valid combination.

    Valid: a live ``rule_profile_id`` alone, both overrides without a profile, or none of the three.
    """
    if rule_profile_id is not None and (rule_type_override is not None or rule_payload_override is not None):
        raise ValidationError({"rule_profile_id": _(
            "rule_profile_id cannot be combined with rule_type_override or rule_payload_override."
        )})
    if (rule_type_override is None) != (rule_payload_override is None):
        supplied_field = "rule_type_override" if rule_type_override is not None else "rule_payload_override"
        raise ValidationError({supplied_field: _(
            "rule_type_override and rule_payload_override must be supplied together."
        )})
    if rule_profile_id is None:
        return
    archived = CompetencyRuleProfile.objects.filter(pk=rule_profile_id).values_list("archived", flat=True).first()
    if archived is None:
        raise ValidationError({"rule_profile_id": _("No CompetencyRuleProfile matches rule_profile_id.")})
    if archived:
        # Archived profiles stay resolvable for existing criteria but are hidden from new associations.
        raise ValidationError({"rule_profile_id": _("rule_profile_id references an archived CompetencyRuleProfile.")})


def associate_competency_criterion(
    tag_id: int,
    object_id: str,
    *,
    group_id: int | None = None,
    logic_operator: str | None = None,
    rule_profile_id: int | None = None,
    rule_type_override: str | None = None,
    rule_payload_override: dict | None = None,
) -> CompetencyCriterion:
    """
    Associate ``object_id`` with the competency ``tag_id`` names, creating a criterion for it.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    The public entry point for the create-criterion endpoint; the caller is expected to have
    already checked ``oel_tagging.can_tag_object``. Rejects re-targeting the same group a
    (tag_id, object_id) pair already uses, and rejects a duplicate via the derive-or-create
    path; a *different* explicit group is a deliberate second association per ADR-0002, not a
    duplicate. Branches on ``group_id`` to resolve or derive/create a leaf, then creates the
    criterion, all inside one transaction so a downstream failure rolls back any group just
    created.

    Raises ValidationError (never DRF's) on rejected input, including an invalid combination of
    rule fields or a missing or archived ``rule_profile_id``; Tag.DoesNotExist if ``tag_id``
    doesn't resolve to a competency tag; CompetencyCriteriaGroup.DoesNotExist if ``group_id``
    doesn't resolve.
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

    if group_id is not None and logic_operator is not None:
        raise ValidationError({"logic_operator": _("group_id and logic_operator cannot both be supplied.")})
    _validate_rule_fields(rule_profile_id, rule_type_override, rule_payload_override)

    with transaction.atomic():
        # Lock order is always the tag, then the course run, so two creates can't deadlock.
        # The database enforces one root per tag; the tag lock only serializes _reject_duplicate_criterion(). That
        # check is check-then-act on both paths: without group_id a request always creates a fresh leaf, and with
        # group_id CompetencyCriterion has no unique constraint on (object_tag, group), so no index catches a
        # concurrent copy.
        Tag.objects.select_for_update().get(pk=tag.pk)
        # Serializes the per-course dominance check on CBE's own row, not the catalog's CourseRun.
        # Created first, since a locking read of a row that doesn't exist yet locks nothing; get_or_create()
        # resolves two first creates racing on the primary key.
        CompetencyCourseRun.objects.get_or_create(course_run=course_run)
        CompetencyCourseRun.objects.select_for_update().get(pk=course_run.pk)
        _reject_duplicate_criterion(tag, object_id, group_id)
        if group_id is not None:
            group = resolve_supplied_leaf_group(group_id, tag, course_run)
        else:
            group = _create_leaf_group(tag, course_run, logic_operator)
        return create_competency_criterion(
            group=group,
            object_id=object_id,
            course_run=course_run,
            rule_profile_id=rule_profile_id,
            rule_type_override=rule_type_override,
            rule_payload_override=rule_payload_override,
        )


def _validate_rule_source(
    rule_profile_id: int | None,
    rule_type_override: str | None,
    rule_payload_override: dict | None,
) -> None:
    """
    Validate the one rule source a bulk update applies.

    Runs the create path's rule-field checks, then also refuses a request naming no rule source: a
    bulk update has no applicable-profile fallback, and ADR-0002 Decision 4 allows a criterion no
    other state. Override values are validated once for the whole batch through the model's own
    ``full_clean()``, so the payload contract has no second copy here. Raises ValidationError keyed
    by the REST field names.
    """
    _validate_rule_fields(rule_profile_id, rule_type_override, rule_payload_override)
    if rule_profile_id is not None:
        return
    # _validate_rule_fields has already refused half a pair, so one override being None means both are.
    if rule_type_override is None:
        raise ValidationError(
            {NON_FIELD_ERRORS: _("Supply rule_profile_id, or both rule_type_override and rule_payload_override.")}
        )
    try:
        CompetencyCriterion(
            rule_type_override=rule_type_override,
            rule_payload_override=rule_payload_override,
        ).full_clean(exclude=["group", "object_tag", "rule_profile"], validate_unique=False, validate_constraints=False)
    except ValidationError as exc:
        errors = dict(exc.error_dict)
        # The model's clean() reports the payload's shape as a non-field error. A payload is only judged
        # against a valid rule type, so once rule_type_override is itself refused, that error just restates it.
        payload_errors = errors.pop(NON_FIELD_ERRORS, [])
        if payload_errors and "rule_type_override" not in errors:
            errors["rule_payload_override"] = [*errors.get("rule_payload_override", []), *payload_errors]
        raise ValidationError(errors) from exc


def _apply_rule_source(
    criterion: CompetencyCriterion,
    rule_profile_id: int | None,
    rule_type_override: str | None,
    rule_payload_override: dict | None,
    user: UserType,
    *,
    applicable: CompetencyRuleProfile | None,
) -> None:
    """
    Point ``criterion`` at ``rule_profile_id``, or give it the override values, saving only if that changes it.

    Override values that exactly match ``applicable``, the profile ADR-0002 Decision 4 assigns the
    criterion, reassign it to that profile instead. All three rule columns are written in one save,
    so the criterion's new history row never records both a profile and overrides, or neither.
    """
    # Compares the parsed rule, never stored JSON text, so key order and formatting don't matter.
    if (
        rule_profile_id is None
        and applicable is not None
        and applicable.rule_type == rule_type_override
        and applicable.rule_payload == rule_payload_override
    ):
        rule_profile_id = applicable.id

    target: tuple[int | None, str | None, dict | None]
    if rule_profile_id is not None:
        target, change_reason = (rule_profile_id, None, None), "Reassigned to rule profile"
    else:
        target, change_reason = (None, rule_type_override, rule_payload_override), "Rule values set"
    if (criterion.rule_profile_id, criterion.rule_type_override, criterion.rule_payload_override) == target:
        return

    criterion.rule_profile_id, criterion.rule_type_override, criterion.rule_payload_override = target
    # Set on the instance so attribution doesn't depend on simple_history's request middleware.
    criterion._history_user = user  # type: ignore[attr-defined]  # pylint: disable=protected-access
    criterion._change_reason = change_reason  # type: ignore[attr-defined]  # pylint: disable=protected-access
    # Never QuerySet.update(): it skips post_save, so simple_history would record nothing.
    criterion.save(update_fields=["rule_profile", "rule_type_override", "rule_payload_override"])


def bulk_update_competency_criteria(
    criterion_ids: list[int],
    group_id: int,
    *,
    rule_profile_id: int | None = None,
    rule_type_override: str | None = None,
    rule_payload_override: dict | None = None,
    user: UserType,
) -> list[CompetencyCriterion]:
    """
    Apply one rule source to every criterion ``criterion_ids`` names in one leaf group, atomically.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    Supply either ``rule_profile_id`` or both override values; each criterion's three rule columns
    are then rewritten together, so a profile clears any overrides and overrides clear any profile.
    Learner status is never touched.

    Returns the named criteria in request order. Raises PermissionDenied if ``user`` fails
    ``oel_tagging.can_tag_object`` for the group's taxonomy and course,
    CompetencyCriteriaGroup.DoesNotExist if the group doesn't exist, CompetencyCriterion.DoesNotExist
    if any named id isn't a criterion of that group, and ValidationError, keyed by the REST field
    names, for a non-leaf group, an empty or repeated id list, or an invalid rule source. Raises
    CompetencyCriterionArchivedError if the group, either of its ancestors, or any named criterion
    is archived.
    """
    with transaction.atomic():
        group = CompetencyCriteriaGroup.objects.select_related("tag__taxonomy", "parent__course").get(
            pk=group_id
        )
        # Criteria hang only off leaf groups, and a leaf's course is on its course-level parent.
        if group.parent is None or group.course_id is not None or group.parent.course is None:
            raise ValidationError({"group_id": _("group_id must reference a leaf CompetencyCriteriaGroup.")})
        # A group's tag is always a competency tag, so its taxonomy is always set.
        assert group.tag.taxonomy is not None
        perm_obj = ObjectTagPermissionItem(taxonomy=group.tag.taxonomy, object_id=str(group.parent.course.course_key))
        if not user.has_perm("oel_tagging.can_tag_object", perm_obj):
            raise PermissionDenied

        if not criterion_ids:
            raise ValidationError({"criterion_ids": _("criterion_ids must name at least one criterion.")})
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValidationError({"criterion_ids": _("criterion_ids must not name the same criterion twice.")})
        _validate_rule_source(rule_profile_id, rule_type_override, rule_payload_override)

        # Archiving a group doesn't archive its criteria, yet ADR 0002 Decision 2 closes them to authoring.
        # An archived ancestor counts too: it stands in for a delete that would have cascaded to this leaf.
        # Locked for the same reason as the criteria below.
        lineage = [group.id, group.parent.id, group.parent.parent_id]
        archived_by_id = dict(
            CompetencyCriteriaGroup.objects.select_for_update().filter(pk__in=lineage).values_list("pk", "archived")
        )
        archived_group_id = next((pk for pk in lineage if archived_by_id.get(pk)), None)
        if archived_group_id is not None:
            raise CompetencyCriterionArchivedError(
                _("Criteria under an archived group cannot be edited: group {group_id}.").format(
                    group_id=archived_group_id
                )
            )

        # Locked so a concurrent archive can't slip in after the archived check, and so a concurrent
        # identical request waits and then sees nothing to change instead of writing a second history row.
        criteria_by_id = CompetencyCriterion.objects.select_for_update().filter(group=group).in_bulk(criterion_ids)
        missing_ids = [criterion_id for criterion_id in criterion_ids if criterion_id not in criteria_by_id]
        if missing_ids:
            raise CompetencyCriterion.DoesNotExist(
                _("Not criteria of this group: {criterion_ids}.").format(
                    criterion_ids=", ".join(str(criterion_id) for criterion_id in missing_ids)
                )
            )

        # One archived criterion refuses the whole batch: ADR 0002 Decision 4 closes archived criteria to authoring.
        archived_ids = [criterion_id for criterion_id in criterion_ids if criteria_by_id[criterion_id].archived]
        if archived_ids:
            raise CompetencyCriterionArchivedError(
                _("Archived criteria cannot be edited: {criterion_ids}.").format(
                    criterion_ids=", ".join(str(criterion_id) for criterion_id in archived_ids)
                )
            )

        # Looked up once for the whole batch, and only when override values need comparing against it:
        # every criterion in a leaf shares the group's tag, so they all resolve to the same profile.
        applicable = _get_applicable_rule_profile(group.tag.taxonomy.id) if rule_profile_id is None else None
        criteria = [criteria_by_id[criterion_id] for criterion_id in criterion_ids]
        for criterion in criteria:
            _apply_rule_source(
                criterion,
                rule_profile_id,
                rule_type_override,
                rule_payload_override,
                user,
                applicable=applicable,
            )
        return criteria


def _effective_rule(criterion: CompetencyCriterion) -> tuple[str, Any]:
    """Return (rule_type, payload) for whichever of criterion's profile or override is set."""
    if criterion.rule_profile_id is not None:
        # The oel_cbe_criterion_profile_xor_override_check constraint guarantees rule_profile is
        # set whenever rule_profile_id is; this assertion only narrows that fact for mypy.
        assert criterion.rule_profile is not None
        return criterion.rule_profile.rule_type, criterion.rule_profile.rule_payload
    # Same constraint, the other way around: rule_type_override is set whenever rule_profile_id
    # is not.
    assert criterion.rule_type_override is not None
    return criterion.rule_type_override, criterion.rule_payload_override


def _raise_leaf_status(user_id: int, criterion_id: int, status: MasteryStatus, now: datetime) -> int:
    """
    Create or raise a learner's leaf status row for one criterion; never lowers a stored status.

    Returns the count of rows created or raised (0 or 1), matching the semantics of
    ``QuerySet.update()``'s own return value.
    """
    row, created = StudentCompetencyCriterionStatus.objects.get_or_create(
        user_id=user_id, criterion_id=criterion_id,
        defaults={"status_id": status, "created": now, "modified": now},
    )
    if created:
        return 1
    return StudentCompetencyCriterionStatus.objects.filter(
        pk=row.pk, status_id__lt=status,
    ).update(status_id=status, modified=now)


def record_graded_object_statuses(*, user_id: int, scores: Sequence[GradedObjectScore]) -> int:
    """
    Record a learner's leaf-level competency status for each graded object in ``scores``.

    UNSTABLE: the rollup and learner-status read entry points still to come may change what this
    function returns without a deprecation cycle.

    Call this only from inside the same transaction that also writes the grade: this function opens
    no transaction of its own and lets any storage error propagate rather than swallowing it, so the
    grade and its leaf statuses always commit or roll back together. Pass each ``object_id`` at most
    once, using the exact string already stored in ``ObjectTag.object_id``; the match performed
    against that value is case-sensitive. Leave out of ``scores`` any object worth zero possible
    points and any object the learner has not attempted. Only criteria whose tag belongs to a
    competency taxonomy are evaluated, and archived criteria are skipped. An evaluated criterion's
    status is only ever raised, never lowered. The return value is the count of leaf rows created or
    raised, where ``0`` means nothing changed. This function performs no authorization check of its
    own; as with ``openedx_tagging.api``, the caller is responsible for enforcing permissions.

    :raises TransactionManagementError: if called outside an atomic block.
    """
    # In autocommit mode get_or_create() would commit a leaf row on its own, apart from the grade.
    if not connection.in_atomic_block:
        raise TransactionManagementError("record_graded_object_statuses() must run inside the grade's transaction.")
    if not scores:
        return 0

    fraction_by_object_id: dict[str, Decimal] = {}
    for score in scores:
        if score.object_id in fraction_by_object_id:
            raise ValueError(f"duplicate object_id in scores: {score.object_id!r}")
        fraction_by_object_id[score.object_id] = score.fraction

    criteria = (
        CompetencyCriterion.objects.filter(
            object_tag__object_id__in=list(fraction_by_object_id),
            object_tag__taxonomy__competencytaxonomy__isnull=False,
            archived=False,
        )
        .select_related("object_tag", "rule_profile")
        .order_by("pk")
    )

    now = datetime.now(tz=timezone.utc)
    changed = 0
    for criterion in criteria:
        fraction = fraction_by_object_id[criterion.object_tag.object_id]
        rule_type, payload = _effective_rule(criterion)
        try:
            demonstrated = evaluate_rule(rule_type, payload, fraction)
        except ValidationError as exc:
            log.warning(
                "Skipping CompetencyCriterion %s for user %s on object %s: its rule cannot be applied: %s",
                criterion.pk, user_id, criterion.object_tag.object_id, exc.messages,
            )
            continue
        status = MasteryStatus.DEMONSTRATED if demonstrated else MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED
        changed += _raise_leaf_status(user_id, criterion.pk, status, now)

    return changed


def update_competency_criteria_group(
    group_id: int,
    *,
    logic_operator: str,
    user: UserType,
) -> CompetencyCriteriaGroup:
    """
    Set how the CompetencyCriteriaGroup ``group_id`` names combines its children, and return it.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and archive behavior
    may change this function's shape without a deprecation cycle.

    ``logic_operator`` is the only thing this changes. The group's name, ordering, competency,
    course, and parent stay as they were created, and so does everything beneath it. A request
    that leaves ``logic_operator`` as stored saves nothing, so the group's history gains a row
    only for a real change. Learner status is never read or written.

    Raises CompetencyCriteriaGroup.DoesNotExist if no group has ``group_id``, or Tag.DoesNotExist
    if its tag isn't on a CompetencyTaxonomy.
    Raises ValidationError, keyed by the REST field names, for a root group, a group with no course
    anywhere above it to check permission against, or a ``logic_operator`` other than AND or OR, and
    PermissionDenied if ``user`` fails ``oel_tagging.can_tag_object`` for the group's taxonomy and
    course. Raises CompetencyCriteriaGroupArchivedError if the group is archived, even when nothing
    would change.
    """
    with transaction.atomic():
        group = CompetencyCriteriaGroup.objects.select_related("course", "parent__course").get(pk=group_id)
        # Nothing in the model stops a group's tag being on another kind of taxonomy, so it's checked
        # the way the create and tree endpoints check theirs.
        tag = resolve_competency_tag(group.tag_id)
        # tag.taxonomy is already confirmed set; re-asserted since that doesn't cross function boundaries for mypy.
        assert tag.taxonomy is not None
        # A root has neither a parent nor a course, and its subtree can span several courses, so
        # there's no one course to check permission against.
        if group.parent_id is None and group.course_id is None:
            raise ValidationError({"group_id": _("A root CompetencyCriteriaGroup cannot be targeted directly.")})
        # A course-level group carries its own course, and a group beneath one takes the course of
        # its nearest ancestor that has one, however deeply it's nested.
        ancestor = group
        while ancestor.course is None and ancestor.parent is not None:
            ancestor = ancestor.parent
        course_run = ancestor.course
        if course_run is None:
            raise ValidationError(
                {"group_id": _("This CompetencyCriteriaGroup has no course above it to check permission against.")}
            )
        perm_obj = ObjectTagPermissionItem(taxonomy=tag.taxonomy, object_id=str(course_run.course_key))
        # has_perm()'s stub expects a Model, but a rules predicate takes any object.
        if not user.has_perm("oel_tagging.can_tag_object", perm_obj):  # type: ignore[arg-type]
            raise PermissionDenied

        # Null is a stored state (a single-child group), but never one a caller may ask for.
        if logic_operator not in LogicOperator.values:
            raise ValidationError(
                {"logic_operator": _("logic_operator must be one of: {choices}.").format(
                    choices=", ".join(LogicOperator.values),
                )}
            )
        # ADR-0002 Decision 2 hides archived groups from authoring; nothing reopens one here.
        if group.archived:
            raise CompetencyCriteriaGroupArchivedError(_("An archived CompetencyCriteriaGroup cannot be edited."))
        if group.logic_operator == logic_operator:
            return group

        group.logic_operator = logic_operator
        # Set on the instance so attribution doesn't depend on simple_history's request middleware.
        group._history_user = user  # type: ignore[attr-defined]  # pylint: disable=protected-access
        # Never QuerySet.update(): it skips post_save, so simple_history would record nothing.
        group.save(update_fields=["logic_operator"])
        return group


def _archive_group(group: CompetencyCriteriaGroup, user: UserType) -> None:
    """Archive ``group``, writing only the flag and recording ``user`` in its history."""
    group.archived = True
    # Set on the instance so attribution doesn't depend on simple_history's request middleware.
    group._history_user = user  # type: ignore[attr-defined]  # pylint: disable=protected-access
    group.save(update_fields=["archived"])


def _cascade_from_group(group: CompetencyCriteriaGroup, event: Literal["delete", "archive"], user: UserType) -> None:
    """
    Walk `group` and its ancestors, collapsing/archiving any left with no live children.

    Checking both relations (`criteria` and `child_groups`) at every level is harmless: the one that
    does not apply is an empty queryset, and it avoids a separate leaf/non-leaf code path.

    The walk stops at a group that is already archived. Everything above it was handled when it was
    archived, so writing it again would only add a redundant history row.
    """
    if group.archived:
        return
    has_live_child = (
        group.criteria.filter(archived=False).exists() or group.child_groups.filter(archived=False).exists()
    )
    parent = group.parent
    if has_live_child:
        return
    if event == "delete" and not group.criteria.exists() and not group.child_groups.exists():
        if _group_has_learner_progress(group.id):
            _archive_group(group, user)
            next_event: Literal["delete", "archive"] = "archive"
        else:
            group.delete()
            next_event = "delete"
    else:
        # Only archived children remain.
        _archive_group(group, user)
        next_event = "archive"
    if parent is not None:
        _cascade_from_group(parent, next_event, user)


def _archive_criterion(criterion: CompetencyCriterion, user: UserType) -> None:
    """
    Archive ``criterion`` in place and cascade the archive to any ancestor left with no live children.

    Does nothing if ``criterion`` is already archived, so a repeat call writes no history.
    """
    if criterion.archived:
        return
    criterion.archived = True
    # Set on the instance so attribution doesn't depend on simple_history's request middleware.
    criterion._history_user = user  # type: ignore[attr-defined]  # pylint: disable=protected-access
    criterion.save(update_fields=["archived"])
    _cascade_from_group(criterion.group, "archive", user)


def _criterion_has_learner_progress(criterion_id: int) -> bool:
    """Return whether a learner status row references ``criterion_id``."""
    return StudentCompetencyCriterionStatus.objects.filter(criterion_id=criterion_id).exists()


def _group_has_learner_progress(group_id: int) -> bool:
    """Return whether a learner group-level status row references ``group_id``."""
    return StudentCompetencyCriteriaGroupStatus.objects.filter(group_id=group_id).exists()


def _lineage_group_ids(group: CompetencyCriteriaGroup) -> list[int]:
    """Return the ids of ``group`` and every ancestor up to the root, one query per level."""
    ids = [group.pk]
    parent_id = group.parent_id
    while parent_id is not None:
        ids.append(parent_id)
        parent_id = CompetencyCriteriaGroup.objects.values_list("parent_id", flat=True).get(pk=parent_id)
    return ids


def delete_or_archive_competency_criterion(criterion_id: int, user: UserType) -> CriterionDeletionResult:
    """
    Hard-delete `criterion_id`, or archive it if a learner status row already references it.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    ``user`` must hold ``oel_tagging.can_tag_object`` for the criterion's object, as
    :func:`bulk_update_competency_criteria` requires; otherwise Django's ``PermissionDenied`` is
    raised before anything is locked or changed.

    An ancestor :class:`CompetencyCriteriaGroup` left with no live children by this call is
    cascaded in turn: hard-deleted if it carries no group-level status of its own, archived
    otherwise. See ADR-0002 Decision 2.

    Raises CompetencyCriterion.DoesNotExist if no such criterion exists (including one deleted
    concurrently) and PermissionDenied if ``user`` may not tag the criterion's object.

    The whole operation runs in one transaction that locks the competency's Tag, then the
    criterion's group and every ancestor group, then the criterion. A concurrent create for the
    same competency waits on the Tag lock. A learner-progress insert on the criterion or on any
    ancestor group waits on that row's lock. The progress checks therefore cannot go stale, so
    no fallback or retry is needed.
    """
    criterion = CompetencyCriterion.objects.select_related("group__tag__taxonomy", "object_tag").get(pk=criterion_id)
    # A group's tag is always a competency tag, so its taxonomy is always set.
    assert criterion.group.tag.taxonomy is not None
    perm_obj = ObjectTagPermissionItem(taxonomy=criterion.group.tag.taxonomy, object_id=criterion.object_tag.object_id)
    # The rules-based permission takes an ObjectTagPermissionItem, but django-stubs types the
    # has_perm object argument as a Model.
    if not user.has_perm("oel_tagging.can_tag_object", cast(Any, perm_obj)):
        raise PermissionDenied()
    tag_id = criterion.group.tag_id
    # Tree changes are serialized by the Tag lock, so reading the lineage before taking it is safe.
    lineage_ids = _lineage_group_ids(criterion.group)
    with transaction.atomic():
        Tag.objects.select_for_update().get(pk=tag_id)
        # Evaluated so the locks are taken now, in primary-key order to avoid deadlocks between writers.
        list(CompetencyCriteriaGroup.objects.select_for_update().filter(pk__in=lineage_ids).order_by("pk"))
        # A concurrent delete may have won; DoesNotExist propagates and the view maps it to 404.
        criterion = CompetencyCriterion.objects.select_for_update().get(pk=criterion_id)
        if _criterion_has_learner_progress(criterion_id):
            _archive_criterion(criterion, user)
            return CriterionDeletionResult(id=criterion_id, archived=True)
        group = criterion.group
        object_tag = criterion.object_tag
        criterion.delete()
        if not CompetencyCriterion.objects.filter(object_tag_id=object_tag.id).exists():
            existing_values = [
                t.value for t in get_object_tags(object_tag.object_id, taxonomy_id=object_tag.taxonomy_id)
                if t.value != group.tag.value
            ]
            tag_object(object_tag.object_id, group.tag.taxonomy, existing_values)
        _cascade_from_group(group, "delete", user)
        return CriterionDeletionResult(id=criterion_id, archived=False)
