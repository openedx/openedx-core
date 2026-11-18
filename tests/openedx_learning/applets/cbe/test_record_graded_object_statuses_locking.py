"""
Tests for the deletion-lock side effect of `record_graded_object_statuses` (issue #782). Recording a
competency status locks the association behind its criterion, so that the association is archived
rather than deleted later.
"""
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from openedx_learning.api import GradedObjectScore, record_graded_object_statuses
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    MasteryStatus,
    RuleType,
    StudentCompetencyCriterionStatus,
)
from openedx_tagging.api import (
    delete_object_tags,
    get_tags_locked_for_deletion,
    is_taxonomy_locked_for_deletion,
    tag_object,
)
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

pytestmark = pytest.mark.django_db


def _make_criterion(
    *,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    rule_profile: CompetencyRuleProfile | None = None,
    override: tuple[str, dict] | None = None,
) -> CompetencyCriterion:
    """Build a leaf CompetencyCriterion using whichever of rule_profile or override the caller passes."""
    rule_type_override, rule_payload_override = override if override is not None else (None, None)
    return CompetencyCriterion.objects.create(
        group=group,
        object_tag=object_tag,
        rule_profile=rule_profile,
        rule_type_override=rule_type_override,
        rule_payload_override=rule_payload_override,
    )


def _score(object_tag: ObjectTag) -> GradedObjectScore:
    """A passing score for the object behind `object_tag`."""
    return GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.9"))


@pytest.fixture(name="criterion")
def _criterion(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> CompetencyCriterion:
    """A leaf CompetencyCriterion directly under the root `group`, using the seeded default profile."""
    return _make_criterion(group=group, object_tag=object_tag, rule_profile=default_rule_profile)


@pytest.mark.usefixtures("criterion")
def test_recording_a_status_locks_the_criterion_object_tag(user, object_tag: ObjectTag) -> None:
    record_graded_object_statuses(user_id=user.id, scores=[_score(object_tag)])

    object_tag.refresh_from_db()
    assert object_tag.deletion_locked is True


def test_second_learner_on_locked_association_succeeds_and_keeps_lock(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag
) -> None:
    record_graded_object_statuses(user_id=user.id, scores=[_score(object_tag)])
    second_user = get_user_model().objects.create(username="learner2")

    changed = record_graded_object_statuses(user_id=second_user.id, scores=[_score(object_tag)])

    assert changed == 1
    assert StudentCompetencyCriterionStatus.objects.filter(criterion=criterion, user_id=second_user.id).exists()
    object_tag.refresh_from_db()
    assert object_tag.deletion_locked is True


def test_graded_object_with_no_criteria_performs_no_lock_query(user) -> None:
    """Content with no competency criteria never reaches the lock call and locks nothing."""
    score = GradedObjectScore(object_id="block-v1:Org1+Python100+Fall2026+problem+none", fraction=Decimal("0.9"))

    with mock.patch("openedx_learning.applets.cbe.api.lock_object_tags_for_deletion") as lock:
        record_graded_object_statuses(user_id=user.id, scores=[score])

    lock.assert_not_called()
    assert ObjectTag.objects.filter(deletion_locked=True).count() == 0


@pytest.mark.parametrize(
    "corrupt_kwargs",
    [
        pytest.param({"rule_type_override": "no-such-rule"}, id="unknown_rule_type"),
        pytest.param({"rule_payload_override": {"op": "gte"}}, id="malformed_payload"),
    ],
)
def test_unappliable_rule_leaves_association_unlocked(
    user, competency_taxonomy: CompetencyTaxonomy, corrupt_kwargs: dict
) -> None:
    """A criterion whose rule cannot be applied is skipped, so its association is not locked."""
    bad_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Bad Rule")
    bad_group = CompetencyCriteriaGroup.objects.create(tag=bad_tag)
    bad_object_tag = ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+bad", taxonomy=competency_taxonomy, tag=bad_tag,
    )
    bad_criterion = _make_criterion(
        group=bad_group, object_tag=bad_object_tag,
        override=(RuleType.GRADE, {"op": "gte", "value": 0.8, "scale": "percent"}),
    )
    CompetencyCriterion.objects.filter(pk=bad_criterion.pk).update(**corrupt_kwargs)

    changed = record_graded_object_statuses(user_id=user.id, scores=[_score(bad_object_tag)])

    assert changed == 0
    bad_object_tag.refresh_from_db()
    assert bad_object_tag.deletion_locked is False


