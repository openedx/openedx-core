"""
Tests for `roll_up_competency_statuses` above the competency level: a graded competency's parents.
"""
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

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
FAILED = MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_tag(taxonomy: CompetencyTaxonomy, value: str, parent: Tag | None = None) -> Tag:
    return Tag.objects.create(taxonomy=taxonomy, value=value, parent=parent)


def _object_id(name: str) -> str:
    """Return the block id of the problem called ``name``."""
    return f"block-v1:Org1+Python100+Fall2026+problem+{name}"


def _earnable(tag: Tag, object_id: str, profile: CompetencyRuleProfile) -> SimpleNamespace:
    """Give ``tag`` a root group with one criterion on ``object_id``."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    object_tag = ObjectTag.objects.create(object_id=object_id, taxonomy=tag.taxonomy, tag=tag)
    criterion = CompetencyCriterion.objects.create(group=root, object_tag=object_tag, rule_profile=profile)
    return SimpleNamespace(root=root, criterion=criterion, object_id=object_id)


def _record(user, earnable: SimpleNamespace, status: MasteryStatus) -> None:
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=earnable.criterion, status_id=status, created=NOW, modified=NOW,
    )


def _set_group_status(user, group: CompetencyCriteriaGroup, status: MasteryStatus) -> None:
    """Store a group status as an earlier rollup would have."""
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=group, status_id=status, created=NOW, modified=NOW,
    )


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
    earnable = _earnable(child, _object_id("c"), default_rule_profile)
    _record(user, earnable, DEMONSTRATED)

    _roll_up(user, earnable.object_id)

    assert [_competency_status(user, tag) for tag in (child, parent, grandparent)] == [DEMONSTRATED] * 3


def test_a_parent_whose_own_requirements_are_met_is_demonstrated_with_a_sub_competency_outstanding(
    user, family, default_rule_profile,
) -> None:
    earnable = _earnable(family.children[0], _object_id("c1"), default_rule_profile)
    _record(user, earnable, FAILED)
    own = _earnable(family.parent, _object_id("p"), default_rule_profile)
    _set_group_status(user, own.root, DEMONSTRATED)

    _roll_up(user, earnable.object_id)

    assert _competency_status(user, family.parent) == DEMONSTRATED


def test_a_parent_is_demonstrated_when_every_sub_competency_is(user, family, default_rule_profile) -> None:
    """The parent has its own requirements, which this learner has not attempted."""
    _earnable(family.parent, _object_id("p"), default_rule_profile)
    earnables = [
        _earnable(child, _object_id(f"c{index}"), default_rule_profile)
        for index, child in enumerate(family.children)
    ]
    for earnable in earnables:
        _record(user, earnable, DEMONSTRATED)

    _roll_up(user, *(earnable.object_id for earnable in earnables))

    assert _competency_status(user, family.parent) == DEMONSTRATED


def test_a_parent_with_progress_from_either_source_is_partially_attempted(user, family, default_rule_profile) -> None:
    """The parent has its own requirements, which this learner has not attempted."""
    _earnable(family.parent, _object_id("p"), default_rule_profile)
    earnable = _earnable(family.children[0], _object_id("c1"), default_rule_profile)
    _record(user, earnable, DEMONSTRATED)
    _earnable(family.children[1], _object_id("c2"), default_rule_profile)

    _roll_up(user, earnable.object_id)

    assert _competency_status(user, family.parent) == PARTIAL


def test_a_failed_own_root_group_shows_progress_on_the_parent(user, family, default_rule_profile) -> None:
    earnable = _earnable(family.children[0], _object_id("c1"), default_rule_profile)
    _record(user, earnable, FAILED)
    own = _earnable(family.parent, _object_id("p"), default_rule_profile)
    _set_group_status(user, own.root, FAILED)

    _roll_up(user, earnable.object_id)

    assert _competency_status(user, family.parent) == PARTIAL


def test_sub_competencies_without_criteria_do_not_block_a_parent_with_its_own_requirements(
    user, family, default_rule_profile,
) -> None:
    earnable = _earnable(family.children[0], _object_id("c1"), default_rule_profile)
    _record(user, earnable, DEMONSTRATED)
    own = _earnable(family.parent, _object_id("p"), default_rule_profile)
    _set_group_status(user, own.root, DEMONSTRATED)

    _roll_up(user, earnable.object_id)

    assert _competency_status(user, family.parent) == DEMONSTRATED


def test_a_sub_competency_without_criteria_caps_a_parent_that_has_no_requirements_of_its_own(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    parent = _make_tag(competency_taxonomy, "Parent")
    earnable_child = _make_tag(competency_taxonomy, "Earnable", parent)
    _make_tag(competency_taxonomy, "Unearnable", parent)
    earnable = _earnable(earnable_child, _object_id("c"), default_rule_profile)
    _record(user, earnable, DEMONSTRATED)

    _roll_up(user, earnable.object_id)

    assert _competency_status(user, parent) == PARTIAL


def test_a_sub_competency_with_criteria_only_further_down_still_participates(
    user, competency_taxonomy, default_rule_profile,
) -> None:
    parent = _make_tag(competency_taxonomy, "Parent")
    middle = _make_tag(competency_taxonomy, "Middle", parent)
    leaf = _make_tag(competency_taxonomy, "Leaf", middle)
    earnable = _earnable(leaf, _object_id("c"), default_rule_profile)
    _record(user, earnable, DEMONSTRATED)

    _roll_up(user, earnable.object_id)

    assert _competency_status(user, leaf) == DEMONSTRATED
    assert _competency_status(user, middle) == DEMONSTRATED
    assert _competency_status(user, parent) == DEMONSTRATED


def test_a_stored_demonstrated_parent_is_not_lowered_when_only_some_sub_competencies_are_attempted(
    user, family, default_rule_profile,
) -> None:
    StudentCompetencyStatus.objects.create(
        user=user, tag=family.parent, status_id=DEMONSTRATED, created=NOW, modified=NOW,
    )
    earnable = _earnable(family.children[0], _object_id("c1"), default_rule_profile)
    _record(user, earnable, DEMONSTRATED)

    _roll_up(user, earnable.object_id)

    assert _competency_status(user, family.parent) == DEMONSTRATED


def test_a_tag_at_the_taxonomy_root_ends_the_walk(user, competency_taxonomy, default_rule_profile) -> None:
    root_tag = _make_tag(competency_taxonomy, "Root")
    earnable = _earnable(root_tag, _object_id("r"), default_rule_profile)
    _record(user, earnable, DEMONSTRATED)

    _roll_up(user, earnable.object_id)

    assert list(StudentCompetencyStatus.objects.values_list("tag_id", flat=True)) == [root_tag.pk]


def test_the_deepest_allowed_taxonomy_is_walked_to_the_top(user, competency_taxonomy, default_rule_profile) -> None:
    tags = [_make_tag(competency_taxonomy, "Level 0")]
    for depth in range(1, 6):
        tags.append(_make_tag(competency_taxonomy, f"Level {depth}", tags[-1]))
    earnable = _earnable(tags[-1], _object_id("deep"), default_rule_profile)
    _record(user, earnable, DEMONSTRATED)

    _roll_up(user, earnable.object_id)

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
    earnable = _earnable(tags[-1], _object_id("too-deep"), default_rule_profile)
    _record(user, earnable, DEMONSTRATED)

    with caplog.at_level(logging.ERROR, logger=rollup.__name__):
        _roll_up(user, earnable.object_id)

    assert _competency_status(user, tags[1]) == DEMONSTRATED
    assert _competency_status(user, tags[0]) is None
    assert [record.getMessage() for record in caplog.records] == [
        f"Competency rollup for user {user.id} stopped at the taxonomy depth cap; "
        f"parent tags {[tags[0].pk]} were not recomputed",
    ]


def test_two_graded_competencies_sharing_a_parent_recompute_that_parent_once(
    user, family, default_rule_profile,
) -> None:
    earnables = [
        _earnable(child, _object_id(f"c{index}"), default_rule_profile)
        for index, child in enumerate(family.children[:2])
    ]
    for earnable in earnables:
        _record(user, earnable, DEMONSTRATED)

    with mock.patch.object(
        rollup, "combine_parent_competency_status", wraps=rollup.combine_parent_competency_status,
    ) as combine:
        _roll_up(user, *(earnable.object_id for earnable in earnables))

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
        _earnable(only_child, _object_id("only"), default_rule_profile),
        _earnable(first, _object_id("first"), default_rule_profile),
    ]
    _earnable(second, _object_id("second"), default_rule_profile)
    for earnable in graded:
        _record(user, earnable, DEMONSTRATED)

    _roll_up(user, *(earnable.object_id for earnable in graded))

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
        _earnable(deep, _object_id("deep"), default_rule_profile),
        _earnable(shallow, _object_id("shallow"), default_rule_profile),
    ]
    for earnable in graded:
        _record(user, earnable, DEMONSTRATED)

    _roll_up(user, *(earnable.object_id for earnable in graded))

    assert _competency_status(user, deep) == DEMONSTRATED
    assert _competency_status(user, middle) == DEMONSTRATED
    assert _competency_status(user, shallow) == DEMONSTRATED
    assert _competency_status(user, top) == DEMONSTRATED
