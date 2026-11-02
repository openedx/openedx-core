"""
Tests for `roll_up_competency_statuses` below the competency level: criterion statuses up through the
criteria groups to the competency a root group belongs to.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.transaction import TransactionManagementError
from django.test.utils import CaptureQueriesContext
from freezegun import freeze_time

from openedx_learning.api import roll_up_competency_statuses
from openedx_learning.applets.cbe import rollup
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    LogicOperator,
    MasteryStatus,
    RuleType,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriterionStatus,
    StudentCompetencyStatus,
)
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

# The rollup refuses to run inside a transaction, so these tests cannot use the default rollback-per-test mode.
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("migration_seed_rows")]

DEMONSTRATED = MasteryStatus.DEMONSTRATED
PARTIAL = MasteryStatus.PARTIALLY_ATTEMPTED
FAILED = MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ASSIGNMENT_1 = "block-v1:Org1+Python100+Fall2026+problem+a1"
ASSIGNMENT_2 = "block-v1:Org1+Python100+Fall2026+problem+a2"


def _make_group(tag: Tag, *, parent=None, logic_operator=None) -> CompetencyCriteriaGroup:
    """Create a criteria group; a null operator is how real root and course-level groups are stored."""
    return CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent, logic_operator=logic_operator)


def _make_criterion(
    group: CompetencyCriteriaGroup, object_id: str, profile: CompetencyRuleProfile,
) -> CompetencyCriterion:
    """Create a criterion on ``group`` for ``object_id``, tagging the object with the group's competency."""
    object_tag = ObjectTag.objects.create(object_id=object_id, taxonomy=group.tag.taxonomy, tag=group.tag)
    return CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=profile)


def _record(user, criterion: CompetencyCriterion, status: MasteryStatus) -> None:
    """Store a leaf status as the grade transaction would have."""
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=status, created=NOW, modified=NOW,
    )


def _group_status(user, group: CompetencyCriteriaGroup) -> int | None:
    row = StudentCompetencyCriteriaGroupStatus.objects.filter(user=user, group=group).first()
    return None if row is None else row.status_id


def _competency_status(user, tag: Tag) -> int | None:
    row = StudentCompetencyStatus.objects.filter(user=user, tag=tag).first()
    return None if row is None else row.status_id


@pytest.fixture(name="and_tree")
def _and_tree(tag: Tag, default_rule_profile: CompetencyRuleProfile) -> SimpleNamespace:
    """A competency whose root has one leaf group requiring two assignments together."""
    root = _make_group(tag)
    leaf = _make_group(tag, parent=root, logic_operator=LogicOperator.AND)
    return SimpleNamespace(
        root=root,
        leaf=leaf,
        first=_make_criterion(leaf, ASSIGNMENT_1, default_rule_profile),
        second=_make_criterion(leaf, ASSIGNMENT_2, default_rule_profile),
    )


@pytest.fixture(name="or_tree")
def _or_tree(tag: Tag, default_rule_profile: CompetencyRuleProfile) -> SimpleNamespace:
    """A competency with two alternative routes, each a leaf group holding one assignment."""
    root = _make_group(tag, logic_operator=LogicOperator.OR)
    route_1 = _make_group(tag, parent=root, logic_operator=LogicOperator.AND)
    route_2 = _make_group(tag, parent=root, logic_operator=LogicOperator.AND)
    return SimpleNamespace(
        root=root,
        route_1=route_1,
        route_2=route_2,
        first=_make_criterion(route_1, ASSIGNMENT_1, default_rule_profile),
        second=_make_criterion(route_2, ASSIGNMENT_2, default_rule_profile),
    )


def _roll_up(user, *object_ids: str) -> None:
    roll_up_competency_statuses(user_id=user.id, object_ids=list(object_ids))


def test_completing_the_last_requirement_demonstrates_the_competency(user, tag, and_tree) -> None:
    _record(user, and_tree.first, DEMONSTRATED)
    _record(user, and_tree.second, DEMONSTRATED)

    _roll_up(user, ASSIGNMENT_2)

    assert _group_status(user, and_tree.leaf) == DEMONSTRATED
    assert _group_status(user, and_tree.root) == DEMONSTRATED
    assert _competency_status(user, tag) == DEMONSTRATED


def test_completing_one_of_several_requirements_shows_progress(user, tag, and_tree) -> None:
    _record(user, and_tree.first, DEMONSTRATED)

    _roll_up(user, ASSIGNMENT_1)

    assert _group_status(user, and_tree.leaf) == PARTIAL
    assert _competency_status(user, tag) == PARTIAL


