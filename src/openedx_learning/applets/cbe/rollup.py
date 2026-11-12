"""
Roll up a learner's competency statuses from the criteria of newly graded objects to the top of the hierarchy.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone

from django.db import connection
from django.db.models import Max
from django.db.transaction import TransactionManagementError

from openedx_tagging.models import Tag
from openedx_tagging.models.base import TAXONOMY_MAX_DEPTH

from .models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    LogicOperator,
    MasteryStatus,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriterionStatus,
    StudentCompetencyStatus,
)
from .statuses import raise_status

log = logging.getLogger(__name__)

__all__ = ["combine_children", "combine_parent_competency_status", "roll_up_competency_statuses"]


def _combine_and(child_statuses: Sequence[MasteryStatus | None]) -> MasteryStatus:
    """Return an AND group's status: demonstrated when every child is, not demonstrated when any child is."""
    if all(status == MasteryStatus.DEMONSTRATED for status in child_statuses):
        return MasteryStatus.DEMONSTRATED
    if MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED in child_statuses:
        return MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED
    return MasteryStatus.PARTIALLY_ATTEMPTED


def _combine_or(child_statuses: Sequence[MasteryStatus | None]) -> MasteryStatus:
    """Return an OR group's status: demonstrated when any child is, not demonstrated when every child is."""
    if MasteryStatus.DEMONSTRATED in child_statuses:
        return MasteryStatus.DEMONSTRATED
    if all(status == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED for status in child_statuses):
        return MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED
    return MasteryStatus.PARTIALLY_ATTEMPTED


def combine_children(
    logic_operator: str | None, child_statuses: Sequence[MasteryStatus | None],
) -> MasteryStatus | None:
    """
    Return a criteria group's status from its children's, or None when no child has a status.

    ``None`` entries are children with no row. A null ``logic_operator`` is treated as OR.
    """
    if all(status is None for status in child_statuses):
        return None
    if logic_operator == LogicOperator.AND:
        return _combine_and(child_statuses)
    return _combine_or(child_statuses)


def combine_parent_competency_status(
    own: MasteryStatus | None, from_sub_competencies: MasteryStatus | None,
) -> MasteryStatus | None:
    """
    Return a parent competency's status: the better of its own criteria result and its sub-competencies' result.

    A parent is demonstrated when either its own criteria are demonstrated or every direct sub-competency is.
    Both inputs are only DEMONSTRATED, PARTIALLY_ATTEMPTED or None, so the better one is the higher value.
    """
    return max((status for status in (own, from_sub_competencies) if status is not None), default=None)


def _child_groups(group_ids: set[int]) -> list[tuple[int, int]]:
    """Return (child group id, parent group id) for every non-archived child group of ``group_ids``."""
    return list(
        CompetencyCriteriaGroup.objects.filter(parent_id__in=group_ids, archived=False).values_list("pk", "parent_id")
    )


def _child_criteria(group_ids: set[int]) -> list[tuple[int, int]]:
    """Return (criterion id, group id) for every non-archived criterion directly under ``group_ids``."""
    return list(
        CompetencyCriterion.objects.filter(group_id__in=group_ids, archived=False).values_list("pk", "group_id")
    )


def _to_status(value: int | None) -> MasteryStatus | None:
    return None if value is None else MasteryStatus(value)


def _stored_group_status(user_id: int, group_id: int) -> MasteryStatus | None:
    """Return the status stored for ``group_id``, or None when the learner has no row for it."""
    return _to_status(
        StudentCompetencyCriteriaGroupStatus.objects.filter(user_id=user_id, group_id=group_id)
        .values_list("status_id", flat=True)
        .first()
    )


def _children_statuses(user_id: int, group_ids: set[int]) -> dict[int, list[MasteryStatus | None]]:
    """Return, per group, one status (or None for no row) for each of its non-archived child groups and criteria."""
    child_groups = _child_groups(group_ids)
    child_criteria = _child_criteria(group_ids)
    group_status = dict(
        StudentCompetencyCriteriaGroupStatus.objects.filter(
            user_id=user_id, group_id__in=[child_id for child_id, _ in child_groups],
        ).values_list("group_id", "status_id")
    )
    criterion_status = dict(
        StudentCompetencyCriterionStatus.objects.filter(
            user_id=user_id, criterion_id__in=[child_id for child_id, _ in child_criteria],
        ).values_list("criterion_id", "status_id")
    )
    statuses: dict[int, list[MasteryStatus | None]] = {group_id: [] for group_id in group_ids}
    for child_id, parent_id in child_groups:
        statuses[parent_id].append(_to_status(group_status.get(child_id)))
    for child_id, parent_id in child_criteria:
        statuses[parent_id].append(_to_status(criterion_status.get(child_id)))
    return statuses


