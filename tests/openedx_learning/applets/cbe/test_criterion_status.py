"""Tests for StudentCompetencyCriterionStatus, a learner's status for one leaf criterion."""
from datetime import datetime

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from openedx_catalog.models import CourseRun
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyMasteryStatus,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    MasteryStatus,
    StudentCompetencyCriterionStatus,
)
from openedx_tagging import api as tagging_api
from openedx_tagging.models import ObjectTag, Tag

pytestmark = pytest.mark.django_db


@pytest.fixture(name="criterion")
def _criterion(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> CompetencyCriterion:
    """A leaf CompetencyCriterion directly under the root `group`."""
    return CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=default_rule_profile)


def test_second_row_for_the_same_user_and_criterion_is_rejected(
    user, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    Learner status rows are updated in place, one row per learner and node under a unique constraint:
    a second row for the same learner and criterion is rejected.
    """
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.PARTIALLY_ATTEMPTED, created=now, modified=now,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyCriterionStatus.objects.create(
            user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
        )


def test_same_user_can_hold_status_for_a_second_criterion(
    user, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    One row per learner and node: the unique constraint is on the learner and the criterion together, so
    the same learner holds a separate row for each criterion.
    """
    second_criterion = CompetencyCriterion.objects.create(
        group=criterion.group, object_tag=criterion.object_tag, rule_profile=criterion.rule_profile
    )
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=second_criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    assert StudentCompetencyCriterionStatus.objects.filter(user=user).count() == 2


def test_attempted_not_demonstrated_is_accepted(user, criterion: CompetencyCriterion, now: datetime) -> None:
    """
    The models accept any status value the caller writes, including `AttemptedNotDemonstrated`, which
    only `StudentCompetencyStatus` rejects.
    """
    row = StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED, created=now, modified=now,
    )

    row.refresh_from_db()
    assert row.status_id == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED


