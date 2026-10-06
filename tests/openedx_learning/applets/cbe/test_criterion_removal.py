"""Tests for delete_competency_criterion() and its ancestor-cascade helper, _cascade_from_group()."""
from datetime import datetime

import pytest

from openedx_learning.api import delete_competency_criterion
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    MasteryStatus,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriterionStatus,
)
from openedx_tagging.models import ObjectTag, Tag

pytestmark = pytest.mark.django_db


def _make_object_tag(competency_taxonomy: CompetencyTaxonomy, tag: Tag, object_id: str) -> ObjectTag:
    """Create an ObjectTag for `object_id`, distinct from the conftest `object_tag` fixture's own row."""
    return ObjectTag.objects.create(object_id=object_id, taxonomy=competency_taxonomy, tag=tag)


def _make_criterion(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> CompetencyCriterion:
    """Create a CompetencyCriterion under `group`, on the system-default rule profile."""
    return CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=default_rule_profile)


# =================================================================================================
# Hard-delete vs. archive, on the criterion itself
# =================================================================================================


def test_hard_delete_with_no_status_removes_the_row_and_its_object_tag(
    tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Deleting a criterion with no learner status hard-deletes it and drops its now-unreferenced
    object_tag, returning archived=False.
    """
    group = CompetencyCriteriaGroup.objects.create(tag=tag)
    criterion = _make_criterion(group, object_tag, default_rule_profile)

    result = delete_competency_criterion(criterion.id)

    assert result.id == criterion.id
    assert result.archived is False
    assert not CompetencyCriterion.objects.filter(id=criterion.id).exists()
    assert not ObjectTag.objects.filter(id=object_tag.id).exists()


def test_archive_path_leaves_the_row_and_its_object_tag_in_place(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    Deleting a criterion that a learner status row already references archives it instead of
    hard-deleting it: the row and its object_tag both survive, and archived=True comes back.
    """
    group = CompetencyCriteriaGroup.objects.create(tag=tag)
    criterion = _make_criterion(group, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    result = delete_competency_criterion(criterion.id)

    assert result.archived is True
    criterion.refresh_from_db()
    assert criterion.archived is True
    assert ObjectTag.objects.filter(id=object_tag.id).exists()


def test_repeat_call_on_an_already_archived_criterion_is_idempotent(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """A second DELETE against an already-archived criterion re-enters the archive branch without error."""
    group = CompetencyCriteriaGroup.objects.create(tag=tag)
    criterion = _make_criterion(group, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    delete_competency_criterion(criterion.id)

    result = delete_competency_criterion(criterion.id)

    assert result.id == criterion.id
    assert result.archived is True
    criterion.refresh_from_db()
    assert criterion.archived is True


def test_shared_object_tag_is_untouched_while_another_criterion_still_references_it(
    tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Two criteria, in two different groups, referencing the same object_tag (a deliberate second
    association per ADR-0002): hard-deleting one leaves the other, and the shared object_tag, intact.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    group_a = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    group_b = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    criterion_a = _make_criterion(group_a, object_tag, default_rule_profile)
    criterion_b = _make_criterion(group_b, object_tag, default_rule_profile)

    delete_competency_criterion(criterion_a.id)

    assert not CompetencyCriterion.objects.filter(id=criterion_a.id).exists()
    assert CompetencyCriterion.objects.filter(id=criterion_b.id).exists()
    assert ObjectTag.objects.filter(id=object_tag.id).exists()


# =================================================================================================
# The ancestor cascade, on hard-delete
# =================================================================================================


def test_single_child_leaf_group_cascade_deletes_while_a_sibling_keeps_the_root_alive(
    competency_taxonomy: CompetencyTaxonomy, tag: Tag, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Hard-deleting a criterion that was its leaf group's only child also deletes that now-empty
    group, but the cascade stops there: a sibling group under the same root, with its own live
    criterion, keeps the root from being touched.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    sibling = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    sibling_object_tag = _make_object_tag(competency_taxonomy, tag, "obj-sibling")
    _make_criterion(sibling, sibling_object_tag, default_rule_profile)

    delete_competency_criterion(criterion.id)

    assert not CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=root.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=sibling.id).exists()


def test_multilevel_cascade_fully_collapses_the_root_to_leaf_chain(
    tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Hard-deleting the only criterion in a root -> mid -> leaf chain, with nothing else anywhere in
    the tree, collapses every group in the chain, one level at a time, all the way to the root.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    mid = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=mid)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)

    delete_competency_criterion(criterion.id)

    assert not CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()
    assert not CompetencyCriteriaGroup.objects.filter(id=mid.id).exists()
    assert not CompetencyCriteriaGroup.objects.filter(id=root.id).exists()


def test_cascade_stops_at_an_ancestor_with_a_surviving_sibling_group(
    competency_taxonomy: CompetencyTaxonomy, tag: Tag, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A deeper chain (root -> branch -> leaf) collapses branch and leaf when the leaf's only
    criterion is hard-deleted, but stops at root: a second branch under root, with its own live
    criterion, keeps root from being touched.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    branch = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=branch)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    other_branch = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    other_object_tag = _make_object_tag(competency_taxonomy, tag, "obj-other-branch")
    other_criterion = _make_criterion(other_branch, other_object_tag, default_rule_profile)

    delete_competency_criterion(criterion.id)

    assert not CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()
    assert not CompetencyCriteriaGroup.objects.filter(id=branch.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=root.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=other_branch.id).exists()
    assert CompetencyCriterion.objects.filter(id=other_criterion.id).exists()


def test_group_with_its_own_status_is_archived_not_deleted_at_zero_remaining_children(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    A leaf group carrying its own StudentCompetencyCriteriaGroupStatus is archived, not
    hard-deleted, even once its last criterion is gone -- while that criterion itself, with no
    status of its own, is still hard-deleted. Archiving the leaf then cascades upward as
    "archive", so an ancestor with no other content is archived too, not deleted.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=leaf, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    result = delete_competency_criterion(criterion.id)

    assert result.archived is False
    assert not CompetencyCriterion.objects.filter(id=criterion.id).exists()
    leaf.refresh_from_db()
    assert leaf.archived is True
    root.refresh_from_db()
    assert root.archived is True


# =================================================================================================
# The ancestor cascade, on archive
# =================================================================================================


def test_archiving_a_groups_only_criterion_archives_the_group_and_cascades_upward(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    Archiving a criterion that was its leaf group's only live child archives that group too, and
    the cascade continues upward through an ancestor with no other active content.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    delete_competency_criterion(criterion.id)

    leaf.refresh_from_db()
    root.refresh_from_db()
    assert leaf.archived is True
    assert root.archived is True
    assert CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=root.id).exists()


# =================================================================================================
# Not found
# =================================================================================================


def test_does_not_exist_for_an_unknown_criterion_id() -> None:
    """A criterion_id with no matching row raises CompetencyCriterion.DoesNotExist."""
    with pytest.raises(CompetencyCriterion.DoesNotExist):
        delete_competency_criterion(999999999)
