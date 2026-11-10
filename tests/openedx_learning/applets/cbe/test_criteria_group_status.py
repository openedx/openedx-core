"""Tests for StudentCompetencyCriteriaGroupStatus, a learner's status for one criteria group."""
from datetime import datetime

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyMasteryStatus,
    CompetencyRuleProfile,
    MasteryStatus,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriterionStatus,
    StudentCompetencyStatus,
)
from openedx_tagging.models import ObjectTag, Tag

pytestmark = pytest.mark.django_db


def test_second_row_for_the_same_user_and_group_is_rejected(
    user, group: CompetencyCriteriaGroup, now: datetime
) -> None:
    """
    Learner status rows are updated in place, one row per learner and node under a unique constraint:
    a second row for the same learner and group is rejected.
    """
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=MasteryStatus.PARTIALLY_ATTEMPTED, created=now, modified=now,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyCriteriaGroupStatus.objects.create(
            user=user, group=group, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
        )


def test_attempted_not_demonstrated_is_accepted(user, group: CompetencyCriteriaGroup, now: datetime) -> None:
    """
    The models accept any status value the caller writes, including `AttemptedNotDemonstrated`, which
    only `StudentCompetencyStatus` rejects.
    """
    row = StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED, created=now, modified=now,
    )

    row.refresh_from_db()
    assert row.status_id == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED


def test_group_status_accepts_a_lower_status_written_over_a_higher_one(
    user, group: CompetencyCriteriaGroup, now: datetime
) -> None:
    """
    The model accepts any status value the caller writes, so a lower status written over a higher one takes
    effect. Staff edits may lower a status, and the monotone-write rule (a status is only ever raised) applies
    to the default tree calculation alone, which enforces it in the API layer.
    """
    row = StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    changed = StudentCompetencyCriteriaGroupStatus.objects.filter(pk=row.pk).update(
        status_id=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED, modified=now,
    )

    assert changed == 1
    row.refresh_from_db()
    assert row.status_id == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED


def test_group_delete_is_protected_by_its_status(user, group: CompetencyCriteriaGroup, now: datetime) -> None:
    """
    The foreign key from `StudentCompetencyCriteriaGroupStatus` to its definition row,
    `CompetencyCriteriaGroup`, is `PROTECT`: deleting a group that holds a learner status raises `ProtectedError`.
    """
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        group.delete()

    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


def test_root_group_delete_is_protected_by_a_descendant_group_status(
    user, group: CompetencyCriteriaGroup, now: datetime
) -> None:
    """
    The transitive cases are tested, not only the direct ones: deleting a root `CompetencyCriteriaGroup`
    above a descendant group that holds a learner status row raises `ProtectedError`.
    """
    child_group = CompetencyCriteriaGroup.objects.create(tag=group.tag, parent=group)
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=child_group, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        group.delete()

    assert CompetencyCriteriaGroup.objects.filter(pk__in=[group.pk, child_group.pk]).count() == 2


def test_tag_delete_is_protected_by_a_group_status_with_no_criterion_status_beneath(
    user, tag: Tag, group: CompetencyCriteriaGroup, now: datetime
) -> None:
    """
    Deleting an `oel_tagging_tag` with a learner status row anywhere beneath it raises `ProtectedError`,
    even when that row is a group status with no criterion status beneath it.
    """
    # #642 wants the database to fail closed here, although ADR-0002 Decision 7's predicate reads only
    # the criterion table.
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    assert not StudentCompetencyCriterionStatus.objects.exists()

    with pytest.raises(ProtectedError), transaction.atomic():
        tag.delete()

    assert Tag.objects.filter(pk=tag.pk).exists()


def test_mastery_status_row_delete_is_protected_by_a_group_status(
    user, group: CompetencyCriteriaGroup, now: datetime
) -> None:
    """
    The `status_id` foreign key to `CompetencyMasteryStatuses` is `PROTECT`.
    """
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        CompetencyMasteryStatus.objects.get(pk=MasteryStatus.DEMONSTRATED).delete()

    assert CompetencyMasteryStatus.objects.filter(pk=MasteryStatus.DEMONSTRATED).exists()


def test_user_delete_removes_only_that_users_rows_from_all_three_status_tables(
    user,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
    now: datetime,
) -> None:
    """
    Deleting a user row removes that user's status rows across all three `Student*Status` models, and
    leaves other users' rows in place.
    """
    other_user = get_user_model().objects.create(username="other_learner")
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    StudentCompetencyStatus.objects.create(
        user=user, tag=group.tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=other_user, group=group, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    user_id = user.pk

    user.delete()

    assert not StudentCompetencyCriterionStatus.objects.filter(user_id=user_id).exists()
    assert not StudentCompetencyCriteriaGroupStatus.objects.filter(user_id=user_id).exists()
    assert not StudentCompetencyStatus.objects.filter(user_id=user_id).exists()
    assert StudentCompetencyCriteriaGroupStatus.objects.filter(user=other_user).exists()
