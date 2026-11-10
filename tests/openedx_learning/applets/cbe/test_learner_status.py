"""
Tests for the learner mastery status models: MasteryStatus, CompetencyMasteryStatus, and
StudentCompetencyStatus.

The deletion tests below are part of this ticket's headline responsibility, not an afterthought:
`StudentCompetencyStatus.tag` (`on_delete=models.PROTECT`) is what stops Django's collector from
walking a `Tag` delete away for free, and `StudentCompetencyStatus.status`
(`on_delete=models.PROTECT`) does the same for the lookup table it points at.
"""
from datetime import datetime, timezone

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from openedx_learning.models import CompetencyMasteryStatus, CompetencyTaxonomy, MasteryStatus, StudentCompetencyStatus
from openedx_tagging.models import Tag

pytestmark = pytest.mark.django_db


@pytest.fixture(name="other_tag")
def _other_tag(competency_taxonomy: CompetencyTaxonomy) -> Tag:
    """A second Tag, from the same taxonomy as `tag`, for a second competency-level status."""
    return Tag.objects.create(taxonomy=competency_taxonomy, value="Decimals")


# ==============================================================================================
# The lookup table and its rank ordering.
# ==============================================================================================


def test_seed_produces_three_rows_in_rank_order() -> None:
    """
    The seed_competency_mastery_statuses data migration creates exactly the three
    CompetencyMasteryStatus rows, with the pinned ids from MasteryStatus, and ids ascend in
    rank (lowest to highest mastery).
    """
    rows = list(CompetencyMasteryStatus.objects.order_by("id"))
    assert [row.id for row in rows] == [
        MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED,
        MasteryStatus.PARTIALLY_ATTEMPTED,
        MasteryStatus.DEMONSTRATED,
    ]
    assert [row.status for row in rows] == [
        MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED.label,
        MasteryStatus.PARTIALLY_ATTEMPTED.label,
        MasteryStatus.DEMONSTRATED.label,
    ]


def test_status_is_unique_on_lookup_table() -> None:
    """
    CompetencyMasteryStatus.status is unique: a second row with a status string
    that already exists raises IntegrityError.
    """
    with pytest.raises(IntegrityError), transaction.atomic():
        CompetencyMasteryStatus.objects.create(status=MasteryStatus.DEMONSTRATED.label)


# ==============================================================================================
# The monotone conditional-update comparison, on StudentCompetencyStatus.
# ==============================================================================================


def test_conditional_raise_is_a_single_no_op_or_effective_update(user, tag: Tag, now: datetime) -> None:
    """
    The monotone comparison works in a single statement on StudentCompetencyStatus: a
    conditional UPDATE guarded by status_id__lt is a no-op against an already-higher status,
    and is the one write that takes effect when the stored status is lower.
    """
    scs = StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    # Already at the top rank: an attempted raise to a lower/equal rank changes nothing.
    changed = StudentCompetencyStatus.objects.filter(
        user=user, tag=tag, status_id__lt=MasteryStatus.PARTIALLY_ATTEMPTED,
    ).update(status_id=MasteryStatus.PARTIALLY_ATTEMPTED, modified=now)
    assert changed == 0
    scs.refresh_from_db()
    assert scs.status_id == MasteryStatus.DEMONSTRATED

    # Lower the stored status, then confirm the same shape raises it exactly once.
    StudentCompetencyStatus.objects.filter(pk=scs.pk).update(status_id=MasteryStatus.PARTIALLY_ATTEMPTED)
    changed = StudentCompetencyStatus.objects.filter(
        user=user, tag=tag, status_id__lt=MasteryStatus.DEMONSTRATED,
    ).update(status_id=MasteryStatus.DEMONSTRATED, modified=now)
    assert changed == 1
    scs.refresh_from_db()
    assert scs.status_id == MasteryStatus.DEMONSTRATED


# ==============================================================================================
# The allow-list check constraint on StudentCompetencyStatus.
# ==============================================================================================


def test_attempted_not_demonstrated_rejected_on_create(user, tag: Tag, now: datetime) -> None:
    """
    The allow-list constraint rejects AttemptedNotDemonstrated on a direct create().
    """
    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyStatus.objects.create(
            user=user,
            tag=tag,
            status_id=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED,
            created=now,
            modified=now,
        )


def test_attempted_not_demonstrated_rejected_on_bulk_create(user, tag: Tag, now: datetime) -> None:
    """
    The allow-list constraint rejects AttemptedNotDemonstrated on bulk_create(), which
    bypasses Model.save() and full_clean(), so Python-side validation is skipped and only
    the database-level check constraint still catches this.
    """
    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyStatus.objects.bulk_create([
            StudentCompetencyStatus(
                user=user,
                tag=tag,
                status_id=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED,
                created=now,
                modified=now,
            )
        ])


