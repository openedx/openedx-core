"""
Public API for Competency-Based Education (CBE).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence, cast

from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import F, Q, QuerySet
from django.db.transaction import TransactionManagementError
from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey

from openedx_catalog.api import get_course_run, get_course_run_ids
from openedx_catalog.models_api import CourseRun
from openedx_tagging.api import create_taxonomy, get_ancestor_tags, get_descendant_tags, get_object_tags, tag_object
from openedx_tagging.models import ObjectTag, Tag, Taxonomy
from openedx_tagging.rules import ObjectTagPermissionItem, UserType

from .data import CriterionDeletionResult, GradedObjectScore, GroupDeletionResult
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
from .rollup import roll_up_competency_statuses
from .rule_payloads import evaluate_rule
from .statuses import raise_status

__all__ = [
    "BlockedCourse",
    "CompetencyCriteriaGroupArchivedError",
    "CompetencyCriteriaTree",
    "CompetencyCriterionArchivedError",
    "CriterionDeletionResult",
    "GradedObjectScore",
    "GroupDeletionResult",
    "associate_competency_criterion",
    "bulk_update_competency_criteria",
    "create_competency_criterion",
    "create_competency_taxonomy",
    "delete_or_archive_competency_criteria_group",
    "delete_or_archive_competency_criterion",
    "get_blocked_courses",
    "get_competency_criteria_tree",
    "get_competency_rule_profiles",
    "get_courses_with_live_criteria",
    "is_competency_taxonomy",
    "record_graded_object_statuses",
    "resolve_competency_criteria_group_course",
    "resolve_competency_tag",
    "resolve_supplied_leaf_group",
    "roll_up_competency_statuses",
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

    ``criteria_count`` is ``len(criteria)``. ``total_criteria_count`` counts the criteria under
    every reachable group before any course scoping.
    """

    groups: list[CompetencyCriteriaGroup]
    criteria: list[CompetencyCriterion]
    criteria_count: int
    total_criteria_count: int