def test_failing_one_of_two_attempted_requirements_shows_the_failure_on_the_group(user, tag, and_tree) -> None:
    """Holds when both grades are recorded before the first rollup; see the next test for the other order."""
    _record(user, and_tree.first, DEMONSTRATED)
    _record(user, and_tree.second, FAILED)

    _roll_up(user, ASSIGNMENT_1, ASSIGNMENT_2)

    assert _group_status(user, and_tree.leaf) == FAILED
    assert _competency_status(user, tag) == PARTIAL


def test_a_group_stays_partially_attempted_once_stored_even_when_the_last_requirement_fails(
    user, tag, and_tree,
) -> None:
    """Raise-only writes discard the failure computed on the second rollup, because it ranks below what is stored."""
    _record(user, and_tree.first, DEMONSTRATED)
    _roll_up(user, ASSIGNMENT_1)
    _record(user, and_tree.second, FAILED)

    _roll_up(user, ASSIGNMENT_2)

    assert _group_status(user, and_tree.leaf) == PARTIAL
    assert _competency_status(user, tag) == PARTIAL


def test_either_alternative_route_demonstrates_the_competency(user, tag, or_tree) -> None:
    _record(user, or_tree.first, DEMONSTRATED)

    _roll_up(user, ASSIGNMENT_1)

    assert _group_status(user, or_tree.route_1) == DEMONSTRATED
    assert _group_status(user, or_tree.route_2) is None
    assert _competency_status(user, tag) == DEMONSTRATED


def test_attempting_some_routes_without_succeeding_shows_progress(user, tag, or_tree) -> None:
    _record(user, or_tree.first, FAILED)

    _roll_up(user, ASSIGNMENT_1)

    assert _group_status(user, or_tree.root) == PARTIAL
    assert _competency_status(user, tag) == PARTIAL


def test_failing_every_route_shows_the_failure_on_the_root_but_not_on_the_competency(user, tag, or_tree) -> None:
    """Holds when both grades are recorded before the first rollup, as in the AND-group test above."""
    _record(user, or_tree.first, FAILED)
    _record(user, or_tree.second, FAILED)

    _roll_up(user, ASSIGNMENT_1, ASSIGNMENT_2)

    assert _group_status(user, or_tree.root) == FAILED
    assert _competency_status(user, tag) == PARTIAL


def test_failing_every_route_one_rollup_at_a_time_leaves_the_root_partially_attempted(user, tag, or_tree) -> None:
    """Pins the raise-only behavior for the other grading order: the stored PARTIAL is never replaced by FAILED."""
    _record(user, or_tree.first, FAILED)
    _roll_up(user, ASSIGNMENT_1)
    _record(user, or_tree.second, FAILED)
    _roll_up(user, ASSIGNMENT_2)

    assert _group_status(user, or_tree.route_1) == FAILED
    assert _group_status(user, or_tree.route_2) == FAILED
    assert _group_status(user, or_tree.root) == PARTIAL
    assert _competency_status(user, tag) == PARTIAL


def test_a_group_with_nothing_attempted_has_no_status(user, tag, and_tree) -> None:
    _roll_up(user, ASSIGNMENT_1, ASSIGNMENT_2)

    assert _group_status(user, and_tree.leaf) is None
    assert _group_status(user, and_tree.root) is None
    assert _competency_status(user, tag) is None


def test_a_root_group_authored_as_and_still_evaluates(user, tag, default_rule_profile) -> None:
    root = _make_group(tag, logic_operator=LogicOperator.AND)
    first = _make_criterion(root, ASSIGNMENT_1, default_rule_profile)
    second = _make_criterion(root, ASSIGNMENT_2, default_rule_profile)
    _record(user, first, DEMONSTRATED)
    _record(user, second, DEMONSTRATED)

    _roll_up(user, ASSIGNMENT_1)

    assert _competency_status(user, tag) == DEMONSTRATED


def test_nested_groups_roll_up_level_by_level(user, tag, default_rule_profile) -> None:
    root = _make_group(tag)
    course_level = _make_group(tag, parent=root)
    leaf = _make_group(tag, parent=course_level, logic_operator=LogicOperator.AND)
    first = _make_criterion(leaf, ASSIGNMENT_1, default_rule_profile)
    second = _make_criterion(leaf, ASSIGNMENT_2, default_rule_profile)
    _record(user, first, DEMONSTRATED)
    _record(user, second, DEMONSTRATED)

    _roll_up(user, ASSIGNMENT_1, ASSIGNMENT_2)

    assert [_group_status(user, group) for group in (leaf, course_level, root)] == [DEMONSTRATED] * 3
    assert _competency_status(user, tag) == DEMONSTRATED


