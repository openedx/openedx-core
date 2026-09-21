"""
Tests for the mastery status models: MasteryStatus, CompetencyMasteryStatus,
StudentCompetencyCriteriaStatus, StudentCompetencyCriteriaGroupStatus, and
StudentCompetencyStatus.

The deletion tests below are this ticket's headline responsibility, not an afterthought.
#641's foreign keys that carry Django's collector down a criteria tree
(`CompetencyCriteriaGroup.parent`, `CompetencyCriteriaGroup.tag`, `CompetencyCriterion.group`,
`CompetencyCriterion.object_tag`) are all CASCADE, and
`tests/openedx_learning/applets/cbe/test_criteria_deletion.py` only asserts that CASCADE half of
each transitive case, because the tables that stop the walk -- this module's three PROTECT
foreign keys (`StudentCompetencyCriteriaStatus.criterion`,
`StudentCompetencyCriteriaGroupStatus.group`, `StudentCompetencyStatus.tag`) -- did not exist on
that branch. Every ProtectedError half of those transitive cases is asserted here instead,
alongside the succeeding (no status beneath the target) half, so each case is proven both ways.
"""
from datetime import datetime, timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyMasteryStatus,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    MasteryStatus,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriteriaStatus,
    StudentCompetencyStatus,
)
from openedx_tagging.models import ObjectTag, Tag

pytestmark = pytest.mark.django_db


@pytest.fixture(name="competency_taxonomy")
def _competency_taxonomy() -> CompetencyTaxonomy:
    """A CompetencyTaxonomy for use as a scope, and as the home taxonomy for `tag`."""
    return CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")


@pytest.fixture(name="tag")
def _tag(competency_taxonomy: CompetencyTaxonomy) -> Tag:
    """A Tag, from `competency_taxonomy`, for use as the competency a criteria tree evaluates."""
    return Tag.objects.create(taxonomy=competency_taxonomy, value="Writing Poetry")


@pytest.fixture(name="object_tag")
def _object_tag(competency_taxonomy: CompetencyTaxonomy, tag: Tag) -> ObjectTag:
    """An ObjectTag associating `tag` with a made-up content object, for use as a criterion's target."""
    return ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+p1",
        taxonomy=competency_taxonomy,
        tag=tag,
    )


@pytest.fixture(name="group")
def _group(tag: Tag) -> CompetencyCriteriaGroup:
    """A root CompetencyCriteriaGroup for `tag`, for use as a criterion's parent group."""
    return CompetencyCriteriaGroup.objects.create(tag=tag)


@pytest.fixture(name="default_rule_profile")
def _default_rule_profile() -> CompetencyRuleProfile:
    """The system-default CompetencyRuleProfile seeded by migration 0003."""
    return CompetencyRuleProfile.objects.get(
        organization__isnull=True,
        course__isnull=True,
        competency_taxonomy__isnull=True,
    )


@pytest.fixture(name="user")
def _user():
    """
    Create a single learner for use in these tests.

    Deliberately unannotated: the user model is swappable, so this library must not
    name a concrete one (edx-lint enforces that as `imported-auth-user`).
    """
    return get_user_model().objects.create(username="learner")


@pytest.fixture(name="now")
def _now() -> datetime:
    """A single UTC timestamp shared by writes in a test."""
    return datetime.now(timezone.utc)


@pytest.fixture(name="other_tag")
def _other_tag(competency_taxonomy: CompetencyTaxonomy) -> Tag:
    """A second Tag, from the same taxonomy as `tag`, for a second competency-level status."""
    return Tag.objects.create(taxonomy=competency_taxonomy, value="Decimals")


@pytest.fixture(name="other_object_tag")
def _other_object_tag(competency_taxonomy: CompetencyTaxonomy, tag: Tag) -> ObjectTag:
    """A second ObjectTag on `tag`, distinct from `object_tag`, for a second leaf criterion."""
    return ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+p2",
        taxonomy=competency_taxonomy,
        tag=tag,
    )


@pytest.fixture(name="criterion")
def _criterion(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> CompetencyCriterion:
    """A leaf CompetencyCriterion under `group`, using the system-default rule profile."""
    return CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=default_rule_profile)