def test_attempted_not_demonstrated_rejected_on_queryset_update(user, tag: Tag, now: datetime) -> None:
    """
    The allow-list constraint also rejects AttemptedNotDemonstrated on QuerySet.update(),
    the same write path the conditional raise above uses.
    """
    scs = StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.PARTIALLY_ATTEMPTED, created=now, modified=now,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyStatus.objects.filter(pk=scs.pk).update(
            status_id=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED,
        )


@pytest.mark.parametrize("status_id", [-1, 15, 25, 40, 30000])
def test_status_outside_the_allow_list_rejected(user, tag: Tag, now: datetime, status_id: int) -> None:
    """
    A status id that is not in the allow-list is rejected. The constraint is an allow-list
    rather than a negation of AttemptedNotDemonstrated, so a value nobody has defined yet is
    refused by default instead of silently accepted. A lookup row is created for the id first,
    so the foreign key is satisfied and only the allow-list constraint can reject the write,
    on every database backend.
    """
    CompetencyMasteryStatus.objects.create(id=status_id, status=f"Undefined{status_id}")

    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyStatus.objects.create(
            user=user, tag=tag, status_id=status_id, created=now, modified=now,
        )


def test_demonstrated_and_partially_attempted_both_accepted(
    user, tag: Tag, other_tag: Tag, now: datetime,
) -> None:
    """
    Both statuses the allow-list permits, Demonstrated and PartiallyAttempted, are
    accepted on create().
    """
    demonstrated = StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    partially_attempted = StudentCompetencyStatus.objects.create(
        user=user, tag=other_tag, status_id=MasteryStatus.PARTIALLY_ATTEMPTED, created=now, modified=now,
    )
    assert demonstrated.status_id == MasteryStatus.DEMONSTRATED
    assert partially_attempted.status_id == MasteryStatus.PARTIALLY_ATTEMPTED


# ==============================================================================================
# One row per learner and tag (ADR-0002 Decision 5 index 8).
# ==============================================================================================


def test_one_row_per_user_and_tag_but_multiple_tags_per_user(
    user, tag: Tag, other_tag: Tag, now: datetime,
) -> None:
    """
    The (user, tag) unique constraint rejects a second row for the same pair, but
    the same learner may hold a status for a different tag.
    """
    StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.PARTIALLY_ATTEMPTED, created=now, modified=now,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyStatus.objects.create(
            user=user, tag=tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
        )

    # A different tag for the same user is a different (user, tag) pair, so it's allowed.
    other = StudentCompetencyStatus.objects.create(
        user=user, tag=other_tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    assert other.tag_id == other_tag.pk


# ==============================================================================================
# created/modified: caller-supplied UTC datetimes, not automatic.
# ==============================================================================================


def test_created_and_modified_are_required_and_must_be_utc(user, tag: Tag, now: datetime) -> None:
    """
    created and modified are caller-supplied, not automatic: omitting either on create()
    raises IntegrityError (NOT NULL, since there is no auto_now/auto_now_add default), and
    passing a naive (non-UTC) datetime fails full_clean() with ValidationError, which is
    the UTC validator manual_date_time_field() carries.
    """
    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyStatus.objects.create(
            user=user,
            tag=tag,
            status_id=MasteryStatus.DEMONSTRATED,
        )

    naive_now = datetime.now()  # deliberately naive, to trigger the UTC validator
    scs = StudentCompetencyStatus(
        user=user,
        tag=tag,
        status_id=MasteryStatus.DEMONSTRATED,
        created=naive_now,
        modified=now,
    )
    with pytest.raises(ValidationError):
        scs.full_clean()


@pytest.mark.parametrize("omitted", ["created", "modified"])
def test_each_timestamp_is_required_on_its_own(user, tag: Tag, now: datetime, omitted: str) -> None:
    """
    Omitting only `created`, or only `modified`, raises IntegrityError: neither column has an
    automatic default, so a field switched to `auto_now`/`auto_now_add` would not be caught by
    a test that omits both.
    """
    timestamps = {"created": now, "modified": now}
    del timestamps[omitted]
    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyStatus.objects.create(
            user=user, tag=tag, status_id=MasteryStatus.DEMONSTRATED, **timestamps,
        )


def test_conditional_raise_can_carry_modified_without_touching_created(user, tag: Tag, now: datetime) -> None:
    """
    A conditional raise can carry `modified` in the same UPDATE while leaving `created`
    untouched: there is no `auto_now` to do it for them, deliberately, because `auto_now`
    does not fire on `QuerySet.update()` and would silently leave the column stale on
    exactly that path.
    """
    scs = StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.PARTIALLY_ATTEMPTED, created=now, modified=now,
    )
    created_at = scs.created

    later = datetime.now(timezone.utc)
    changed = StudentCompetencyStatus.objects.filter(
        user=user, tag=tag, status_id__lt=MasteryStatus.DEMONSTRATED,
    ).update(status_id=MasteryStatus.DEMONSTRATED, modified=later)
    assert changed == 1

    scs.refresh_from_db()
    assert scs.status_id == MasteryStatus.DEMONSTRATED
    assert scs.modified == later
    assert scs.created == created_at