def test_two_grades_recorded_one_after_the_other_both_count(user, tag, and_tree) -> None:
    """Each writer sees only its own assignment finished; the later rollup reads both and gets it right."""
    _record(user, and_tree.first, DEMONSTRATED)
    _roll_up(user, ASSIGNMENT_1)
    assert _competency_status(user, tag) == PARTIAL

    _record(user, and_tree.second, DEMONSTRATED)
    _roll_up(user, ASSIGNMENT_2)

    assert _group_status(user, and_tree.leaf) == DEMONSTRATED
    assert _competency_status(user, tag) == DEMONSTRATED


def test_two_grades_recorded_in_the_opposite_order_both_count(user, tag, and_tree) -> None:
    """The result does not depend on which assignment is graded first."""
    _record(user, and_tree.second, DEMONSTRATED)
    _roll_up(user, ASSIGNMENT_2)
    assert _group_status(user, and_tree.leaf) == PARTIAL
    assert _competency_status(user, tag) == PARTIAL

    _record(user, and_tree.first, DEMONSTRATED)
    _roll_up(user, ASSIGNMENT_1)

    assert _group_status(user, and_tree.leaf) == DEMONSTRATED
    assert _group_status(user, and_tree.root) == DEMONSTRATED
    assert _competency_status(user, tag) == DEMONSTRATED


def test_a_stale_recomputation_never_sets_a_learner_back(user, tag, and_tree) -> None:
    _record(user, and_tree.first, DEMONSTRATED)
    _record(user, and_tree.second, DEMONSTRATED)
    _roll_up(user, ASSIGNMENT_1, ASSIGNMENT_2)
    StudentCompetencyCriterionStatus.objects.filter(criterion=and_tree.second).delete()

    _roll_up(user, ASSIGNMENT_1)

    assert _group_status(user, and_tree.leaf) == DEMONSTRATED
    assert _competency_status(user, tag) == DEMONSTRATED


_StatusRows = list[tuple[int, int, datetime]]


def _status_tables() -> tuple[_StatusRows, _StatusRows]:
    """Snapshot both rollup tables so a test can prove a rollup wrote nothing."""
    return (
        list(StudentCompetencyCriteriaGroupStatus.objects.order_by("pk").values_list("pk", "status_id", "modified")),
        list(StudentCompetencyStatus.objects.order_by("pk").values_list("pk", "status_id", "modified")),
    )


def test_repeating_a_recomputation_changes_nothing(user, and_tree) -> None:
    _record(user, and_tree.first, DEMONSTRATED)
    _record(user, and_tree.second, DEMONSTRATED)
    _roll_up(user, ASSIGNMENT_1, ASSIGNMENT_2)
    before = _status_tables()

    _roll_up(user, ASSIGNMENT_1, ASSIGNMENT_2)

    assert _status_tables() == before


def test_a_recomputation_that_fails_partway_reaches_the_same_result_on_retry(user, tag, and_tree) -> None:
    _record(user, and_tree.first, DEMONSTRATED)
    _record(user, and_tree.second, DEMONSTRATED)
    real_raise_status = rollup.raise_status
    calls = []

    def fail_on_second_write(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise DatabaseError("simulated failure between two levels")
        return real_raise_status(*args, **kwargs)

    with mock.patch.object(rollup, "raise_status", side_effect=fail_on_second_write):
        with pytest.raises(DatabaseError):
            _roll_up(user, ASSIGNMENT_1, ASSIGNMENT_2)
    assert _competency_status(user, tag) is None

    _roll_up(user, ASSIGNMENT_1, ASSIGNMENT_2)

    assert _group_status(user, and_tree.leaf) == DEMONSTRATED
    assert _competency_status(user, tag) == DEMONSTRATED


def test_a_level_whose_value_does_not_change_keeps_its_last_changed_time(user, and_tree) -> None:
    _record(user, and_tree.first, DEMONSTRATED)
    with freeze_time("2026-02-01"):
        _roll_up(user, ASSIGNMENT_1)
    before = _status_tables()

    with freeze_time("2026-03-01"):
        _roll_up(user, ASSIGNMENT_1)

    assert _status_tables() == before
    assert StudentCompetencyCriteriaGroupStatus.objects.get(group=and_tree.leaf).modified == datetime(
        2026, 2, 1, tzinfo=timezone.utc,
    )


def test_the_walk_continues_upward_past_a_group_whose_stored_status_is_already_higher(user, tag, and_tree) -> None:
    _record(user, and_tree.first, DEMONSTRATED)
    _record(user, and_tree.second, DEMONSTRATED)
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=and_tree.leaf, status_id=DEMONSTRATED, created=NOW, modified=NOW,
    )

    _roll_up(user, ASSIGNMENT_1)

    assert _group_status(user, and_tree.root) == DEMONSTRATED
    assert _competency_status(user, tag) == DEMONSTRATED


