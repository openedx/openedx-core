"""
Tests for `roll_up_competency_statuses` above the competency level: a graded competency's parents.
"""
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest
from django.contrib.auth import get_user_model

from openedx_learning.api import roll_up_competency_statuses
from openedx_learning.applets.cbe import rollup
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    MasteryStatus,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriterionStatus,
    StudentCompetencyStatus,
)
from openedx_tagging.models import ObjectTag, Tag
from openedx_tagging.models.base import TAXONOMY_MAX_DEPTH

# The rollup refuses to run inside a transaction, so these tests cannot use the default rollback-per-test mode.
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("migration_seed_rows")]

DEMONSTRATED = MasteryStatus.DEMONSTRATED
PARTIAL = MasteryStatus.PARTIALLY_ATTEMPTED
NOT_DEMONSTRATED = MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_tag(taxonomy: CompetencyTaxonomy, value: str, parent: Tag | None = None) -> Tag:
    return Tag.objects.create(taxonomy=taxonomy, value=value, parent=parent)


def _object_id(name: str) -> str:
    """Return the block id of the problem called ``name``."""
    return f"block-v1:Org1+Python100+Fall2026+problem+{name}"


def _add_requirement(
    tag: Tag, object_id: str, profile: CompetencyRuleProfile, *, archived: bool = False,
) -> SimpleNamespace:
    """Give ``tag`` a root group with one criterion on ``object_id``, both archived when ``archived`` is set."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag, archived=archived)
    object_tag = ObjectTag.objects.create(object_id=object_id, taxonomy=tag.taxonomy, tag=tag)
    criterion = CompetencyCriterion.objects.create(
        group=root, object_tag=object_tag, rule_profile=profile, archived=archived,
    )
    return SimpleNamespace(root=root, criterion=criterion, object_id=object_id)


def _add_nested_requirement(
    tag: Tag, object_id: str, profile: CompetencyRuleProfile, *, levels: int,
) -> SimpleNamespace:
    """Give ``tag`` a root group and one criterion on ``object_id`` that sits ``levels`` groups below the root."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    group = root
    for _ in range(levels):
        group = CompetencyCriteriaGroup.objects.create(tag=tag, parent=group)
    object_tag = ObjectTag.objects.create(object_id=object_id, taxonomy=tag.taxonomy, tag=tag)
    criterion = CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=profile)
    return SimpleNamespace(root=root, group=group, criterion=criterion, object_id=object_id)


def _record(user, requirement: SimpleNamespace, status: MasteryStatus) -> None:
    """Store the learner's status on the requirement's criterion as the grade transaction would have."""
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=requirement.criterion, status_id=status, created=NOW, modified=NOW,
    )


def _set_group_status(user, group: CompetencyCriteriaGroup, status: MasteryStatus) -> None:
    """Store a group status as an earlier rollup would have."""
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=status, created=NOW, modified=NOW,
    )


def _set_competency_status(user, tag: Tag, status: MasteryStatus) -> None:
    """Store a competency status as an earlier rollup would have."""
    StudentCompetencyStatus.objects.create(user=user, tag=tag, status_id=status, created=NOW, modified=NOW)


def _competency_status(user, tag: Tag) -> int | None:
    row = StudentCompetencyStatus.objects.filter(user=user, tag=tag).first()
    return None if row is None else row.status_id


def _roll_up(user, *object_ids: str) -> None:
    roll_up_competency_statuses(user_id=user.id, object_ids=list(object_ids))


@pytest.fixture(name="family")
def _family(competency_taxonomy: CompetencyTaxonomy) -> SimpleNamespace:
    """A grandparent, a parent, and three sibling sub-competencies under the parent."""
    grandparent = _make_tag(competency_taxonomy, "Grandparent")
    parent = _make_tag(competency_taxonomy, "Parent", grandparent)
    children = [_make_tag(competency_taxonomy, f"Child {n}", parent) for n in (1, 2, 3)]
    return SimpleNamespace(grandparent=grandparent, parent=parent, children=children)