# ==============================================================================================
# No history package on StudentCompetencyStatus (ADR-0003 Decision 5).
# ==============================================================================================


def test_no_history_package_applied() -> None:
    """
    StudentCompetencyStatus has no `history` attribute.

    ADR-0003 Decision 5 leaves how learner status history is retained undecided, so no history
    package is applied to it.
    """
    assert not hasattr(StudentCompetencyStatus, "history")


# ==============================================================================================
# Deletion. StudentCompetencyStatus.tag and .status are both PROTECT: neither a Tag nor a
# CompetencyMasteryStatus row can be deleted out from under a learner's recorded status.
# ==============================================================================================


def test_tag_delete_protected_by_competency_status_on_tag(tag: Tag, user, now: datetime) -> None:
    """
    Deleting a Tag raises ProtectedError when a StudentCompetencyStatus row references it
    directly via `tag` (`on_delete=models.PROTECT`).
    """
    StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        tag.delete()

    assert Tag.objects.filter(pk=tag.pk).exists()


def test_parent_tag_delete_protected_by_competency_status_on_child_tag(
    competency_taxonomy: CompetencyTaxonomy, tag: Tag, user, now: datetime,
) -> None:
    """
    Deleting a parent Tag raises ProtectedError when a StudentCompetencyStatus row references
    one of its child tags, reached transitively: parent Tag -> child Tag via Tag.parent
    (CASCADE, in openedx_tagging) -> the status row via StudentCompetencyStatus.tag (PROTECT).
    """
    child_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Sonnets", parent=tag)
    StudentCompetencyStatus.objects.create(
        user=user, tag=child_tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        tag.delete()

    assert Tag.objects.filter(pk=tag.pk).exists()
    assert Tag.objects.filter(pk=child_tag.pk).exists()


def test_unreferenced_tag_delete_succeeds(tag: Tag, other_tag: Tag, user, now: datetime) -> None:
    """
    Deleting a Tag that no StudentCompetencyStatus row references succeeds, and leaves a status
    row on a different tag untouched: PROTECT blocks only the tags a status row references.
    """
    scs = StudentCompetencyStatus.objects.create(
        user=user, tag=other_tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    tag.delete()

    assert not Tag.objects.filter(pk=tag.pk).exists()
    assert Tag.objects.filter(pk=other_tag.pk).exists()
    assert StudentCompetencyStatus.objects.filter(pk=scs.pk).exists()


def test_taxonomy_delete_protected_by_competency_status_beneath_a_tag(
    competency_taxonomy: CompetencyTaxonomy, tag: Tag, user, now: datetime,
) -> None:
    """
    Deleting a CompetencyTaxonomy raises ProtectedError when a StudentCompetencyStatus row
    exists on one of its tags, reached transitively: CompetencyTaxonomy -> Tag via
    Tag.taxonomy (CASCADE, in openedx_tagging) -> the status row via
    StudentCompetencyStatus.tag (PROTECT). Django evaluates PROTECT on every row the collector
    reaches, not only the row passed to delete(), which is why the transitive case works.
    """
    StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        competency_taxonomy.delete()

    assert CompetencyTaxonomy.objects.filter(pk=competency_taxonomy.pk).exists()
    assert Tag.objects.filter(pk=tag.pk).exists()


def test_status_row_delete_blocked_when_referenced(tag: Tag, user, now: datetime) -> None:
    """
    Deleting a CompetencyMasteryStatus row raises ProtectedError when a StudentCompetencyStatus
    row references it via `status` (`on_delete=models.PROTECT`): the lookup table is system-owned
    immutable data, and this is what stops a row from being deleted out from under a learner's
    recorded status.
    """
    scs = StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        scs.status.delete()

    assert CompetencyMasteryStatus.objects.filter(pk=MasteryStatus.DEMONSTRATED).exists()


def test_user_delete_removes_competency_status(user, tag: Tag, now: datetime) -> None:
    """
    Deleting a User cascades to that learner's StudentCompetencyStatus row
    (`on_delete=models.CASCADE` on its `user` foreign key), while leaving the tag itself
    untouched: the status is a derived fact about the learner, so it goes when they do, but the
    competency it was measuring does not.
    """
    competency_status = StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    user.delete()

    assert not StudentCompetencyStatus.objects.filter(pk=competency_status.pk).exists()
    assert Tag.objects.filter(pk=tag.pk).exists()