def test_one_object_backing_criteria_in_two_groups_computes_each_group_once(user, tag, default_rule_profile) -> None:
    root = _make_group(tag)
    group_1 = _make_group(tag, parent=root, logic_operator=LogicOperator.AND)
    group_2 = _make_group(tag, parent=root, logic_operator=LogicOperator.AND)
    object_tag = ObjectTag.objects.create(object_id=ASSIGNMENT_1, taxonomy=tag.taxonomy, tag=tag)
    criteria = [
        CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=default_rule_profile)
        for group in (group_1, group_2)
    ]
    for criterion in criteria:
        _record(user, criterion, DEMONSTRATED)

    with mock.patch.object(rollup, "combine_children", wraps=rollup.combine_children) as combine:
        _roll_up(user, ASSIGNMENT_1)

    assert combine.call_count == 3  # group_1, group_2, then the shared root once
    assert _competency_status(user, tag) == DEMONSTRATED


@pytest.mark.parametrize("object_ids", [[], ["block-v1:Org1+Nothing+Fall2026+problem+x"], [ASSIGNMENT_1, ASSIGNMENT_1]])
@pytest.mark.usefixtures("tag")
def test_object_ids_with_no_criteria_do_nothing(user, object_ids) -> None:
    roll_up_competency_statuses(user_id=user.id, object_ids=object_ids)

    assert not StudentCompetencyCriteriaGroupStatus.objects.exists()
    assert not StudentCompetencyStatus.objects.exists()


def test_an_empty_object_list_runs_no_queries(user, django_assert_num_queries) -> None:
    with django_assert_num_queries(0):
        roll_up_competency_statuses(user_id=user.id, object_ids=[])


def test_a_criterion_tagged_outside_a_competency_taxonomy_is_not_rolled_up(user, tag, default_rule_profile) -> None:
    plain_taxonomy = Taxonomy.objects.create(name="Plain")
    plain_tag = Tag.objects.create(taxonomy=plain_taxonomy, value="Plain tag")
    root = _make_group(tag)
    object_tag = ObjectTag.objects.create(object_id=ASSIGNMENT_1, taxonomy=plain_taxonomy, tag=plain_tag)
    criterion = CompetencyCriterion.objects.create(group=root, object_tag=object_tag, rule_profile=default_rule_profile)
    _record(user, criterion, DEMONSTRATED)

    _roll_up(user, ASSIGNMENT_1)

    assert not StudentCompetencyCriteriaGroupStatus.objects.exists()


def test_a_storage_error_propagates_so_the_caller_can_retry(user, and_tree) -> None:
    _record(user, and_tree.first, DEMONSTRATED)

    with mock.patch.object(rollup, "raise_status", side_effect=DatabaseError("down")):
        with pytest.raises(DatabaseError):
            _roll_up(user, ASSIGNMENT_1)


def test_every_write_runs_outside_any_enclosing_transaction(user, competency_taxonomy) -> None:
    """Each level must commit before the next is read, so the walk may not wrap itself in a transaction."""
    parent = Tag.objects.create(taxonomy=competency_taxonomy, value="Parent")
    child = Tag.objects.create(taxonomy=competency_taxonomy, value="Child", parent=parent)
    root = CompetencyCriteriaGroup.objects.create(tag=child)
    object_tag = ObjectTag.objects.create(object_id=ASSIGNMENT_1, taxonomy=competency_taxonomy, tag=child)
    criterion = CompetencyCriterion.objects.create(
        group=root,
        object_tag=object_tag,
        rule_type_override=RuleType.GRADE,
        rule_payload_override={"op": "gte", "value": 0.8, "scale": "percent"},
    )
    _record(user, criterion, DEMONSTRATED)
    real_raise_status = rollup.raise_status
    atomic_at_write = []

    def observe(*args, **kwargs):
        atomic_at_write.append(connection.in_atomic_block)
        return real_raise_status(*args, **kwargs)

    with mock.patch.object(rollup, "raise_status", side_effect=observe), CaptureQueriesContext(connection) as queries:
        _roll_up(user, ASSIGNMENT_1)

    # Three writes: the root group, the child competency, and the parent competency in the second walk.
    assert len(atomic_at_write) == 3
    assert not any(atomic_at_write)
    # An enclosing transaction would turn get_or_create's own atomic() into a savepoint.
    assert not any("SAVEPOINT" in query["sql"] for query in queries)


def test_a_call_inside_a_transaction_raises(user) -> None:
    """Each level must commit before the next is read, so the walk refuses to run inside a transaction."""
    with pytest.raises(TransactionManagementError), transaction.atomic():
        _roll_up(user, ASSIGNMENT_1)
