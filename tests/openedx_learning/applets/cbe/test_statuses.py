"""
Tests for `raise_status`, the monotone write shared by the learner status tables.
"""
import logging
from datetime import datetime, timezone

import pytest
from django.db.models.signals import post_save

from openedx_learning.applets.cbe import statuses
from openedx_learning.applets.cbe.statuses import raise_status
from openedx_learning.models import MasteryStatus, StudentCompetencyCriteriaGroupStatus, StudentCompetencyStatus

pytestmark = pytest.mark.django_db

FIRST = datetime(2026, 1, 1, tzinfo=timezone.utc)
SECOND = datetime(2026, 2, 1, tzinfo=timezone.utc)


def _write(user, tag, status: MasteryStatus, now: datetime) -> int:
    return raise_status(
        StudentCompetencyStatus.objects, lookup={"user_id": user.id, "tag_id": tag.id}, status=status, now=now,
    )


def test_creates_a_missing_row(user, tag) -> None:
    """A first write creates the row with created equal to modified."""
    assert _write(user, tag, MasteryStatus.PARTIALLY_ATTEMPTED, FIRST) == 1

    row = StudentCompetencyStatus.objects.get(user=user, tag=tag)
    assert row.status_id == MasteryStatus.PARTIALLY_ATTEMPTED
    assert (row.created, row.modified) == (FIRST, FIRST)


def test_raises_a_lower_stored_status(user, tag) -> None:
    """A higher status replaces a lower one and moves modified but not created."""
    _write(user, tag, MasteryStatus.PARTIALLY_ATTEMPTED, FIRST)

    assert _write(user, tag, MasteryStatus.DEMONSTRATED, SECOND) == 1

    row = StudentCompetencyStatus.objects.get(user=user, tag=tag)
    assert row.status_id == MasteryStatus.DEMONSTRATED
    assert (row.created, row.modified) == (FIRST, SECOND)


def test_never_lowers_a_stored_status(user, tag) -> None:
    """A lower status is discarded and the row is left exactly as it was."""
    _write(user, tag, MasteryStatus.DEMONSTRATED, FIRST)

    assert _write(user, tag, MasteryStatus.PARTIALLY_ATTEMPTED, SECOND) == 0

    row = StudentCompetencyStatus.objects.get(user=user, tag=tag)
    assert row.status_id == MasteryStatus.DEMONSTRATED
    assert row.modified == FIRST


def test_equal_status_changes_nothing(user, tag) -> None:
    """Writing the stored value again is a no-op, so modified keeps its old value."""
    _write(user, tag, MasteryStatus.DEMONSTRATED, FIRST)

    assert _write(user, tag, MasteryStatus.DEMONSTRATED, SECOND) == 0

    assert StudentCompetencyStatus.objects.get(user=user, tag=tag).modified == FIRST


def test_creating_a_row_is_logged(user, tag, caplog: pytest.LogCaptureFixture) -> None:
    """A created row is logged with its model, lookup, and new status."""
    with caplog.at_level(logging.INFO, logger=statuses.__name__):
        _write(user, tag, MasteryStatus.PARTIALLY_ATTEMPTED, FIRST)

    assert [record.getMessage() for record in caplog.records] == [
        f"Created StudentCompetencyStatus {{'user_id': {user.id}, 'tag_id': {tag.id}}}: status None -> 20",
    ]


def test_raising_a_status_is_logged(user, tag, caplog: pytest.LogCaptureFixture) -> None:
    """A raised row is logged with the status it went from and to."""
    _write(user, tag, MasteryStatus.PARTIALLY_ATTEMPTED, FIRST)

    with caplog.at_level(logging.INFO, logger=statuses.__name__):
        _write(user, tag, MasteryStatus.DEMONSTRATED, SECOND)

    assert [record.getMessage() for record in caplog.records] == [
        f"Raised StudentCompetencyStatus {{'user_id': {user.id}, 'tag_id': {tag.id}}}: status 20 -> 30",
    ]


def test_an_unchanged_status_is_not_logged(user, tag, caplog: pytest.LogCaptureFixture) -> None:
    """A write that leaves the stored status as it was logs nothing."""
    _write(user, tag, MasteryStatus.DEMONSTRATED, FIRST)

    with caplog.at_level(logging.INFO, logger=statuses.__name__):
        _write(user, tag, MasteryStatus.PARTIALLY_ATTEMPTED, SECOND)
        _write(user, tag, MasteryStatus.DEMONSTRATED, SECOND)

    assert not caplog.records


@pytest.fixture(name="saves")
def fixture_saves():
    """Record every post_save sent for StudentCompetencyStatus as (created, status_id, modified)."""
    received = []

    def receiver(sender, instance, created, **kwargs):  # pylint: disable=unused-argument
        received.append((created, instance.status_id, instance.modified))

    post_save.connect(receiver, sender=StudentCompetencyStatus, weak=False)
    yield received
    post_save.disconnect(receiver, sender=StudentCompetencyStatus)


def test_creating_a_row_sends_post_save(user, tag, saves) -> None:
    """A created row sends post_save with created=True."""
    _write(user, tag, MasteryStatus.PARTIALLY_ATTEMPTED, FIRST)

    assert saves == [(True, MasteryStatus.PARTIALLY_ATTEMPTED, FIRST)]


def test_raising_a_status_sends_post_save(user, tag, saves) -> None:
    """A raise is a bulk UPDATE, so raise_status sends post_save itself, with the raised values."""
    _write(user, tag, MasteryStatus.PARTIALLY_ATTEMPTED, FIRST)

    _write(user, tag, MasteryStatus.DEMONSTRATED, SECOND)

    assert saves[1:] == [(False, MasteryStatus.DEMONSTRATED, SECOND)]


def test_an_unchanged_status_sends_no_post_save(user, tag, saves) -> None:
    """A write that leaves the stored status as it was sends nothing."""
    _write(user, tag, MasteryStatus.DEMONSTRATED, FIRST)

    _write(user, tag, MasteryStatus.PARTIALLY_ATTEMPTED, SECOND)
    _write(user, tag, MasteryStatus.DEMONSTRATED, SECOND)

    assert len(saves) == 1


def test_raising_a_group_status_sends_post_save(user, group) -> None:
    """Group status raises are bulk UPDATEs too, so they send post_save like competency status raises."""
    received = []

    def receiver(sender, instance, created, **kwargs):  # pylint: disable=unused-argument
        received.append((created, instance.status_id, instance.modified))

    post_save.connect(receiver, sender=StudentCompetencyCriteriaGroupStatus, weak=False)
    try:
        lookup = {"user_id": user.id, "group_id": group.id}
        raise_status(
            StudentCompetencyCriteriaGroupStatus.objects, lookup=lookup,
            status=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED, now=FIRST,
        )
        raise_status(
            StudentCompetencyCriteriaGroupStatus.objects, lookup=lookup, status=MasteryStatus.DEMONSTRATED, now=SECOND,
        )
    finally:
        post_save.disconnect(receiver, sender=StudentCompetencyCriteriaGroupStatus)

    assert received == [
        (True, MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED, FIRST),
        (False, MasteryStatus.DEMONSTRATED, SECOND),
    ]