def get_competency_criteria_tree(tag: Tag, course_keys: list[CourseKey] | None = None) -> CompetencyCriteriaTree:
    """
    Return the non-archived CompetencyCriteriaGroup tree and CompetencyCriterion leaves for ``tag``.

    UNSTABLE: the criteria-authoring family is incomplete, so its group update and archive
    behavior may change this function's shape without a deprecation cycle.

    A group is reachable only if it and every ancestor up to its root are non-archived. A
    criterion is returned only if it is non-archived and its group is returned, so the result
    always forms one tree: no returned ``parent_id`` or ``group_id`` names a row that was left out.

    ``course_keys`` scopes the result by course run. A course run's groups are its course-level
    group (the group whose ``course`` is that run) and every group nested below it, at any depth.
    The groups above any course are the ones not nested in any course-level group.

    - ``None`` (the default) returns every reachable group.
    - A list returns the groups above any course plus the groups of the named course runs.
      ``[]`` returns only the groups above any course.
    - A key that matches no course run is dropped, and a repeated key counts once, at its
      first position. A list in which no key resolves returns only the groups above any course.

    Groups come back with the groups above any course first, then the named courses in the order
    requested, and within each of those by ``(parent_id, ordering, id)`` with root-level rows first.
    Criteria are ordered by their group's position in ``groups``, then ``id``.

    ``criteria_count`` is ``len(criteria)``. ``total_criteria_count`` is the number of criteria
    under every reachable group before ``course_keys`` is applied, so a caller scoped down to
    zero criteria can still tell the competency is not empty.
    """
    groups = list(
        CompetencyCriteriaGroup.objects.filter(tag=tag, archived=False)
        .select_related("course")
        .order_by(F("parent_id").asc(nulls_first=True), "ordering", "id")
    )
    if not groups:
        return CompetencyCriteriaTree(groups=[], criteria=[], criteria_count=0, total_criteria_count=0)

    # Walk down from the roots instead of relying on row order. A group's parent is fixed once
    # saved, but a QuerySet.update() can still give a group a parent with a larger id, and
    # ordering by parent_id would then visit that child before its parent.
    groups_by_parent_id: dict[int | None, list[CompetencyCriteriaGroup]] = {}
    for group in groups:
        groups_by_parent_id.setdefault(group.parent_id, []).append(group)
    # Maps each reachable group id to the course run id of its branch, or None above any course.
    branch_course_ids: dict[int, int | None] = {}
    pending = [(group, group.course_id) for group in groups_by_parent_id.get(None, [])]
    while pending:
        group, branch_course_id = pending.pop()
        branch_course_ids[group.id] = branch_course_id
        for child in groups_by_parent_id.get(group.id, []):
            pending.append((child, branch_course_id if branch_course_id is not None else child.course_id))
    reachable_groups = [group for group in groups if group.id in branch_course_ids]

    if course_keys is None:
        scoped_groups = reachable_groups
    else:
        run_ids_by_key = get_course_run_ids(course_keys)
        rank_by_run_id = {
            run_ids_by_key[key]: rank
            for rank, key in enumerate((key for key in dict.fromkeys(course_keys) if key in run_ids_by_key), start=1)
        }
        # Groups above any course rank 0, so a scoped tree never orphans a child from its parent, and
        # a branch ranks by its course. A branch whose course was not requested is left out.
        # sorted() is stable, so each rank keeps the query order.
        ranked_groups = []
        for group in reachable_groups:
            group_course_id = branch_course_ids[group.id]
            if group_course_id is None:
                ranked_groups.append((0, group))
            elif group_course_id in rank_by_run_id:
                ranked_groups.append((rank_by_run_id[group_course_id], group))
        scoped_groups = [group for _, group in sorted(ranked_groups, key=lambda ranked: ranked[0])]

    position_by_group_id = {group.id: position for position, group in enumerate(scoped_groups)}
    criteria = list(
        CompetencyCriterion.objects.filter(group_id__in=position_by_group_id.keys(), archived=False)
        .select_related("object_tag")
        .order_by("group_id", "id")
    )
    # sort() is stable, so criteria of one group keep their id order.
    criteria.sort(key=lambda criterion: position_by_group_id[criterion.group_id])
    if course_keys is None:
        total_criteria_count = len(criteria)
    else:
        total_criteria_count = CompetencyCriterion.objects.filter(
            group_id__in=branch_course_ids.keys(), archived=False,
        ).count()

    return CompetencyCriteriaTree(
        groups=scoped_groups,
        criteria=criteria,
        criteria_count=len(criteria),
        total_criteria_count=total_criteria_count,
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
    from a concurrent insert. The caller's tag lock is for ``_reject_duplicate_criterion()``. The delete path
    relies on the same lock to keep this function's inserts out of its emptiness-check window.
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


def get_courses_with_live_criteria(
    tag_ids: Iterable[int], course: CourseRun | None = None, *, course_keys: Iterable[CourseKey] | None = None,
) -> dict[CourseRun, Tag]:
    """
    Return every course where one of ``tag_ids`` has a live CompetencyCriterion.

    UNSTABLE: the criteria-authoring family is incomplete, so its group update and archive
    behavior may change this function's shape without a deprecation cycle.

    Maps each conflicting CourseRun to the lowest-id tag (of ``tag_ids``) with a criterion there,
    narrowed by ``course`` and by ``course_keys``, each when given. Archived criteria and criteria
    under an archived group are ignored.
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
    if course_keys is not None:
        groups = groups.filter(parent__course__course_key__in=list(course_keys))
    result: dict[CourseRun, Tag] = {}
    for group in groups.select_related("tag", "parent__course").distinct().order_by("tag_id"):
        assert group.parent is not None, f"CompetencyCriteriaGroup {group.id} has no parent"
        assert group.parent.course is not None, f"CompetencyCriteriaGroup {group.id}'s parent has no course"
        result.setdefault(group.parent.course, group.tag)
    return result


@dataclass(frozen=True, kw_only=True, slots=True)
class BlockedCourse:
    """
    A course where a taxonomy ancestor or descendant of a competency already has a live CompetencyCriterion.

    UNSTABLE: the criteria-authoring family is incomplete, so this shape may change without a
    deprecation cycle.
    """

    course_key: CourseKey
    conflicting_tag_id: int
    conflicting_tag_value: str


def _get_ancestor_and_descendant_tag_ids(tag: Tag) -> set[int]:
    """
    Return the ids of every taxonomy ancestor and descendant of ``tag``.

    The blocked-courses read and the write-time containment check both use this, so they cannot disagree.
    """
    return (
        set(get_ancestor_tags(tag).values_list("id", flat=True))
        | set(get_descendant_tags(tag).values_list("id", flat=True))
    )


def get_blocked_courses(tag: Tag, course_keys: Iterable[CourseKey] | None = None) -> list[BlockedCourse]:
    """
    Return the courses where a taxonomy ancestor or descendant of ``tag`` already has a live criterion.

    UNSTABLE: the criteria-authoring family is incomplete, so its group update and archive
    behavior may change this function's shape without a deprecation cycle.

    Narrowed to ``course_keys`` when given. Results are sorted by course key. When several ancestors
    or descendants have a criterion in one course, the one with the lowest id is named. Criteria on
    ``tag`` itself never block a course. Requested keys that match no blocked course are dropped.
    """
    ancestor_and_descendant_ids = _get_ancestor_and_descendant_tag_ids(tag)
    if not ancestor_and_descendant_ids:
        return []
    conflicts = get_courses_with_live_criteria(ancestor_and_descendant_ids, course_keys=course_keys)
    rows = []
    for course_run, conflicting_tag in conflicts.items():
        # Typed optional, but save() always sets it.
        assert course_run.course_key is not None, f"CourseRun {course_run.id} has no course_key"
        rows.append(BlockedCourse(
            course_key=course_run.course_key,
            conflicting_tag_id=conflicting_tag.id,
            conflicting_tag_value=conflicting_tag.value,
        ))
    return sorted(rows, key=lambda row: str(row.course_key))


def _validate_containment(group: CompetencyCriteriaGroup, course: CourseRun) -> None:
    """
    Raise ValidationError (keyed "tag_id") if ``group.tag`` conflicts with an existing criterion.

    A conflict is a taxonomy ancestor or descendant of ``group.tag`` that already has a
    CompetencyCriterion in ``course``.
    """
    tag = group.tag
    ancestor_and_descendant_ids = _get_ancestor_and_descendant_tag_ids(tag)
    if not ancestor_and_descendant_ids:
        return
    conflicting_tag = get_courses_with_live_criteria(ancestor_and_descendant_ids, course=course).get(course)
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

    ``course_run`` is ``group``'s course. Must run inside a transaction. This function takes the
    competency's Tag row lock itself, so a concurrent delete of a sibling criterion can't cascade
    this call's new rows away. It takes the lock here, not only in its caller, so a future direct
    caller of this public function can't bypass it: the delete path's correctness depends on every
    creator holding it. The caller must also hold the course run lock that
    associate_competency_criterion() takes, so the dominance check below can't race another create,
    and must take the Tag lock before the course run lock.
    """
    # Re-acquiring the lock associate_competency_criterion() already holds is harmless within one transaction.
    Tag.objects.select_for_update().get(pk=group.tag_id)
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
        # The database enforces one root per tag; the tag lock serializes _reject_duplicate_criterion(). That
        # check is check-then-act on both paths: without group_id a request always creates a fresh leaf, and with
        # group_id CompetencyCriterion has no unique constraint on (object_tag, group), so no index catches a
        # concurrent copy. delete_or_archive_competency_criterion() depends on this lock too: it keeps this
        # function's inserts out of the delete's emptiness-check window.
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


def _set_history_user(instance: Any, user: UserType) -> None:
    """Record ``user`` as the author of ``instance``'s next history row; API callers have no request middleware."""
    instance._history_user = user  # type: ignore[attr-defined]  # pylint: disable=protected-access


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
    _set_history_user(criterion, user)
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
    is archived. Raises Tag.DoesNotExist if the group's competency tag was deleted concurrently.
    """
    with transaction.atomic():
        group = CompetencyCriteriaGroup.objects.select_related("tag__taxonomy", "parent__course").get(
            pk=group_id
        )
        # Criteria hang only off leaf groups, and a leaf's course is on its course-level parent.
        if group.parent is None or group.course_id is not None or group.parent.course is None:
            raise ValidationError({"group_id": _("group_id must reference a leaf CompetencyCriteriaGroup.")})
        # The create path only accepts competency tags, and a group's tag never changes, so its taxonomy is set.
        assert group.tag.taxonomy is not None
        perm_obj = ObjectTagPermissionItem(taxonomy=group.tag.taxonomy, object_id=str(group.parent.course.course_key))
        if not user.has_perm("oel_tagging.can_tag_object", perm_obj):
            raise PermissionDenied

        if not criterion_ids:
            raise ValidationError({"criterion_ids": _("criterion_ids must name at least one criterion.")})
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValidationError({"criterion_ids": _("criterion_ids must not name the same criterion twice.")})
        _validate_rule_source(rule_profile_id, rule_type_override, rule_payload_override)

        # Every CBE authoring writer of a competency's criteria takes its Tag row lock first and no other row lock
        # before it, so those writers can't deadlock with each other. Django's cascade deletes of a Tag, a Taxonomy
        # or a CourseRun take the Tag row last, and can deadlock with a create or a hard delete: one side fails
        # with a MySQL 1213 error and no data is corrupted. Everything below reads committed state, since a
        # delete, an archive or a create for this competency can't commit while this transaction holds the lock.
        # The reads above are safe without it because a group's tag, parent and course never change.
        Tag.objects.select_for_update().get(pk=group.tag_id)

        # Archiving a group doesn't archive its criteria, yet ADR 0002 Decision 2 closes them to authoring.
        # An archived ancestor counts too: it stands in for a delete that would have cascaded to this leaf.
        lineage = [group.id, group.parent.id, group.parent.parent_id]
        archived_by_id = dict(CompetencyCriteriaGroup.objects.filter(pk__in=lineage).values_list("pk", "archived"))
        archived_group_id = next((pk for pk in lineage if archived_by_id.get(pk)), None)
        if archived_group_id is not None:
            raise CompetencyCriterionArchivedError(
                _("Criteria under an archived group cannot be edited: group {group_id}.").format(
                    group_id=archived_group_id
                )
            )

        # No row lock here: the Tag lock already stops a concurrent archive from slipping in after the archived
        # check, and makes a concurrent identical request wait and then see nothing to change instead of
        # writing a second history row. Locking the lineage or the criteria as well would only add a second
        # lock order for writers to deadlock on.
        criteria_by_id = CompetencyCriterion.objects.filter(group=group).in_bulk(criterion_ids)
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
        # Saves run in ascending primary-key order because the grading write inserts status rows in ascending
        # criterion-pk order, and each insert takes an implicit shared foreign-key lock on its criterion row;
        # a different order here could deadlock with it.
        for criterion in sorted(criteria, key=lambda item: item.pk):
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

    Call this at most once per transaction. Its implicit criterion-row locks are taken in ascending
    primary-key order within one call, and two calls in one transaction would form two ascending runs
    that can deadlock with a concurrent bulk update.

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
        changed += raise_status(
            StudentCompetencyCriterionStatus.objects,
            lookup={"user_id": user_id, "criterion_id": criterion.pk},
            status=status,
            now=now,
        )

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
    if its tag isn't on a CompetencyTaxonomy or was deleted concurrently.
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

        # The checks above read only a group's tag, parent and course, which never change, so they run before
        # the lock and a caller who fails them never holds it.
        Tag.objects.select_for_update().get(pk=tag.pk)
        # Re-read under the Tag lock: a concurrent delete or archive may have won.
        group = CompetencyCriteriaGroup.objects.select_related("course").get(pk=group_id)

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
        _set_history_user(group, user)
        # Never QuerySet.update(): it skips post_save, so simple_history would record nothing.
        group.save(update_fields=["logic_operator"])
        return group


def _archive_group(group: CompetencyCriteriaGroup, user: UserType) -> None:
    """Archive ``group``, writing only the flag and recording ``user`` in its history."""
    group.archived = True
    _set_history_user(group, user)
    group.save(update_fields=["archived"])


def _cascade_remove_from_group(group: CompetencyCriteriaGroup, user: UserType) -> None:
    """
    Walk ``group`` and its ancestors, deleting or archiving any left with no live children.

    An archived group is never saved again: everything above it was handled when it was archived,
    so a second save would only add a redundant history row. If it has no criteria, no child groups
    and no group-level status, it is deleted and the walk continues upward, so no empty group stays
    persisted. Otherwise the walk stops there.
    """
    parent = group.parent
    if group.archived:
        if group.criteria.exists() or group.child_groups.exists():
            return
        if _group_has_learner_progress(group.id):
            return
        _set_history_user(group, user)
        group.delete()
        if parent is not None:
            _cascade_remove_from_group(parent, user)
        return
    has_live_child = (
        group.criteria.filter(archived=False).exists() or group.child_groups.filter(archived=False).exists()
    )
    if has_live_child:
        return
    if not group.criteria.exists() and not group.child_groups.exists():
        if _group_has_learner_progress(group.id):
            _archive_group(group, user)
        else:
            _set_history_user(group, user)
            group.delete()
    else:
        # Only archived children remain.
        _archive_group(group, user)
    if parent is not None:
        _cascade_remove_from_group(parent, user)


def _write_criterion_archived(criterion: CompetencyCriterion, user: UserType) -> bool:
    """
    Archive ``criterion`` in place, recording ``user`` in its history, without cascading to its group.

    Returns False and writes nothing if ``criterion`` is already archived, so a repeat call adds no history row.
    """
    if criterion.archived:
        return False
    criterion.archived = True
    _set_history_user(criterion, user)
    criterion.save(update_fields=["archived"])
    return True


def _archive_criterion(criterion: CompetencyCriterion, user: UserType) -> None:
    """
    Archive ``criterion`` in place and cascade the archive to any ancestor left with no live children.

    Does nothing if ``criterion`` is already archived, so a repeat call writes no history.
    """
    if _write_criterion_archived(criterion, user):
        _cascade_remove_from_group(criterion.group, user)


def _criterion_has_learner_progress(criterion_id: int) -> bool:
    """Return whether a learner status row references ``criterion_id``."""
    return StudentCompetencyCriterionStatus.objects.filter(criterion_id=criterion_id).exists()


def _group_has_learner_progress(group_id: int) -> bool:
    """Return whether a learner group-level status row references ``group_id``."""
    return StudentCompetencyCriteriaGroupStatus.objects.filter(group_id=group_id).exists()


def delete_or_archive_competency_criterion(criterion_id: int, user: UserType) -> CriterionDeletionResult:
    """
    Hard-delete ``criterion_id``, or archive it if a learner status row already references it.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    ``user`` must hold ``oel_tagging.can_tag_object`` for the taxonomy of the criterion's group and for
    the usage key its ``ObjectTag`` tags. The check runs before anything is locked or changed.

    An ancestor :class:`CompetencyCriteriaGroup` left with no live children by this call is cascaded in
    turn: hard-deleted if it has no children of any kind and no group-level status of its own, archived
    otherwise, including when only archived children remain. See ADR-0002 Decision 2.

    On a hard delete, the criterion's own ObjectTag is deleted too, and only when no other
    criterion references it. No other ObjectTag on the object is touched.

    Raises ``CompetencyCriterion.DoesNotExist`` if no such criterion exists (including one deleted
    concurrently), ``Tag.DoesNotExist`` if its competency tag was deleted concurrently, Django's
    ``PermissionDenied`` if ``user`` fails the permission check, and Django's ``ValidationError`` if
    the archive path's ``full_clean()`` rejects an invalid stored rule payload.

    The first read and the permission check run before the lock. The rest runs in one transaction that
    takes only one explicit lock, on the competency's Tag row, the lock every CBE authoring writer of its
    criteria takes first, so a concurrent create waits until this call commits. The lock is held until the
    request commits, so other authoring writers for this competency, and inserts that reference its Tag
    row, wait meanwhile.

    The re-read under the lock and the ancestor walk's ``exists()`` checks rely on READ COMMITTED, the
    isolation level this repo always runs (see ``AGENTS.md``): they must see concurrent commits.

    A learner's grade saved at the same moment as a hard delete never loses data silently, but it can
    fail one side with an error. The delete checks for a status row and then deletes the criterion, and a
    grade write that inserts a status row in between is not waited for. There are three variants when
    the grade is the one that gets in first:

    - A status row committed before the status check: the delete archives the criterion, the normal path.
    - A status row committed after the check and before ``criterion.delete()`` collects its rows:
      ``ProtectedError``.
    - A status row inserted but not yet committed when the delete runs: the delete waits for the grade
      transaction, then fails the foreign-key check with ``IntegrityError``.

    ``ProtectedError`` is a subclass of ``IntegrityError``. Either one rolls this transaction back and
    leaves the criterion in place, and the REST view maps both to a retryable 409, since a second delete
    archives the criterion. If the DELETE comes first instead, the grade's INSERT waits for it to commit
    and then fails inside the learner's grade transaction.
    """
    criterion = CompetencyCriterion.objects.select_related("group__tag__taxonomy", "object_tag").get(pk=criterion_id)
    # The create path only accepts competency tags, and a group's tag never changes, so its taxonomy is set.
    assert criterion.group.tag.taxonomy is not None
    perm_obj = ObjectTagPermissionItem(taxonomy=criterion.group.tag.taxonomy, object_id=criterion.object_tag.object_id)
    # has_perm()'s stub expects a Model, but a rules predicate takes any object.
    if not user.has_perm("oel_tagging.can_tag_object", cast(Any, perm_obj)):
        raise PermissionDenied()
    tag_id = criterion.group.tag_id
    with transaction.atomic():
        # A lock rather than a constraint, because Django cascades deletes in Python and the database can't
        # catch three races that follow. A create that commits a new leaf and criterion between the ancestor
        # walk's emptiness check and the row-collecting SELECT inside ``group.delete()`` would have those rows
        # deleted silently, after the create call already returned success. Two deletes of sibling criteria in
        # one leaf would each still see the other's criterion and leave an empty leaf behind. A create that
        # reuses the same ObjectTag and commits between the "still referenced" check and the ObjectTag delete
        # would have its criterion cascaded away.
        Tag.objects.select_for_update().get(pk=tag_id)
        # Re-read under the Tag lock: a concurrent delete may have won; DoesNotExist propagates to the caller.
        criterion = CompetencyCriterion.objects.select_related("group", "object_tag").get(pk=criterion_id)
        if _criterion_has_learner_progress(criterion_id):
            _archive_criterion(criterion, user)
            return CriterionDeletionResult(id=criterion_id, archived=True)
        group = criterion.group
        object_tag = criterion.object_tag
        _set_history_user(criterion, user)
        criterion.delete()
        # Only this criterion's own ObjectTag is a candidate, and only while no other criterion uses it.
        if not CompetencyCriterion.objects.filter(object_tag_id=object_tag.id).exists():
            ObjectTag.objects.filter(pk=object_tag.id).delete()
        _cascade_remove_from_group(group, user)
        return CriterionDeletionResult(id=criterion_id, archived=False)


def resolve_competency_criteria_group_course(group: CompetencyCriteriaGroup) -> CourseRun:
    """
    Return the CourseRun that scopes the subtree rooted at ``group``.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    That is the course of the nearest group with one, starting at ``group`` itself and walking
    up through its ancestors, so a group may sit at any depth below its course-level group. Costs
    one query per ancestor walked. A caller checks ``oel_tagging.can_tag_object`` against this
    course before removing the subtree.

    Raises ValidationError (keyed "group_id") if ``group`` is a root CompetencyCriteriaGroup,
    whose subtree can span several courses, or if no group in its lineage has a course.
    """
    if group.parent_id is None:
        raise ValidationError({"group_id": _("group_id must not reference a root CompetencyCriteriaGroup.")})

    node: CompetencyCriteriaGroup | None = group
    while node is not None:
        if node.course is not None:
            return node.course
        node = node.parent
    raise ValidationError(
        {"group_id": _("group_id must reference a group under a course-level CompetencyCriteriaGroup.")}
    )


def _competency_criteria_subtree(target: CompetencyCriteriaGroup) -> tuple[set[int], list[CompetencyCriterion]]:
    """
    Return the ids of ``target`` and its descendant groups (found level by level), and the subtree's criteria.

    Must run inside a transaction that already holds the competency's Tag lock, so no tree change
    for that competency can land between this read and the delete or archive that follows it.
    """
    subtree_ids = {target.pk}
    level_ids = [target.pk]
    while level_ids:
        level_ids = list(
            CompetencyCriteriaGroup.objects.filter(parent_id__in=level_ids).values_list("pk", flat=True)
        )
        subtree_ids.update(level_ids)
    criteria = list(CompetencyCriterion.objects.filter(group_id__in=subtree_ids).order_by("pk"))
    return subtree_ids, criteria


def _archive_competency_criteria_subtree(
    target_group: CompetencyCriteriaGroup,
    all_group_ids: set[int],
    criteria: list[CompetencyCriterion],
    user: UserType,
) -> None:
    """Archive every group and criterion in the subtree, then cascade the archive above the target."""
    # The criteria are written without #674's per-criterion cascade: every group in the subtree is archived
    # here, and the one upward walk below covers the target's ancestors. Never touches any ObjectTag.
    for group in CompetencyCriteriaGroup.objects.filter(id__in=all_group_ids, archived=False):
        _archive_group(group, user)
    for criterion in criteria:
        _write_criterion_archived(criterion, user)
    if target_group.parent is not None:
        _cascade_remove_from_group(target_group.parent, user)


def _subtree_has_learner_progress(group_ids: set[int], criterion_ids: list[int]) -> bool:
    """Return whether a learner status row references any group in ``group_ids`` or any of ``criterion_ids``."""
    return (
        StudentCompetencyCriteriaGroupStatus.objects.filter(group_id__in=group_ids).exists()
        or StudentCompetencyCriterionStatus.objects.filter(criterion_id__in=criterion_ids).exists()
    )


def _delete_orphaned_object_tags(candidate_ids: set[int]) -> None:
    """
    Delete each ObjectTag in ``candidate_ids`` that no CompetencyCriterion references, and nothing else.

    ``candidate_ids`` are the ObjectTag ids of the criteria in a deleted group subtree. Must run
    after those criteria are deleted, inside the transaction that holds the competency's Tag lock.
    No other ObjectTag on the object, in any taxonomy, is read or written.
    """
    still_referenced = set(
        CompetencyCriterion.objects.filter(object_tag_id__in=candidate_ids).values_list("object_tag_id", flat=True)
    )
    ObjectTag.objects.filter(pk__in=candidate_ids - still_referenced).delete()


def delete_or_archive_competency_criteria_group(group_id: int, user: UserType) -> GroupDeletionResult:
    """
    Hard-delete the CompetencyCriteriaGroup subtree rooted at `group_id`, or archive it whole.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    ``user`` must hold ``oel_tagging.can_tag_object`` for the course
    :func:`resolve_competency_criteria_group_course` returns for the target group; otherwise
    Django's ``PermissionDenied`` is raised before anything is locked or changed.

    The subtree is the target group plus every descendant group and leaf CompetencyCriterion.
    Hard-deleted if no learner status exists anywhere in it; archived in place, all-or-nothing,
    if any does. Either way, each ancestor left with no live children is then hard-deleted or
    archived, as for a criterion delete (ADR-0002 Decision 2).

    On a hard delete, only the ObjectTags of the subtree's criteria that no other criterion
    references are removed; no other tag on the object is touched.

    The whole operation runs in one transaction that locks only the competency's Tag, the same lock
    a create or a criterion delete for that competency takes first, so those wait until this call
    commits.

    A learner status written concurrently, after this call's progress check, makes the call
    archive the subtree instead of deleting it, provided a second progress check finds a status on
    the subtree. If it finds none, the failure came from outside the subtree, such as a status that
    reached an ancestor group during the cascade, and the original ``IntegrityError`` is re-raised
    with nothing changed. A caller may retry: the retry re-checks, then deletes or archives.

    Raises CompetencyCriteriaGroup.DoesNotExist if no such group exists (including one deleted
    concurrently), Tag.DoesNotExist if its competency tag was deleted concurrently, PermissionDenied
    if ``user`` may not tag the course's object, ValidationError (keyed "group_id") if `group_id`
    names a root CompetencyCriteriaGroup or a group with no course-level group in its lineage, and
    ``IntegrityError`` when the delete races with a write outside the target subtree.
    """
    group = CompetencyCriteriaGroup.objects.select_related("tag__taxonomy").get(pk=group_id)
    course = resolve_competency_criteria_group_course(group)
    # A group's tag is always a competency tag, so its taxonomy is always set.
    assert group.tag.taxonomy is not None
    perm_obj = ObjectTagPermissionItem(taxonomy=group.tag.taxonomy, object_id=str(course.course_key))
    # The rules-based permission takes an ObjectTagPermissionItem, but django-stubs types the
    # has_perm object argument as a Model.
    if not user.has_perm("oel_tagging.can_tag_object", cast(Any, perm_obj)):
        raise PermissionDenied()
    tag_id = group.tag_id
    with transaction.atomic():
        Tag.objects.select_for_update().get(pk=tag_id)
        # Read under the Tag lock: a concurrent delete may have won, and DoesNotExist propagates.
        target_group = CompetencyCriteriaGroup.objects.get(pk=group_id)
        subtree_ids, criteria = _competency_criteria_subtree(target_group)
        criterion_ids = [criterion.pk for criterion in criteria]
        archived = _subtree_has_learner_progress(subtree_ids, criterion_ids)
        if not archived:
            try:
                # Savepoint: a learner status committed after the progress check above makes the delete
                # fail on its PROTECT foreign key, and only this block must roll back.
                with transaction.atomic():
                    parent_group = target_group.parent
                    target_group.delete()
                    _delete_orphaned_object_tags({criterion.object_tag_id for criterion in criteria})
                    if parent_group is not None:
                        _cascade_remove_from_group(parent_group, user)
            except IntegrityError:
                # Archive only if the failure traces to the subtree's own progress. A status on an ancestor
                # group, or any other integrity failure, would not be fixed by archiving the subtree. The
                # re-query is needed because the status may have landed after the pre-check above.
                if not _subtree_has_learner_progress(subtree_ids, criterion_ids):
                    raise
                archived = True
                # The rolled-back block may have cleared the pk of in-memory instances it had already deleted.
                target_group = CompetencyCriteriaGroup.objects.get(pk=group_id)
        if archived:
            _archive_competency_criteria_subtree(target_group, subtree_ids, criteria, user)
        return GroupDeletionResult(
            id=group_id,
            archived=archived,
            cascaded_group_count=len(subtree_ids) - 1,
            cascaded_criteria_count=len(criteria),
        )
