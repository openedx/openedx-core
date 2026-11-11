"""
The monotone write shared by the learner status tables: leaf, group, and competency.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TypeVar

from django.db import models, transaction
from django.db.models.signals import post_save

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

    ``post_save`` fires for a created row (from ``get_or_create``) and for a raised one (sent here, because the raise
    is a bulk ``UPDATE``), so listeners such as analytics sinks see every change. It does not fire when nothing changed.

    Other code that changes a stored status, such as the staff override and recalculation planned in ADR-0004, must
    send ``post_save`` too.
    """
    row, created = manager.get_or_create(**lookup, defaults={"status_id": status, "created": now, "modified": now})
    if created:
        log.info("Created %s %s: status %s -> %s", manager.model.__name__, lookup, None, status)
        return 1
    # Commit the raise with its post_save, so a failing receiver rolls it back and a retry resends.
    with transaction.atomic(using=manager.db, savepoint=False):
        # A single conditional UPDATE, never read-then-write: a stale read can only compute a value that is too low.
        changed = manager.filter(pk=row.pk, status_id__lt=status).update(status_id=status, modified=now)
        if changed:
            log.info("Raised %s %s: status %s -> %s", manager.model.__name__, lookup, row.status_id, status)
            row.status_id, row.modified = status, now
            post_save.send(
                sender=manager.model,
                instance=row,
                created=False,
                update_fields=frozenset({"status_id", "modified"}),
                raw=False,
                using=manager.db,
            )
    return changed