def _competency_level(status: MasteryStatus) -> MasteryStatus:
    """Map a group status to a competency status, which never holds ATTEMPTED_NOT_DEMONSTRATED."""
    if status == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED:
        return MasteryStatus.PARTIALLY_ATTEMPTED
    return status


def _raise_group_status(user_id: int, group_id: int, status: MasteryStatus | None, now: datetime) -> None:
    """Raise the learner's stored status for the group to ``status``; a None status writes nothing."""
    if status is None:
        return
    raise_status(
        StudentCompetencyCriteriaGroupStatus.objects,
        lookup={"user_id": user_id, "group_id": group_id},
        status=status,
        now=now,
    )


def _raise_competency_status(user_id: int, tag_id: int, status: MasteryStatus | None, now: datetime) -> None:
    """Raise the learner's stored status for the competency to ``status``; a None status writes nothing."""
    if status is None:
        return
    raise_status(
        StudentCompetencyStatus.objects,
        lookup={"user_id": user_id, "tag_id": tag_id},
        status=status,
        now=now,
    )


def _raise_competency_from_root(user_id: int, root: CompetencyCriteriaGroup, now: datetime) -> None:
    """Raise the competency's status from the status stored for its root group; no stored status writes nothing."""
    status = _stored_group_status(user_id, root.pk)
    if status is not None:
        _raise_competency_status(user_id, root.tag_id, _competency_level(status), now)


def _roll_up_group(
    user_id: int, group: CompetencyCriteriaGroup, child_statuses: list[MasteryStatus | None], now: datetime,
) -> None:
    """
    Recompute one group, and for a root group raise its competency's status from the root's stored status.

    An archived group is not recomputed.
    """
    if not group.archived:
        _raise_group_status(user_id, group.pk, combine_children(group.logic_operator, child_statuses), now)
    # The stored status can be higher than this recomputation, and the competency has to match it.
    if group.parent_id is None:
        _raise_competency_from_root(user_id, group, now)


def _roll_up_group_level(user_id: int, group_ids: set[int], now: datetime) -> list[CompetencyCriteriaGroup]:
    """Roll up each group in ``group_ids`` from its children's statuses, and return the groups."""
    child_statuses = _children_statuses(user_id, group_ids)
    groups = list(CompetencyCriteriaGroup.objects.filter(pk__in=group_ids))
    for group in groups:
        _roll_up_group(user_id, group, child_statuses[group.pk], now)
    return groups


def _competency_tag_ids(groups: list[CompetencyCriteriaGroup]) -> set[int]:
    """Return the ids of the competency tags that the root groups in ``groups`` belong to."""
    return {group.tag_id for group in groups if group.parent_id is None}


def _parent_group_ids(groups: list[CompetencyCriteriaGroup]) -> set[int]:
    """Return the ids of the parent groups of the non-root groups in ``groups``."""
    return {group.parent_id for group in groups if group.parent_id is not None}


def _roll_up_groups(user_id: int, group_ids: set[int], now: datetime) -> set[int]:
    """
    Recompute each group in ``group_ids`` and then its parent, one level at a time, up to the roots.

    Returns the ids of the competency tags whose root group was reached.
    """
    competency_tag_ids: set[int] = set()
    level = set(group_ids)
    while level:
        groups = _roll_up_group_level(user_id, level, now)
        competency_tag_ids |= _competency_tag_ids(groups)
        level = _parent_group_ids(groups)
    return competency_tag_ids


def _own_criteria_statuses(user_id: int, tag_ids: set[int]) -> dict[int, MasteryStatus]:
    """Return each tag's own criteria result: its best root-group status, for tags that have an attempted root."""
    # Archived roots are counted because the walk raises a competency's row from an archived root's stored status.
    rows = (
        StudentCompetencyCriteriaGroupStatus.objects.filter(
            user_id=user_id, group__tag_id__in=tag_ids, group__parent__isnull=True,
        )
        .values("group__tag_id")
        .annotate(best=Max("status_id"))
        .values_list("group__tag_id", "best")
    )
    return {tag_id: _competency_level(MasteryStatus(best)) for tag_id, best in rows}