def test_criterion_status_accepts_a_lower_status_written_over_a_higher_one(
    user, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    The model accepts any status value the caller writes, so a lower status written over a higher one takes
    effect. Staff may downward-correct a wrong status, and the monotone-write rule (a status is only ever
    raised) applies to the default tree calculation alone, which enforces it in the API layer.
    """
    row = StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    changed = StudentCompetencyCriterionStatus.objects.filter(pk=row.pk).update(
        status_id=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED, modified=now,
    )

    assert changed == 1
    row.refresh_from_db()
    assert row.status_id == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED


def test_criterion_delete_is_protected_by_its_status(user, criterion: CompetencyCriterion, now: datetime) -> None:
    """
    The foreign key from `StudentCompetencyCriterionStatus` to its definition row, `CompetencyCriterion`,
    is `PROTECT`: deleting a criterion that holds a learner status raises `ProtectedError`.
    """
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        criterion.delete()

    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_object_tag_delete_is_protected_by_a_criterion_status(
    user, object_tag: ObjectTag, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    Deleting an `oel_tagging_objecttag` whose criterion has a learner status row raises `ProtectedError`.
    """
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        object_tag.delete()

    assert ObjectTag.objects.filter(pk=object_tag.pk).exists()


def test_tagging_api_object_tag_delete_is_protected_by_a_criterion_status(
    user, object_tag: ObjectTag, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    Deleting an `oel_tagging_objecttag` whose criterion has a learner status row raises `ProtectedError`,
    also through `openedx_tagging.api.delete_object_tags()`.
    """
    # delete_object_tags() deletes through a QuerySet, a different call path from Model.delete().
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        tagging_api.delete_object_tags(object_tag.object_id)

    assert ObjectTag.objects.filter(pk=object_tag.pk).exists()


def test_object_tag_delete_with_no_status_beneath_it_cascades_its_criterion_away(
    user, competency_taxonomy: CompetencyTaxonomy, tag: Tag, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    Deleting an `oel_tagging_objecttag` with no status rows beneath it succeeds and cascades its criterion
    away, while a criterion on another object tag holds status.
    """
    criterion_with_status = CompetencyCriterion.objects.create(
        group=criterion.group,
        object_tag=ObjectTag.objects.create(
            object_id="block-v1:Org1+Python100+Fall2026+problem+p4", taxonomy=competency_taxonomy, tag=tag,
        ),
        rule_profile=criterion.rule_profile,
    )
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion_with_status, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    criterion.object_tag.delete()

    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion_with_status.pk).exists()


def test_root_group_delete_is_protected_by_a_criterion_status_two_levels_down(
    user, group: CompetencyCriteriaGroup, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    The transitive cases are tested, not only the direct ones: deleting a root `CompetencyCriteriaGroup`
    with a learner status row two levels beneath it raises `ProtectedError` and removes nothing.
    """
    child_group = CompetencyCriteriaGroup.objects.create(tag=group.tag, parent=group)
    criterion.group = child_group
    criterion.save()
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        group.delete()

    assert CompetencyCriteriaGroup.objects.filter(pk__in=[group.pk, child_group.pk]).count() == 2
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_tag_delete_is_protected_by_a_criterion_status(
    user, tag: Tag, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    Deleting an `oel_tagging_tag` with a learner status row anywhere beneath it raises `ProtectedError`.
    """
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        tag.delete()

    assert Tag.objects.filter(pk=tag.pk).exists()


def test_tag_delete_with_no_status_beneath_it_cascades_the_whole_criteria_tree_away(
    user, competency_taxonomy: CompetencyTaxonomy, tag: Tag, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    Deleting an `oel_tagging_tag` with no status rows beneath it succeeds and cascades the whole criteria
    tree away, while another tag's tree holds status.
    """
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Decimals")
    other_criterion = CompetencyCriterion.objects.create(
        group=CompetencyCriteriaGroup.objects.create(tag=other_tag),
        object_tag=ObjectTag.objects.create(
            object_id="block-v1:Org1+Python100+Fall2026+problem+p3", taxonomy=competency_taxonomy, tag=other_tag,
        ),
        rule_profile=criterion.rule_profile,
    )
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=other_criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    tag.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=criterion.group_id).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=other_criterion.pk).exists()


def test_tagging_api_parent_tag_delete_is_protected_by_a_criterion_status_under_a_subtag(
    user,
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
    now: datetime,
) -> None:
    """
    Deleting an `oel_tagging_tag` with a learner status row anywhere beneath it raises `ProtectedError`,
    including a row beneath one of its subtags, deleted through `openedx_tagging.api.delete_tags_from_taxonomy()`.
    """
    subtag = Tag.objects.create(taxonomy=competency_taxonomy, parent=tag, value="Sonnets")
    subtag_object_tag = ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+p2", taxonomy=competency_taxonomy, tag=subtag,
    )
    subtag_group = CompetencyCriteriaGroup.objects.create(tag=subtag)
    subtag_criterion = CompetencyCriterion.objects.create(
        group=subtag_group, object_tag=subtag_object_tag, rule_profile=default_rule_profile
    )
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=subtag_criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    count_before = Tag.objects.filter(pk__in=[tag.pk, subtag.pk]).count()

    with pytest.raises(ProtectedError), transaction.atomic():
        tagging_api.delete_tags_from_taxonomy(competency_taxonomy, [tag.value], with_subtags=True)

    assert count_before == 2
    assert Tag.objects.filter(pk__in=[tag.pk, subtag.pk]).count() == count_before


def test_taxonomy_delete_is_protected_by_a_criterion_status(
    user, competency_taxonomy: CompetencyTaxonomy, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    Deleting an `oel_tagging_taxonomy` raises `ProtectedError` when a status row exists beneath any of its
    tags, here deleted through its `CompetencyTaxonomy` row.
    """
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        competency_taxonomy.delete()

    assert CompetencyTaxonomy.objects.filter(pk=competency_taxonomy.pk).exists()


def test_course_run_delete_is_protected_by_a_criterion_status_in_its_course_scoped_group(
    user, group: CompetencyCriteriaGroup, course_run: CourseRun, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    The transitive cases are tested, not only the direct ones: deleting a `CourseRun` whose course-scoped
    `CompetencyCriteriaGroup` holds a criterion with a learner status row raises `ProtectedError`.
    """
    course_group = CompetencyCriteriaGroup.objects.create(tag=group.tag, parent=group, course=course_run)
    criterion.group = course_group
    criterion.save()
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        course_run.delete()

    assert CourseRun.objects.filter(pk=course_run.pk).exists()


def test_deleting_a_branch_with_no_status_succeeds_while_a_sibling_branch_holds_status(
    user,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
    now: datetime,
) -> None:
    """
    Deleting a `CompetencyCriteriaGroup` at depth with no status rows beneath it succeeds and cascades its
    criteria away, while a sibling branch of the same tree holds status.
    """
    branch_with_status = CompetencyCriteriaGroup.objects.create(tag=group.tag, parent=group)
    branch_without_status = CompetencyCriteriaGroup.objects.create(tag=group.tag, parent=group)
    criterion_with_status = CompetencyCriterion.objects.create(
        group=branch_with_status, object_tag=object_tag, rule_profile=default_rule_profile
    )
    criterion_without_status = CompetencyCriterion.objects.create(
        group=branch_without_status, object_tag=object_tag, rule_profile=default_rule_profile
    )
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion_with_status, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    branch_without_status.delete()

    assert not CompetencyCriterion.objects.filter(pk=criterion_without_status.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion_with_status.pk).exists()


def test_mastery_status_row_delete_is_protected_by_a_criterion_status(
    user, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    The `status_id` foreign key to `CompetencyMasteryStatuses` is `PROTECT`.
    """
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        CompetencyMasteryStatus.objects.get(pk=MasteryStatus.DEMONSTRATED).delete()

    assert CompetencyMasteryStatus.objects.filter(pk=MasteryStatus.DEMONSTRATED).exists()


def test_user_delete_removes_only_that_users_criterion_status(
    user, criterion: CompetencyCriterion, now: datetime
) -> None:
    """
    The `user_id` foreign key on `StudentCompetencyCriterionStatus` is `CASCADE`: deleting a user row
    removes that user's status rows, and only that user's.
    """
    other_user = get_user_model().objects.create(username="other_learner")
    for learner in (user, other_user):
        StudentCompetencyCriterionStatus.objects.create(
            user=learner, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
        )
    user_id = user.pk

    user.delete()

    assert not StudentCompetencyCriterionStatus.objects.filter(user_id=user_id).exists()
    assert StudentCompetencyCriterionStatus.objects.filter(user=other_user).exists()
