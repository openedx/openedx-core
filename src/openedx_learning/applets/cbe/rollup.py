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


def combine_children(
    logic_operator: str | None, child_statuses: Sequence[MasteryStatus | None],
) -> MasteryStatus | None:
    """
    Return a criteria group's status from its children's, or None when no child has a status.

    ``None`` entries are children with no row. A null ``logic_operator`` is treated as OR.
    """
    attempted = {status for status in child_statuses if status is not None}
    if not attempted:
        return None
    every_child_attempted = None not in child_statuses
    if logic_operator == LogicOperator.AND:
        if every_child_attempted and attempted == {MasteryStatus.DEMONSTRATED}:
            return MasteryStatus.DEMONSTRATED
        if MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED in attempted:
            return MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED
    else:  # OR, or a null operator, which behaves as OR.
        if MasteryStatus.DEMONSTRATED in attempted:
            return MasteryStatus.DEMONSTRATED
        if every_child_attempted and attempted == {MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED}:
            return MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED
    # Some child has a status, but neither rule above is settled: the learner is in progress.
    return MasteryStatus.PARTIALLY_ATTEMPTED


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
    """Return (child group id, parent group id) for every child group of ``group_ids``."""
    return list(CompetencyCriteriaGroup.objects.filter(parent_id__in=group_ids).values_list("pk", "parent_id"))


def _child_criteria(group_ids: set[int]) -> list[tuple[int, int]]:
    """Return (criterion id, group id) for every criterion directly under ``group_ids``."""
    return list(CompetencyCriterion.objects.filter(group_id__in=group_ids).values_list("pk", "group_id"))


def _to_status(value: int | None) -> MasteryStatus | None:
    return None if value is None else MasteryStatus(value)


def _children_statuses(user_id: int, group_ids: set[int]) -> dict[int, list[MasteryStatus | None]]:
    """Return, per group, one status (or None for no row) for each of its child groups and criteria."""
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


def _roll_up_groups(user_id: int, group_ids: set[int], now: datetime) -> set[int]:
    """
    Recompute each group in ``group_ids`` and then its parent, one level at a time, up to the roots.

    Returns the ids of the competency tags whose root group was reached.
    """
    competency_tag_ids: set[int] = set()
    frontier = set(group_ids)
    while frontier:
        statuses = _children_statuses(user_id, frontier)
        next_frontier: set[int] = set()
        for group in CompetencyCriteriaGroup.objects.filter(pk__in=frontier):
            status = combine_children(group.logic_operator, statuses[group.pk])
            if status is not None:
                raise_status(
                    StudentCompetencyCriteriaGroupStatus.objects,
                    lookup={"user_id": user_id, "group_id": group.pk},
                    status=status,
                    now=now,
                )
            if group.parent_id is not None:
                next_frontier.add(group.parent_id)
                continue
            competency_tag_ids.add(group.tag_id)
            if status is not None:
                raise_status(
                    StudentCompetencyStatus.objects,
                    lookup={"user_id": user_id, "tag_id": group.tag_id},
                    status=_competency_level(status),
                    now=now,
                )
        frontier = next_frontier
    return competency_tag_ids


def _own_criteria_statuses(user_id: int, tag_ids: set[int]) -> dict[int, MasteryStatus]:
    """Return each tag's own criteria result: its best root-group status, for tags that have an attempted root."""
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


def _roll_up_parent_tags(user_id: int, tag_ids: set[int], now: datetime) -> None:
    """Recompute the parent of each tag in ``tag_ids``, then its parent, one level at a time, up to the top."""
    frontier = set(tag_ids)
    # The depth cap bounds the walk to a few iterations, and a tag's parents stay in its own taxonomy.
    for _ in range(TAXONOMY_MAX_DEPTH):
        parent_ids = _parent_ids(frontier)
        if not parent_ids:
            return
        parents = list(Tag.objects.filter(pk__in=parent_ids))
        children_by_parent: dict[int, list[int]] = {}
        for child_id, parent_id in Tag.objects.filter(parent_id__in=parent_ids).values_list("pk", "parent_id"):
            children_by_parent.setdefault(parent_id, []).append(child_id)
        competency_status = dict(
            StudentCompetencyStatus.objects.filter(user_id=user_id, tag__parent_id__in=parent_ids).values_list(
                "tag_id", "status_id",
            )
        )
        own = _own_criteria_statuses(user_id, parent_ids)
        for parent in parents:
            child_ids = children_by_parent.get(parent.pk, [])
            status = combine_parent_competency_status(
                own.get(parent.pk), _sub_competency_status(child_ids, competency_status),
            )
            if status is not None:
                raise_status(
                    StudentCompetencyStatus.objects,
                    lookup={"user_id": user_id, "tag_id": parent.pk},
                    status=status,
                    now=now,
                )
        frontier = parent_ids
    # Tag.save() enforces the cap, so only a tree built around it can still have parents here.
    pending = _parent_ids(frontier)
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

    :raises TransactionManagementError: if called inside an atomic block.
    """
    if connection.in_atomic_block:
        raise TransactionManagementError(
            "roll_up_competency_statuses() must run outside any transaction: "
            "each level commits before the next is read."
        )
    if not object_ids:
        return
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