@pytest.fixture(name="child_group")
def _child_group(tag: Tag, group: CompetencyCriteriaGroup) -> CompetencyCriteriaGroup:
    """A child CompetencyCriteriaGroup under `group`, for a tree the collector must walk two levels down."""
    return CompetencyCriteriaGroup.objects.create(tag=tag, parent=group)


@pytest.fixture(name="child_criterion")
def _child_criterion(
    child_group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> CompetencyCriterion:
    """A leaf CompetencyCriterion under `child_group`, for exercising a delete at depth."""
    return CompetencyCriterion.objects.create(
        group=child_group, object_tag=object_tag, rule_profile=default_rule_profile
    )


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
# The monotone conditional-update comparison, on the leaf level (where ADR-0004 Decision 1 has an
# automatic raise actually land) and on the top level (the backup's original coverage).
# ==============================================================================================


def test_conditional_raise_is_a_single_no_op_or_effective_update_on_leaf(
    user, criterion: CompetencyCriterion, now: datetime,
) -> None:
    """
    The monotone comparison works in a single statement on StudentCompetencyCriteriaStatus, the
    leaf level. ADR-0004 Decision 1 writes the leaf synchronously with the learner's grade, so
    this is where an automatic raise actually lands; the top-level equivalent below exercises the
    same UPDATE shape one level up.
    """
    scs = StudentCompetencyCriteriaStatus.objects.create(
        user=user,
        criterion=criterion,
        status_id=MasteryStatus.DEMONSTRATED,
        created=now,
        modified=now,
    )

    # Already at the top rank: an attempted raise to a lower/equal rank changes nothing.
    changed = StudentCompetencyCriteriaStatus.objects.filter(
        user=user, criterion=criterion, status_id__lt=MasteryStatus.PARTIALLY_ATTEMPTED,
    ).update(status_id=MasteryStatus.PARTIALLY_ATTEMPTED, modified=now)
    assert changed == 0
    scs.refresh_from_db()
    assert scs.status_id == MasteryStatus.DEMONSTRATED

    # Lower the stored status, then confirm the same shape raises it exactly once.
    StudentCompetencyCriteriaStatus.objects.filter(pk=scs.pk).update(status_id=MasteryStatus.PARTIALLY_ATTEMPTED)
    changed = StudentCompetencyCriteriaStatus.objects.filter(
        user=user, criterion=criterion, status_id__lt=MasteryStatus.DEMONSTRATED,
    ).update(status_id=MasteryStatus.DEMONSTRATED, modified=now)
    assert changed == 1
    scs.refresh_from_db()
    assert scs.status_id == MasteryStatus.DEMONSTRATED


def test_conditional_raise_is_a_single_no_op_or_effective_update(user, tag: Tag, now: datetime) -> None:
    """
    The monotone comparison works in a single statement on StudentCompetencyStatus, the top
    level: a conditional UPDATE guarded by status_id__lt is a no-op against an already-higher
    status, and is the one write that takes effect when the stored status is lower.
    """
    scs = StudentCompetencyStatus.objects.create(
        user=user,
        tag=tag,
        status_id=MasteryStatus.DEMONSTRATED,
        created=now,
        modified=now,
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
# The allow-list check constraint, which applies only to StudentCompetencyStatus, and its
# boundary: the other two models accept all three MasteryStatus values.
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
    bypasses Model.save() and so would otherwise skip any Python-side validation.
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
        user=user,
        tag=tag,
        status_id=MasteryStatus.PARTIALLY_ATTEMPTED,
        created=now,
        modified=now,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyStatus.objects.filter(pk=scs.pk).update(
            status_id=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED,
        )


def test_demonstrated_and_partially_attempted_both_accepted(
    user, tag: Tag, other_tag: Tag, now: datetime,
) -> None:
    """
    Both statuses the allow-list permits, Demonstrated and PartiallyAttempted, are
    accepted on create().
    """
    demonstrated = StudentCompetencyStatus.objects.create(
        user=user,
        tag=tag,
        status_id=MasteryStatus.DEMONSTRATED,
        created=now,
        modified=now,
    )
    partially_attempted = StudentCompetencyStatus.objects.create(
        user=user,
        tag=other_tag,
        status_id=MasteryStatus.PARTIALLY_ATTEMPTED,
        created=now,
        modified=now,
    )
    assert demonstrated.status_id == MasteryStatus.DEMONSTRATED
    assert partially_attempted.status_id == MasteryStatus.PARTIALLY_ATTEMPTED


def test_leaf_and_group_status_accept_attempted_not_demonstrated(
    user, criterion: CompetencyCriterion, group: CompetencyCriteriaGroup, now: datetime,
) -> None:
    """
    Unlike StudentCompetencyStatus, neither StudentCompetencyCriteriaStatus nor
    StudentCompetencyCriteriaGroupStatus carries a check constraint on `status`: all three
    MasteryStatus values, including AttemptedNotDemonstrated, are valid at the leaf and group
    levels, because an in-progress state is meaningful there. The restriction to
    PartiallyAttempted/Demonstrated is specific to the top-level competency status, which
    represents overall demonstration, not an in-progress state.
    """
    leaf = StudentCompetencyCriteriaStatus.objects.create(
        user=user,
        criterion=criterion,
        status_id=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED,
        created=now,
        modified=now,
    )
    group_status = StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user,
        group=group,
        status_id=MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED,
        created=now,
        modified=now,
    )
    assert leaf.status_id == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED
    assert group_status.status_id == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED


# ==============================================================================================
# One row per learner and node (ADR-0002 Decision 5 indexes 6, 7, and 8), one test per model.
# ==============================================================================================


def test_one_row_per_user_and_criterion_but_multiple_criteria_per_user(
    user, criterion: CompetencyCriterion, other_object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    The (user, criterion) unique constraint rejects a second row for the same pair, but the
    same learner may hold a status for a different criterion.
    """
    # Same group as `criterion`, read off it rather than taking a separate `group` fixture
    # argument, which would push this function over pylint's max-args.
    other_criterion = CompetencyCriterion.objects.create(
        group=criterion.group, object_tag=other_object_tag, rule_profile=default_rule_profile
    )
    StudentCompetencyCriteriaStatus.objects.create(
        user=user,
        criterion=criterion,
        status_id=MasteryStatus.PARTIALLY_ATTEMPTED,
        created=now,
        modified=now,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyCriteriaStatus.objects.create(
            user=user,
            criterion=criterion,
            status_id=MasteryStatus.DEMONSTRATED,
            created=now,
            modified=now,
        )

    # A different criterion for the same user is a different (user, criterion) pair, so it's allowed.
    other = StudentCompetencyCriteriaStatus.objects.create(
        user=user,
        criterion=other_criterion,
        status_id=MasteryStatus.DEMONSTRATED,
        created=now,
        modified=now,
    )
    assert other.criterion_id == other_criterion.pk


def test_one_row_per_user_and_group_but_multiple_groups_per_user(
    user, tag: Tag, group: CompetencyCriteriaGroup, now: datetime,
) -> None:
    """
    The (user, group) unique constraint rejects a second row for the same pair, but the
    same learner may hold a status for a different group.
    """
    other_group = CompetencyCriteriaGroup.objects.create(tag=tag)
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user,
        group=group,
        status_id=MasteryStatus.PARTIALLY_ATTEMPTED,
        created=now,
        modified=now,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyCriteriaGroupStatus.objects.create(
            user=user,
            group=group,
            status_id=MasteryStatus.DEMONSTRATED,
            created=now,
            modified=now,
        )

    # A different group for the same user is a different (user, group) pair, so it's allowed.
    other = StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user,
        group=other_group,
        status_id=MasteryStatus.DEMONSTRATED,
        created=now,
        modified=now,
    )
    assert other.group_id == other_group.pk


def test_one_row_per_user_and_tag_but_multiple_tags_per_user(
    user, tag: Tag, other_tag: Tag, now: datetime,
) -> None:
    """
    The (user, tag) unique constraint rejects a second row for the same pair, but
    the same learner may hold a status for a different tag.
    """
    StudentCompetencyStatus.objects.create(
        user=user,
        tag=tag,
        status_id=MasteryStatus.PARTIALLY_ATTEMPTED,
        created=now,
        modified=now,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        StudentCompetencyStatus.objects.create(
            user=user,
            tag=tag,
            status_id=MasteryStatus.DEMONSTRATED,
            created=now,
            modified=now,
        )

    # A different tag for the same user is a different (user, tag) pair, so it's allowed.
    other = StudentCompetencyStatus.objects.create(
        user=user,
        tag=other_tag,
        status_id=MasteryStatus.DEMONSTRATED,
        created=now,
        modified=now,
    )
    assert other.tag_id == other_tag.pk


# ==============================================================================================
# created/modified: caller-supplied, required, and UTC-only. The field is defined identically
# (manual_date_time_field()) on all three models, so one model's coverage stands for all three.
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


def test_conditional_raise_can_carry_modified_without_touching_created(user, tag: Tag, now: datetime) -> None:
    """
    A conditional raise can carry `modified` in the same UPDATE while leaving `created`
    untouched, showing the mandated write path can keep `modified` honest without an
    auto_now, which is why the field is caller-supplied rather than automatic.
    """
    created_at = now
    scs = StudentCompetencyStatus.objects.create(
        user=user,
        tag=tag,
        status_id=MasteryStatus.PARTIALLY_ATTEMPTED,
        created=created_at,
        modified=created_at,
    )

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
# No history package on any of the three learner status models (ADR-0003 Decision 5), unlike
# #641's criteria definition models.
# ==============================================================================================


def test_no_history_package_applied() -> None:
    """
    None of the three learner status models has a `history` attribute.

    ADR-0003 Decision 1 gives django-simple-history to the three criteria definition models
    (CompetencyCriteriaGroup, CompetencyCriterion, CompetencyRuleProfile) only. ADR-0003 Decision
    5 leaves how learner status history is retained undecided, so no history package is applied
    to any of the three models here.
    """
    assert not hasattr(StudentCompetencyCriteriaStatus, "history")
    assert not hasattr(StudentCompetencyCriteriaGroupStatus, "history")
    assert not hasattr(StudentCompetencyStatus, "history")


# ==============================================================================================
# Deletion. Each transitive case is proven both ways: ProtectedError when a status row exists
# somewhere beneath the target, and a full cascade of the criteria tree when none does. Across
# the tag/group/taxonomy cases, the blocking status row is deliberately placed at a different
# level each time (a competency status directly on the tag, a group status under a group, and a
# leaf status two levels below a taxonomy) so the suite as a whole exercises all three PROTECT
# foreign keys, not just one of them repeatedly.
# ==============================================================================================


def test_tag_delete_protected_by_competency_status_on_tag(
    tag: Tag, group: CompetencyCriteriaGroup, criterion: CompetencyCriterion, user, now: datetime,
) -> None:
    """
    Deleting a Tag raises ProtectedError when a StudentCompetencyStatus row references it
    directly via `tag` (`on_delete=models.PROTECT`). This is the shallowest of the transitive
    cases: the collector finds the blocking row on the tag itself, with no CASCADE hop needed
    first.
    """
    StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        tag.delete()

    assert Tag.objects.filter(pk=tag.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_tag_delete_with_no_status_cascades_whole_criteria_tree(
    tag: Tag, group: CompetencyCriteriaGroup, criterion: CompetencyCriterion,
) -> None:
    """
    Deleting a Tag with no learner status beneath it succeeds and cascades away the whole
    criteria tree hanging off it: Tag -> CompetencyCriteriaGroup.tag (CASCADE) ->
    CompetencyCriterion.group (CASCADE).
    """
    tag.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_group_delete_at_depth_protected_by_leaf_status(
    group: CompetencyCriteriaGroup, child_group: CompetencyCriteriaGroup, child_criterion: CompetencyCriterion,
    user, now: datetime,
) -> None:
    """
    Deleting a CompetencyCriteriaGroup raises ProtectedError when a
    StudentCompetencyCriteriaStatus row exists on a criterion two levels below it:
    `group` -> `child_group` via CompetencyCriteriaGroup.parent (CASCADE), then `child_group` ->
    `child_criterion` via CompetencyCriterion.group (CASCADE), then `child_criterion` -> the
    status row via StudentCompetencyCriteriaStatus.criterion (PROTECT). Deleting `group` makes
    Django's collector walk both CASCADE hops before it reaches the PROTECT that stops it.
    """
    StudentCompetencyCriteriaStatus.objects.create(
        user=user, criterion=child_criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        group.delete()

    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=child_group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=child_criterion.pk).exists()


def test_group_delete_at_depth_with_no_status_cascades_descendants(
    group: CompetencyCriteriaGroup, child_group: CompetencyCriteriaGroup, child_criterion: CompetencyCriterion,
) -> None:
    """
    Deleting a CompetencyCriteriaGroup with no status rows beneath it succeeds and cascades away
    its child group and that child's criterion, via the same two CASCADE hops
    (CompetencyCriteriaGroup.parent, then CompetencyCriterion.group) that the protected case
    above walks before hitting a PROTECT.
    """
    group.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=child_group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=child_criterion.pk).exists()


def test_object_tag_delete_protected_by_leaf_status(
    object_tag: ObjectTag, criterion: CompetencyCriterion, user, now: datetime,
) -> None:
    """
    Deleting an ObjectTag raises ProtectedError when a StudentCompetencyCriteriaStatus row
    references its criterion, reached transitively: ObjectTag -> CompetencyCriterion.object_tag
    (CASCADE) -> the status row via StudentCompetencyCriteriaStatus.criterion (PROTECT).
    """
    StudentCompetencyCriteriaStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        object_tag.delete()

    assert ObjectTag.objects.filter(pk=object_tag.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_object_tag_delete_with_no_status_cascades_criterion(
    object_tag: ObjectTag, criterion: CompetencyCriterion,
) -> None:
    """
    Deleting an ObjectTag with no learner status on its criterion succeeds and cascades that
    criterion away via CompetencyCriterion.object_tag (CASCADE).
    """
    object_tag.delete()

    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_taxonomy_delete_protected_by_group_status(
    competency_taxonomy: CompetencyTaxonomy, tag: Tag, group: CompetencyCriteriaGroup, user, now: datetime,
) -> None:
    """
    Deleting a CompetencyTaxonomy raises ProtectedError when a
    StudentCompetencyCriteriaGroupStatus row exists on a group under one of its tags, reached
    transitively: CompetencyTaxonomy -> Tag via Tag.taxonomy (CASCADE, in openedx_tagging) ->
    `group` via CompetencyCriteriaGroup.tag (CASCADE) -> the status row via
    StudentCompetencyCriteriaGroupStatus.group (PROTECT).
    """
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        competency_taxonomy.delete()

    assert CompetencyTaxonomy.objects.filter(pk=competency_taxonomy.pk).exists()
    assert Tag.objects.filter(pk=tag.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


def test_taxonomy_delete_with_no_status_cascades_every_tag_and_its_criteria(
    competency_taxonomy: CompetencyTaxonomy, tag: Tag, group: CompetencyCriteriaGroup,
    criterion: CompetencyCriterion,
) -> None:
    """
    Deleting a CompetencyTaxonomy with no learner status beneath any of its tags succeeds and
    cascades away every tag and its criteria tree: CompetencyTaxonomy -> Tag (CASCADE) ->
    CompetencyCriteriaGroup.tag (CASCADE) -> CompetencyCriterion.group (CASCADE). This is the
    same chain case 13 (`test_tag_delete_with_no_status_cascades_whole_criteria_tree`) proves,
    reached transitively from one level higher.
    """
    competency_taxonomy.delete()

    assert not Tag.objects.filter(pk=tag.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_user_delete_removes_status_across_all_three_models(
    user, tag: Tag, group: CompetencyCriteriaGroup, criterion: CompetencyCriterion, now: datetime,
) -> None:
    """
    Deleting a User cascades to that learner's status rows in all three models
    (`on_delete=models.CASCADE` on each model's `user` foreign key), while leaving the
    competency definitions themselves untouched: the status is a derived fact about the
    learner, so it goes when they do, but the criteria tree it was measuring does not.
    """
    leaf = StudentCompetencyCriteriaStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    group_status = StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    competency_status = StudentCompetencyStatus.objects.create(
        user=user, tag=tag, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    user.delete()

    assert not StudentCompetencyCriteriaStatus.objects.filter(pk=leaf.pk).exists()
    assert not StudentCompetencyCriteriaGroupStatus.objects.filter(pk=group_status.pk).exists()
    assert not StudentCompetencyStatus.objects.filter(pk=competency_status.pk).exists()
    assert Tag.objects.filter(pk=tag.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
