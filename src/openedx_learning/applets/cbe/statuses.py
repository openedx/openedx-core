"""
The monotone write shared by the learner status tables: leaf, group, and competency.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TypeVar

from django.db import models

from .models import (
    MasteryStatus,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriterionStatus,
    StudentCompetencyStatus,
)

log = logging.getLogger(__name__)

__all__ = ["raise_status"]

_ModelT = TypeVar(
    "_ModelT", StudentCompetencyCriterionStatus, StudentCompetencyCriteriaGroupStatus, StudentCompetencyStatus,
)


def raise_status(
    manager: models.Manager[_ModelT], *, lookup: dict[str, int], status: MasteryStatus, now: datetime,
) -> int:
    """
    Create or raise the learner status row identified by ``lookup``; never lowers a stored status.

    Returns 1 if a row was created or raised, 0 if the stored status was already at least ``status``.
    """
    row, created = manager.get_or_create(**lookup, defaults={"status_id": status, "created": now, "modified": now})
    if created:
        log.info("Created %s %s: status %s -> %s", manager.model.__name__, lookup, None, status)
        return 1
    # A single conditional UPDATE, never read-then-write: a stale read can only compute a value that is too low.
    changed = manager.filter(pk=row.pk, status_id__lt=status).update(status_id=status, modified=now)
    if changed:
        log.info("Raised %s %s: status %s -> %s", manager.model.__name__, lookup, row.status_id, status)
    return changed