def test_demonstrating_the_only_sub_competency_raises_the_parent_and_grandparent(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    grandparent = _make_tag(competency_taxonomy, "Grandparent")
    parent = _make_tag(competency_taxonomy, "Parent", grandparent)
    child = _make_tag(competency_taxonomy, "Child", parent)
    requirement = _add_requirement(child, _object_id("c"), default_rule_profile)
    _record(user, requirement, DEMONSTRATED)

    _roll_up(user, requirement.object_id)

    assert [_competency_status(user, tag) for tag in (child, parent, grandparent)] == [DEMONSTRATED] * 3


def test_a_parent_whose_own_requirements_are_met_is_demonstrated_with_a_sub_competency_outstanding(
    user, family, default_rule_profile,
) -> None:
    requirement = _add_requirement(family.children[0], _object_id("c1"), default_rule_profile)
    _record(user, requirement, NOT_DEMONSTRATED)
    parent_requirement = _add_requirement(family.parent, _object_id("p"), default_rule_profile)
    _set_group_status(user, parent_requirement.root, DEMONSTRATED)

    _roll_up(user, requirement.object_id)

    assert _competency_status(user, family.parent) == DEMONSTRATED


def test_a_parent_is_demonstrated_when_every_sub_competency_is(user, family, default_rule_profile) -> None:
    """The parent has its own requirements, which this learner has not attempted."""
    _add_requirement(family.parent, _object_id("p"), default_rule_profile)
    requirements = [
        _add_requirement(child, _object_id(f"c{index}"), default_rule_profile)
        for index, child in enumerate(family.children)
    ]
    for requirement in requirements:
        _record(user, requirement, DEMONSTRATED)

    _roll_up(user, *(requirement.object_id for requirement in requirements))

    assert _competency_status(user, family.parent) == DEMONSTRATED


def test_a_parent_with_progress_from_either_source_is_partially_attempted(user, family, default_rule_profile) -> None:
    """The parent has its own requirements, which this learner has not attempted."""
    _add_requirement(family.parent, _object_id("p"), default_rule_profile)
    requirement = _add_requirement(family.children[0], _object_id("c1"), default_rule_profile)
    _record(user, requirement, DEMONSTRATED)
    _add_requirement(family.children[1], _object_id("c2"), default_rule_profile)

    _roll_up(user, requirement.object_id)

    assert _competency_status(user, family.parent) == PARTIAL


def test_a_failed_own_root_group_shows_progress_on_the_parent(user, family, default_rule_profile) -> None:
    requirement = _add_requirement(family.children[0], _object_id("c1"), default_rule_profile)
    _record(user, requirement, NOT_DEMONSTRATED)
    parent_requirement = _add_requirement(family.parent, _object_id("p"), default_rule_profile)
    _set_group_status(user, parent_requirement.root, NOT_DEMONSTRATED)

    _roll_up(user, requirement.object_id)

    assert _competency_status(user, family.parent) == PARTIAL


def test_sub_competencies_without_criteria_do_not_block_a_parent_with_its_own_requirements(
    user, family, default_rule_profile,
) -> None:
    requirement = _add_requirement(family.children[0], _object_id("c1"), default_rule_profile)
    _record(user, requirement, DEMONSTRATED)
    parent_requirement = _add_requirement(family.parent, _object_id("p"), default_rule_profile)
    _set_group_status(user, parent_requirement.root, DEMONSTRATED)

    _roll_up(user, requirement.object_id)

    assert _competency_status(user, family.parent) == DEMONSTRATED


def test_a_sub_competency_without_criteria_caps_a_parent_that_has_no_requirements_of_its_own(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    parent = _make_tag(competency_taxonomy, "Parent")
    child_with_requirement = _make_tag(competency_taxonomy, "With requirement", parent)
    _make_tag(competency_taxonomy, "Without requirement", parent)
    requirement = _add_requirement(child_with_requirement, _object_id("c"), default_rule_profile)
    _record(user, requirement, DEMONSTRATED)

    _roll_up(user, requirement.object_id)

    assert _competency_status(user, parent) == PARTIAL


def test_a_sub_competency_with_criteria_only_further_down_still_participates(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    parent = _make_tag(competency_taxonomy, "Parent")
    middle = _make_tag(competency_taxonomy, "Middle", parent)
    leaf = _make_tag(competency_taxonomy, "Leaf", middle)
    requirement = _add_requirement(leaf, _object_id("c"), default_rule_profile)
    _record(user, requirement, DEMONSTRATED)

    _roll_up(user, requirement.object_id)

    assert _competency_status(user, leaf) == DEMONSTRATED
    assert _competency_status(user, middle) == DEMONSTRATED
    assert _competency_status(user, parent) == DEMONSTRATED


def test_a_stored_demonstrated_parent_is_not_lowered_when_only_some_sub_competencies_are_attempted(
    user, family, default_rule_profile,
) -> None:
    StudentCompetencyStatus.objects.create(
        user=user, tag=family.parent, status_id=DEMONSTRATED, created=NOW, modified=NOW,
    )
    requirement = _add_requirement(family.children[0], _object_id("c1"), default_rule_profile)
    _record(user, requirement, DEMONSTRATED)

    _roll_up(user, requirement.object_id)

    assert _competency_status(user, family.parent) == DEMONSTRATED


def test_a_tag_at_the_taxonomy_root_ends_the_walk(user, competency_taxonomy, default_rule_profile) -> None:
    root_tag = _make_tag(competency_taxonomy, "Root")
    requirement = _add_requirement(root_tag, _object_id("r"), default_rule_profile)
    _record(user, requirement, DEMONSTRATED)

    _roll_up(user, requirement.object_id)

    assert list(StudentCompetencyStatus.objects.values_list("tag_id", flat=True)) == [root_tag.pk]


def test_the_deepest_allowed_taxonomy_is_walked_to_the_top(user, competency_taxonomy, default_rule_profile) -> None:
    tags = [_make_tag(competency_taxonomy, "Level 0")]
    for depth in range(1, 6):
        tags.append(_make_tag(competency_taxonomy, f"Level {depth}", tags[-1]))
    requirement = _add_requirement(tags[-1], _object_id("deep"), default_rule_profile)
    _record(user, requirement, DEMONSTRATED)

    _roll_up(user, requirement.object_id)

    assert _competency_status(user, tags[0]) == DEMONSTRATED
    assert _competency_status(user, tags[1]) == DEMONSTRATED
    assert _competency_status(user, tags[2]) == DEMONSTRATED
    assert _competency_status(user, tags[3]) == DEMONSTRATED
    assert _competency_status(user, tags[4]) == DEMONSTRATED
    assert _competency_status(user, tags[5]) == DEMONSTRATED


def test_a_taxonomy_deeper_than_the_cap_logs_the_parents_left_unrecomputed(
    user, competency_taxonomy, default_rule_profile, caplog: pytest.LogCaptureFixture,
) -> None:
    """A tree deeper than the cap is walked as far as the cap allows, and the parents left out are logged."""
    tags = [_make_tag(competency_taxonomy, "Level 0")]
    for depth in range(1, TAXONOMY_MAX_DEPTH + 2):
        tags.append(_make_tag(competency_taxonomy, f"Level {depth}"))
        # Tag.save() rejects a tag deeper than the cap, so attach each level with a raw UPDATE.
        Tag.objects.filter(pk=tags[-1].pk).update(
            parent=tags[-2], depth=depth, lineage=tags[-2].lineage + tags[-1].value + "\t",
        )
        tags[-1].refresh_from_db()
    requirement = _add_requirement(tags[-1], _object_id("too-deep"), default_rule_profile)
    _record(user, requirement, DEMONSTRATED)

    with caplog.at_level(logging.ERROR, logger=rollup.__name__):
        _roll_up(user, requirement.object_id)

    assert _competency_status(user, tags[1]) == DEMONSTRATED
    assert _competency_status(user, tags[0]) is None
    assert [record.getMessage() for record in caplog.records] == [
        f"Competency rollup for user {user.id} stopped at the taxonomy depth cap; "
        f"parent tags {[tags[0].pk]} were not recomputed",
    ]


def test_two_graded_competencies_sharing_a_parent_recompute_that_parent_once(
    user, family, default_rule_profile,
) -> None:
    requirements = [
        _add_requirement(child, _object_id(f"c{index}"), default_rule_profile)
        for index, child in enumerate(family.children[:2])
    ]
    for requirement in requirements:
        _record(user, requirement, DEMONSTRATED)

    with mock.patch.object(
        rollup, "combine_parent_competency_status", wraps=rollup.combine_parent_competency_status,
    ) as combine:
        _roll_up(user, *(requirement.object_id for requirement in requirements))

    assert combine.call_count == 2  # the parent once, then the grandparent once


def test_parents_at_the_same_level_are_rolled_up_independently(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    parent_one = _make_tag(competency_taxonomy, "Parent 1")
    only_child = _make_tag(competency_taxonomy, "Only child", parent_one)
    parent_two = _make_tag(competency_taxonomy, "Parent 2")
    first = _make_tag(competency_taxonomy, "First", parent_two)
    second = _make_tag(competency_taxonomy, "Second", parent_two)
    graded = [
        _add_requirement(only_child, _object_id("only"), default_rule_profile),
        _add_requirement(first, _object_id("first"), default_rule_profile),
    ]
    _add_requirement(second, _object_id("second"), default_rule_profile)
    for requirement in graded:
        _record(user, requirement, DEMONSTRATED)

    _roll_up(user, *(requirement.object_id for requirement in graded))

    assert _competency_status(user, parent_one) == DEMONSTRATED
    assert _competency_status(user, parent_two) == PARTIAL


def test_competencies_graded_at_different_depths_converge_on_the_shared_ancestor(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    top = _make_tag(competency_taxonomy, "Top")
    middle = _make_tag(competency_taxonomy, "Middle", top)
    deep = _make_tag(competency_taxonomy, "Deep", middle)
    shallow = _make_tag(competency_taxonomy, "Shallow", top)
    graded = [
        _add_requirement(deep, _object_id("deep"), default_rule_profile),
        _add_requirement(shallow, _object_id("shallow"), default_rule_profile),
    ]
    for requirement in graded:
        _record(user, requirement, DEMONSTRATED)

    _roll_up(user, *(requirement.object_id for requirement in graded))

    assert _competency_status(user, deep) == DEMONSTRATED
    assert _competency_status(user, middle) == DEMONSTRATED
    assert _competency_status(user, shallow) == DEMONSTRATED
    assert _competency_status(user, top) == DEMONSTRATED


def test_a_parent_with_its_own_requirements_is_demonstrated_when_a_sub_competency_is_fully_archived(
    user, family, default_rule_profile,
) -> None:
    """The parent is demonstrated through its own requirement, whatever state its sub-competencies are in."""
    archived_child, graded_child, _child_without_requirements = family.children
    parent_requirement = _add_requirement(family.parent, _object_id("p"), default_rule_profile)
    _set_group_status(user, parent_requirement.root, DEMONSTRATED)
    _add_requirement(archived_child, _object_id("c1"), default_rule_profile, archived=True)
    graded_requirement = _add_requirement(graded_child, _object_id("c2"), default_rule_profile)
    _record(user, graded_requirement, DEMONSTRATED)

    _roll_up(user, graded_requirement.object_id)

    assert _competency_status(user, graded_child) == DEMONSTRATED
    assert _competency_status(user, family.parent) == DEMONSTRATED


def test_a_parent_with_no_requirements_of_its_own_stays_short_when_a_sub_competency_is_fully_archived(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    """The archived sub-competency can never be demonstrated, so the parent cannot have every sub-competency."""
    parent = _make_tag(competency_taxonomy, "Parent")
    archived_child = _make_tag(competency_taxonomy, "Archived", parent)
    graded_child = _make_tag(competency_taxonomy, "Graded", parent)
    _add_requirement(archived_child, _object_id("a"), default_rule_profile, archived=True)
    graded_requirement = _add_requirement(graded_child, _object_id("g"), default_rule_profile)
    _record(user, graded_requirement, DEMONSTRATED)

    _roll_up(user, graded_requirement.object_id)

    assert _competency_status(user, graded_child) == DEMONSTRATED
    assert _competency_status(user, parent) == PARTIAL


def test_an_archived_root_group_still_starts_the_parent_competency_walk(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    """A rollup for an archived requirement writes nothing for its own competency but still recomputes the parent."""
    parent = _make_tag(competency_taxonomy, "Parent")
    archived_child = _make_tag(competency_taxonomy, "Archived", parent)
    demonstrated_child = _make_tag(competency_taxonomy, "Demonstrated earlier", parent)
    _set_competency_status(user, demonstrated_child, DEMONSTRATED)
    archived_requirement = _add_requirement(archived_child, _object_id("a"), default_rule_profile, archived=True)
    _record(user, archived_requirement, DEMONSTRATED)

    _roll_up(user, archived_requirement.object_id)

    assert _competency_status(user, archived_child) is None
    assert _competency_status(user, parent) == PARTIAL


def test_a_parents_own_archived_root_still_counts_through_its_stored_status(
    user, family, default_rule_profile,
) -> None:
    """The parent demonstrated its own requirement before that requirement was archived, and keeps the result."""
    graded_child = family.children[0]
    parent_requirement = _add_requirement(family.parent, _object_id("p"), default_rule_profile, archived=True)
    _set_group_status(user, parent_requirement.root, DEMONSTRATED)
    graded_requirement = _add_requirement(graded_child, _object_id("c1"), default_rule_profile)
    _record(user, graded_requirement, NOT_DEMONSTRATED)

    _roll_up(user, graded_requirement.object_id)

    assert _competency_status(user, graded_child) == PARTIAL
    assert _competency_status(user, family.parent) == DEMONSTRATED


def test_a_parent_whose_only_sub_competency_has_no_status_gets_no_status(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    """The parent has no requirement of its own and no sub-competency with a status, so there is nothing to write."""
    parent = _make_tag(competency_taxonomy, "Parent")
    archived_child = _make_tag(competency_taxonomy, "Archived", parent)
    archived_requirement = _add_requirement(archived_child, _object_id("a"), default_rule_profile, archived=True)
    _record(user, archived_requirement, DEMONSTRATED)

    _roll_up(user, archived_requirement.object_id)

    assert _competency_status(user, archived_child) is None
    assert _competency_status(user, parent) is None


def test_root_groups_reached_at_different_depths_each_start_their_own_parent_walk(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    """The shallow root is reached on the first level of the group walk and the deep root two levels later."""
    shallow_parent = _make_tag(competency_taxonomy, "Shallow parent")
    shallow = _make_tag(competency_taxonomy, "Shallow", shallow_parent)
    deep_parent = _make_tag(competency_taxonomy, "Deep parent")
    deep = _make_tag(competency_taxonomy, "Deep", deep_parent)
    graded = [
        _add_requirement(shallow, _object_id("shallow"), default_rule_profile),
        _add_nested_requirement(deep, _object_id("deep"), default_rule_profile, levels=2),
    ]
    for requirement in graded:
        _record(user, requirement, DEMONSTRATED)

    _roll_up(user, *(requirement.object_id for requirement in graded))

    assert _competency_status(user, shallow_parent) == DEMONSTRATED
    assert _competency_status(user, deep_parent) == DEMONSTRATED


def test_another_learners_statuses_do_not_count_toward_the_rolled_up_learner(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    """The other learner holds a status at every level that the rollup reads: criterion, root group, competency."""
    other_learner = get_user_model().objects.create(username="other_learner")
    parent = _make_tag(competency_taxonomy, "Parent")
    graded_child = _make_tag(competency_taxonomy, "Graded", parent)
    other_child = _make_tag(competency_taxonomy, "Other", parent)
    parent_requirement = _add_requirement(parent, _object_id("p"), default_rule_profile)
    graded_requirement = _add_requirement(graded_child, _object_id("graded"), default_rule_profile)
    other_requirement = _add_requirement(other_child, _object_id("other"), default_rule_profile)
    _record(user, graded_requirement, DEMONSTRATED)
    _record(other_learner, other_requirement, DEMONSTRATED)
    _set_group_status(other_learner, parent_requirement.root, DEMONSTRATED)
    _set_competency_status(other_learner, other_child, DEMONSTRATED)

    _roll_up(user, graded_requirement.object_id, other_requirement.object_id)

    assert _competency_status(user, graded_child) == DEMONSTRATED
    assert _competency_status(user, other_child) is None
    assert _competency_status(user, parent) == PARTIAL
    assert _competency_status(other_learner, parent) is None


def test_a_repeated_rollup_of_nested_groups_under_a_parent_competency_runs_a_fixed_number_of_queries(
    user, competency_taxonomy, default_rule_profile, django_assert_num_queries,
) -> None:
    """The first rollup creates every status row, so the repeated one only reads and compares."""
    parent = _make_tag(competency_taxonomy, "Parent")
    child = _make_tag(competency_taxonomy, "Child", parent)
    requirement = _add_nested_requirement(child, _object_id("c"), default_rule_profile, levels=1)
    _record(user, requirement, DEMONSTRATED)
    _roll_up(user, requirement.object_id)

    with django_assert_num_queries(22):
        _roll_up(user, requirement.object_id)


def test_a_nested_group_in_a_parents_own_tree_does_not_count_as_the_parents_own_result(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    """Only a root group's status is the parent's own result, and this learner has none on the parent's root."""
    parent = _make_tag(competency_taxonomy, "Parent")
    child = _make_tag(competency_taxonomy, "Child", parent)
    parent_requirement = _add_nested_requirement(parent, _object_id("p"), default_rule_profile, levels=1)
    _set_group_status(user, parent_requirement.group, DEMONSTRATED)
    child_requirement = _add_requirement(child, _object_id("c"), default_rule_profile)
    _record(user, child_requirement, NOT_DEMONSTRATED)

    _roll_up(user, child_requirement.object_id)

    assert _competency_status(user, child) == PARTIAL
    assert _competency_status(user, parent) == PARTIAL