@pytest.mark.parametrize("criteria_count", [1, 3])
def test_lock_is_one_bulk_call_regardless_of_criteria_count(
    user, competency_taxonomy: CompetencyTaxonomy, default_rule_profile: CompetencyRuleProfile, criteria_count: int
) -> None:
    object_id = "block-v1:Org1+Python100+Fall2026+problem+many"
    for index in range(criteria_count):
        tag = Tag.objects.create(taxonomy=competency_taxonomy, value=f"Competency {index}")
        object_tag = ObjectTag.objects.create(object_id=object_id, taxonomy=competency_taxonomy, tag=tag)
        _make_criterion(
            group=CompetencyCriteriaGroup.objects.create(tag=tag),
            object_tag=object_tag,
            rule_profile=default_rule_profile,
        )

    with CaptureQueriesContext(connection) as captured:
        changed = record_graded_object_statuses(
            user_id=user.id, scores=[GradedObjectScore(object_id=object_id, fraction=Decimal("0.9"))],
        )

    assert changed == criteria_count
    lock_updates = [
        query["sql"] for query in captured.captured_queries
        if "oel_tagging_objecttag" in query["sql"] and "UPDATE" in query["sql"].upper()
    ]
    assert len(lock_updates) == 1
    objecttag_queries = [q["sql"] for q in captured.captured_queries if "oel_tagging_objecttag" in q["sql"]]
    # Three queries touch the table whatever the criteria count: the criteria SELECT (which joins it through
    # select_related), then the lock's SELECT of pk and deletion_locked, then the lock's single UPDATE.
    assert len(objecttag_queries) == 3
    assert ObjectTag.objects.filter(object_id=object_id, deletion_locked=True).count() == criteria_count


def test_rolled_back_status_write_leaves_nothing_locked(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag
) -> None:
    """A status write that is rolled back takes its lock with it, since both share the caller's transaction."""

    class _BoomError(Exception):
        """Raised deliberately to force the enclosing transaction to roll back."""

    with pytest.raises(_BoomError), transaction.atomic():
        record_graded_object_statuses(user_id=user.id, scores=[_score(object_tag)])
        assert ObjectTag.objects.values_list("deletion_locked", flat=True).get(pk=object_tag.pk) is True
        raise _BoomError()

    object_tag.refresh_from_db()
    assert object_tag.deletion_locked is False
    assert not StudentCompetencyCriterionStatus.objects.filter(criterion=criterion).exists()


def test_lock_failure_rolls_back_the_status_write(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag
) -> None:
    with mock.patch(
        "openedx_learning.applets.cbe.api.lock_object_tags_for_deletion",
        side_effect=ObjectTag.DoesNotExist("gone"),
    ):
        with pytest.raises(ObjectTag.DoesNotExist), transaction.atomic():
            record_graded_object_statuses(user_id=user.id, scores=[_score(object_tag)])

    assert not StudentCompetencyCriterionStatus.objects.filter(criterion=criterion).exists()
    object_tag.refresh_from_db()
    assert object_tag.deletion_locked is False


def test_criterion_whose_status_already_exists_is_still_locked(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag, now
) -> None:
    StudentCompetencyCriterionStatus.objects.create(
        user_id=user.id, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    assert object_tag.deletion_locked is False

    changed = record_graded_object_statuses(user_id=user.id, scores=[_score(object_tag)])

    assert changed == 0
    object_tag.refresh_from_db()
    assert object_tag.deletion_locked is True


def test_deleting_locked_association_archives_it_and_protects_tag_and_taxonomy(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag, tag: Tag, competency_taxonomy: CompetencyTaxonomy
) -> None:
    """Deleting a locked association archives it, protects its tag and taxonomy, and keeps the status resolving."""
    record_graded_object_statuses(user_id=user.id, scores=[_score(object_tag)])
    assert get_tags_locked_for_deletion([tag.id]) == {tag.id}
    assert is_taxonomy_locked_for_deletion(competency_taxonomy.id) is True

    delete_object_tags(object_tag.object_id)

    object_tag.refresh_from_db()
    assert object_tag.archived is True
    assert object_tag.deletion_locked is True
    assert get_tags_locked_for_deletion([tag.id]) == {tag.id}
    assert is_taxonomy_locked_for_deletion(competency_taxonomy.id) is True
    assert not hasattr(Tag, "deletion_locked")
    assert not hasattr(Taxonomy, "deletion_locked")
    resolved = StudentCompetencyCriterionStatus.objects.get(criterion=criterion, user_id=user.id).criterion.object_tag
    assert resolved.pk == object_tag.pk
    assert resolved.archived is True


def test_archived_association_records_no_new_mastery(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag, competency_taxonomy: CompetencyTaxonomy
) -> None:
    """
    A criterion whose tag association was archived after a learner earned a status through it records no mastery
    for a later learner, while the earlier learner's status still resolves through the archived association.
    """
    record_graded_object_statuses(user_id=user.id, scores=[_score(object_tag)])
    tag_object(object_tag.object_id, competency_taxonomy, [])
    object_tag.refresh_from_db()
    assert object_tag.archived is True
    second_user = get_user_model().objects.create(username="learner2")

    changed = record_graded_object_statuses(user_id=second_user.id, scores=[_score(object_tag)])

    assert changed == 0
    assert not StudentCompetencyCriterionStatus.objects.filter(criterion=criterion, user_id=second_user.id).exists()
    resolved = StudentCompetencyCriterionStatus.objects.get(criterion=criterion, user_id=user.id).criterion.object_tag
    assert resolved.pk == object_tag.pk
    assert resolved.archived is True