def _sub_competency_status(child_ids: list[int], competency_status: dict[int, int]) -> MasteryStatus | None:
    """Return DEMONSTRATED if every sub-competency is, PARTIALLY_ATTEMPTED if any has a status, else None."""
    statuses = [competency_status[child_id] for child_id in child_ids if child_id in competency_status]
    if not statuses:
        return None
    if len(statuses) == len(child_ids) and all(status == MasteryStatus.DEMONSTRATED for status in statuses):
        return MasteryStatus.DEMONSTRATED
    return MasteryStatus.PARTIALLY_ATTEMPTED


def _parent_ids(tag_ids: set[int]) -> set[int]:
    """Return the ids of the parents of the tags in ``tag_ids``."""
    return set(Tag.objects.filter(pk__in=tag_ids, parent_id__isnull=False).values_list("parent_id", flat=True))


def _child_ids_by_parent(parent_ids: set[int]) -> dict[int, list[int]]:
    """Return, per parent tag, the ids of its direct child tags."""
    children: dict[int, list[int]] = {}
    for child_id, parent_id in Tag.objects.filter(parent_id__in=parent_ids).values_list("pk", "parent_id"):
        children.setdefault(parent_id, []).append(child_id)
    return children


def _child_competency_statuses(user_id: int, parent_ids: set[int]) -> dict[int, int]:
    """Return the learner's stored competency status for each child tag of ``parent_ids`` that has one."""
    return dict(
        StudentCompetencyStatus.objects.filter(user_id=user_id, tag__parent_id__in=parent_ids).values_list(
            "tag_id", "status_id",
        )
    )


def _roll_up_parent_level(user_id: int, parent_ids: set[int], now: datetime) -> None:
    """Recompute each competency in ``parent_ids`` from its own criteria and its direct sub-competencies."""
    child_ids = _child_ids_by_parent(parent_ids)
    child_statuses = _child_competency_statuses(user_id, parent_ids)
    own_statuses = _own_criteria_statuses(user_id, parent_ids)
    for parent_id in sorted(parent_ids):
        status = combine_parent_competency_status(
            own_statuses.get(parent_id), _sub_competency_status(child_ids.get(parent_id, []), child_statuses),
        )
        _raise_competency_status(user_id, parent_id, status, now)


def _roll_up_parent_tags(user_id: int, tag_ids: set[int], now: datetime) -> None:
    """Recompute the parent of each tag in ``tag_ids``, then its parent, one level at a time, up to the top."""
    level = set(tag_ids)
    # The depth cap bounds the walk to a few iterations, and a tag's parents stay in its own taxonomy.
    for _ in range(TAXONOMY_MAX_DEPTH):
        level = _parent_ids(level)
        if not level:
            return
        _roll_up_parent_level(user_id, level, now)
    # Tag.save() enforces the cap, so only a tree built around it can still have parents here.
    pending = _parent_ids(level)
    if pending:
        log.error(
            "Competency rollup for user %s stopped at the taxonomy depth cap; parent tags %s were not recomputed",
            user_id,
            sorted(pending),
        )


def roll_up_competency_statuses(*, user_id: int, object_ids: Sequence[str]) -> None:
    """
    Recompute the learner's statuses above the criteria attached to ``object_ids``, up to the top of the framework.

    The caller must not be inside a transaction, because each level has to commit before the next is read.
    Repeated calls with the same arguments are safe, since every write only ever raises a stored status.
    Archived groups and criteria are left out of the combination, and a status already stored against them is kept.

    :raises TransactionManagementError: if called inside an atomic block.
    """
    if connection.in_atomic_block:
        raise TransactionManagementError(
            "roll_up_competency_statuses() must run outside any transaction: "
            "each level commits before the next is read."
        )
    if not object_ids:
        return
    # Not filtered on archived: naming an archived criterion's object recomputes its group from non-archived children.
    group_ids = set(
        CompetencyCriterion.objects.filter(
            object_tag__object_id__in=list(set(object_ids)),
            object_tag__tag__taxonomy__competencytaxonomy__isnull=False,
        ).values_list("group_id", flat=True)
    )
    if not group_ids:
        return
    now = datetime.now(tz=timezone.utc)
    _roll_up_parent_tags(user_id, _roll_up_groups(user_id, group_ids, now), now)
