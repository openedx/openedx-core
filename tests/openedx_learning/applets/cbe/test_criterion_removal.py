"""Tests for delete_or_archive_competency_criterion() and its ancestor-cascade helper, _cascade_remove_from_group()."""
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
import rules
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from rules.permissions import permissions as rule_permissions

from openedx_learning.api import delete_or_archive_competency_criterion
from openedx_learning.applets.cbe import api as cbe_api
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

CHANGE_OBJECTTAG_OBJECTID = "oel_tagging.change_objecttag_objectid"


@pytest.fixture(autouse=True)
def _allow_object_level_tagging() -> Iterator[None]:
    """
    Let any user tag any object; restore the real rule afterwards.

    openedx_tagging denies change_objecttag_objectid to everyone and leaves the real Studio-role
    check to openedx-platform, so these tests substitute a permissive one.
    """
    original = rule_permissions[CHANGE_OBJECTTAG_OBJECTID]
    rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, lambda _user, _object_id: True)
    yield
    rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, original)


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
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Deleting a criterion with no learner status hard-deletes it and drops its now-unreferenced
    object_tag, returning archived=False.
    """
    group = CompetencyCriteriaGroup.objects.create(tag=tag)
    criterion = _make_criterion(group, object_tag, default_rule_profile)

    result = delete_or_archive_competency_criterion(criterion.id, user)

    assert result.id == criterion.id
    assert result.archived is False
    assert not CompetencyCriterion.objects.filter(id=criterion.id).exists()
    assert not ObjectTag.objects.filter(id=object_tag.id).exists()


@pytest.mark.parametrize("shared_object_tag", [False, True], ids=["own object_tag", "shared object_tag"])
def test_archive_path_leaves_the_row_and_its_object_tag_in_place(
    *,
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
    shared_object_tag: bool,
) -> None:
    """
    Deleting a criterion that a learner status row already references archives it instead of
    hard-deleting it: the row and its object_tag both survive, and archived=True comes back. Another
    criterion on the same object_tag is left in place too.
    """
    group = CompetencyCriteriaGroup.objects.create(tag=tag)
    criterion = _make_criterion(group, object_tag, default_rule_profile)
    other = None
    if shared_object_tag:
        other_group = CompetencyCriteriaGroup.objects.create(tag=tag, parent=group)
        other = _make_criterion(other_group, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    result = delete_or_archive_competency_criterion(criterion.id, user)

    assert result.archived is True
    criterion.refresh_from_db()
    assert criterion.archived is True
    assert ObjectTag.objects.filter(id=object_tag.id).exists()
    if other is not None:
        other.refresh_from_db()
        assert other.archived is False


def _latest_history_user(model_name: str, pk: int):
    """Return the user recorded on the newest historical row of the ``model_name`` row ``pk``."""
    historical = apps.get_model("openedx_learning", model_name)
    return historical.objects.filter(id=pk).order_by("-history_date", "-history_id").first().history_user


def _history_count(model_name: str, pk: int) -> int:
    """Return how many historical rows exist for the ``model_name`` row ``pk``."""
    return apps.get_model("openedx_learning", model_name).objects.filter(id=pk).count()


def test_repeat_delete_on_an_archived_criterion_writes_no_history(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    A second delete of an already-archived criterion returns archived=True and leaves the
    criterion and its groups untouched, so no new historical row is written for any of them.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    delete_or_archive_competency_criterion(criterion.id, user)
    counts = (
        _history_count("HistoricalCompetencyCriterion", criterion.id),
        _history_count("HistoricalCompetencyCriteriaGroup", leaf.id),
        _history_count("HistoricalCompetencyCriteriaGroup", root.id),
    )

    result = delete_or_archive_competency_criterion(criterion.id, user)

    assert result.id == criterion.id
    assert result.archived is True
    criterion.refresh_from_db()
    assert criterion.archived is True
    assert counts == (
        _history_count("HistoricalCompetencyCriterion", criterion.id),
        _history_count("HistoricalCompetencyCriteriaGroup", leaf.id),
        _history_count("HistoricalCompetencyCriteriaGroup", root.id),
    )


@pytest.mark.parametrize("has_status_row", [True, False], ids=["archive", "hard delete"])
def test_removal_records_the_acting_user_in_history(
    has_status_row: bool,
    *,
    user,
    tag: Tag,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
    now: datetime,
) -> None:
    """
    Archiving a criterion, or hard-deleting one with no status row, records the acting user on the
    newest historical row of it and of each group the cascade archived or deleted.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    if has_status_row:
        StudentCompetencyCriterionStatus.objects.create(
            user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
        )

    delete_or_archive_competency_criterion(criterion.id, user)

    assert _latest_history_user("HistoricalCompetencyCriterion", criterion.id) == user
    assert _latest_history_user("HistoricalCompetencyCriteriaGroup", leaf.id) == user
    assert _latest_history_user("HistoricalCompetencyCriteriaGroup", root.id) == user


def test_shared_object_tag_is_untouched_while_another_criterion_still_references_it(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
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

    delete_or_archive_competency_criterion(criterion_a.id, user)

    assert not CompetencyCriterion.objects.filter(id=criterion_a.id).exists()
    assert CompetencyCriterion.objects.filter(id=criterion_b.id).exists()
    assert ObjectTag.objects.filter(id=object_tag.id).exists()


def test_hard_delete_removes_only_the_criterions_own_object_tag(
    user,
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A hard delete removes the criterion's own ObjectTag, now referenced by nothing. An ObjectTag for
    another competency on the same object stays with its criterion, so the delete picks the ObjectTag
    by its primary key and not by the object it is on.
    """
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Reading Poetry")
    other_object_tag = _make_object_tag(competency_taxonomy, other_tag, object_tag.object_id)
    criterion = _make_criterion(CompetencyCriteriaGroup.objects.create(tag=tag), object_tag, default_rule_profile)
    other_criterion = _make_criterion(
        CompetencyCriteriaGroup.objects.create(tag=other_tag), other_object_tag, default_rule_profile
    )

    result = delete_or_archive_competency_criterion(criterion.id, user)

    assert result.archived is False
    assert not CompetencyCriterion.objects.filter(id=criterion.id).exists()
    assert not ObjectTag.objects.filter(id=object_tag.id).exists()
    assert CompetencyCriterion.objects.filter(id=other_criterion.id).exists()
    assert ObjectTag.objects.filter(id=other_object_tag.id).exists()


# =================================================================================================
# The ancestor cascade, on hard-delete
# =================================================================================================


def test_single_child_leaf_group_cascade_deletes_while_a_sibling_keeps_the_root_alive(
    user, competency_taxonomy: CompetencyTaxonomy, tag: Tag, object_tag: ObjectTag,
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

    delete_or_archive_competency_criterion(criterion.id, user)

    assert not CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=root.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=sibling.id).exists()
    root.refresh_from_db()
    assert root.archived is False


def test_multilevel_cascade_fully_collapses_the_root_to_leaf_chain(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Hard-deleting the only criterion in a root -> mid -> leaf chain, with nothing else anywhere in
    the tree, collapses every group in the chain, one level at a time, all the way to the root.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    mid = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=mid)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)

    delete_or_archive_competency_criterion(criterion.id, user)

    assert not CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()
    assert not CompetencyCriteriaGroup.objects.filter(id=mid.id).exists()
    assert not CompetencyCriteriaGroup.objects.filter(id=root.id).exists()


def test_cascade_stops_at_an_ancestor_with_a_surviving_sibling_group(
    user, competency_taxonomy: CompetencyTaxonomy, tag: Tag, object_tag: ObjectTag,
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

    delete_or_archive_competency_criterion(criterion.id, user)

    assert not CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()
    assert not CompetencyCriteriaGroup.objects.filter(id=branch.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=root.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=other_branch.id).exists()
    assert CompetencyCriterion.objects.filter(id=other_criterion.id).exists()
    root.refresh_from_db()
    assert root.archived is False


@pytest.mark.parametrize("progress_on", ["leaf group", "root group"])
def test_group_with_its_own_status_is_archived_not_deleted_at_zero_remaining_children(
    progress_on: str, user, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    A group carrying its own StudentCompetencyCriteriaGroupStatus is archived, not hard-deleted,
    once its last child is gone, whether that group is the leaf or the root above it (depth 2).
    The criterion itself, with no status of its own, is still hard-deleted, and an archive
    cascades upward as "archive", so every ancestor of an archived group is archived too.
    """
    assert object_tag.tag is not None
    tag = object_tag.tag
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=leaf if progress_on == "leaf group" else root,
        status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    result = delete_or_archive_competency_criterion(criterion.id, user)

    assert result.archived is False
    assert not CompetencyCriterion.objects.filter(id=criterion.id).exists()
    root.refresh_from_db()
    assert root.archived is True
    if progress_on == "leaf group":
        leaf.refresh_from_db()
        assert leaf.archived is True
    else:
        assert not CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()


def test_hard_delete_beside_an_archived_criterion_archives_the_group_and_its_parent(
    user, competency_taxonomy: CompetencyTaxonomy, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    A leaf holding one archived criterion (it has learner progress) and one live criterion: hard-deleting
    the live one removes it, and the leaf, left with only archived children, is archived with its parent.
    """
    assert object_tag.tag is not None
    tag = object_tag.tag
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    live = _make_criterion(leaf, object_tag, default_rule_profile)
    archived_object_tag = _make_object_tag(competency_taxonomy, tag, "obj-archived")
    archived = _make_criterion(leaf, archived_object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=archived, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    delete_or_archive_competency_criterion(archived.id, user)

    result = delete_or_archive_competency_criterion(live.id, user)

    assert result.archived is False
    assert not CompetencyCriterion.objects.filter(id=live.id).exists()
    leaf.refresh_from_db()
    root.refresh_from_db()
    assert leaf.archived is True
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

    result = delete_or_archive_competency_criterion(criterion.id, user)

    assert result.archived is True
    criterion.refresh_from_db()
    assert criterion.archived is True
    leaf.refresh_from_db()
    root.refresh_from_db()
    assert leaf.archived is True
    assert root.archived is True


# =================================================================================================
# Permission
# =================================================================================================


def test_a_user_without_permission_is_refused_and_nothing_changes(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A user who may not tag the criterion's object is refused, and the criterion, group and ObjectTag survive."""
    group = CompetencyCriteriaGroup.objects.create(tag=tag)
    criterion = _make_criterion(group, object_tag, default_rule_profile)
    rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, lambda _user, _object_id: False)

    with pytest.raises(PermissionDenied):
        delete_or_archive_competency_criterion(criterion.id, user)

    criterion.refresh_from_db()
    assert criterion.archived is False
    assert CompetencyCriteriaGroup.objects.filter(id=group.id).exists()
    assert ObjectTag.objects.filter(id=object_tag.id).exists()


def test_delete_re_reads_the_criterion_under_the_tag_lock(
    run_when_tag_lock_is_taken,
    user,
    tag: Tag,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A criterion removed while the delete waits for the Tag lock raises DoesNotExist, so the delete
    reads the criterion again after the lock rather than trusting its first read.
    """
    criterion = _make_criterion(CompetencyCriteriaGroup.objects.create(tag=tag), object_tag, default_rule_profile)
    # Stands in for a concurrent delete that commits while this one waits for the lock.
    run_when_tag_lock_is_taken(lambda: CompetencyCriterion.objects.filter(pk=criterion.pk).delete())

    with pytest.raises(CompetencyCriterion.DoesNotExist):
        delete_or_archive_competency_criterion(criterion.id, user)


# =================================================================================================
# Not found
# =================================================================================================


def test_does_not_exist_for_an_unknown_criterion_id(user) -> None:
    """The API layer lets DoesNotExist propagate for an unknown criterion_id so each caller can map it itself."""
    with pytest.raises(CompetencyCriterion.DoesNotExist):
        delete_or_archive_competency_criterion(999999999, user)


def test_a_criterion_removed_with_its_group_before_the_lock_raises_does_not_exist(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, monkeypatch,
) -> None:
    """
    A concurrent group delete that removes the criterion and its ancestors after the function's
    pre-lock read, but before its Tag lock, surfaces as CompetencyCriterion.DoesNotExist.

    The race is simulated without threads: the ``transaction`` name the api module uses is swapped
    for one whose ``atomic`` (the function's only use of it) first hard-deletes the group chain,
    cascading to the criterion, and then opens the real transaction. That is the point between the
    pre-lock read and the Tag lock.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    group = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    criterion = _make_criterion(group, object_tag, default_rule_profile)
    real_atomic = transaction.atomic

    @contextmanager
    def delete_chain_then_atomic() -> Iterator[None]:
        CompetencyCriteriaGroup.objects.filter(pk=root.pk).delete()
        with real_atomic():
            yield

    monkeypatch.setattr(cbe_api, "transaction", SimpleNamespace(atomic=delete_chain_then_atomic))

    with pytest.raises(CompetencyCriterion.DoesNotExist):
        delete_or_archive_competency_criterion(criterion.id, user)


# =================================================================================================
# The walk stops at archived groups
# =================================================================================================


def test_cascade_stops_at_a_group_that_is_already_archived(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    Archiving a criterion whose group is already archived leaves that group and its parent alone, so
    neither gets a new historical row: everything above an archived group was handled when it was archived.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag, archived=True)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, archived=True)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    counts = (
        _history_count("HistoricalCompetencyCriteriaGroup", leaf.id),
        _history_count("HistoricalCompetencyCriteriaGroup", root.id),
    )

    result = delete_or_archive_competency_criterion(criterion.id, user)

    assert result.archived is True
    assert CompetencyCriteriaGroup.objects.filter(pk__in=[leaf.id, root.id], archived=True).count() == 2
    assert counts == (
        _history_count("HistoricalCompetencyCriteriaGroup", leaf.id),
        _history_count("HistoricalCompetencyCriteriaGroup", root.id),
    )


def test_delete_after_the_learner_is_gone_deletes_the_emptied_archived_groups(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    Once the learner whose status archived a criterion is deleted, a repeat delete hard-deletes the
    criterion, and every archived group it leaves with nothing in it is deleted too: no empty group stays.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    learner = get_user_model().objects.create(username="departing-learner")
    StudentCompetencyCriterionStatus.objects.create(
        user=learner, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    assert delete_or_archive_competency_criterion(criterion.id, user).archived is True
    learner.delete()

    result = delete_or_archive_competency_criterion(criterion.id, user)

    assert result.archived is False
    assert not CompetencyCriterion.objects.filter(pk=criterion.id).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk__in=[leaf.id, root.id]).exists()
    assert _latest_history_user("HistoricalCompetencyCriteriaGroup", leaf.id) == user
    assert _latest_history_user("HistoricalCompetencyCriteriaGroup", root.id) == user


def test_delete_leaves_an_archived_group_that_still_holds_an_archived_criterion(
    user, tag: Tag, competency_taxonomy: CompetencyTaxonomy, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A hard delete under an archived group that still holds another criterion leaves that group and its
    parent as they are, archived and with no new historical row.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag, archived=True)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, archived=True)
    kept = _make_criterion(leaf, object_tag, default_rule_profile)
    kept.archived = True
    kept.save()
    other_object_tag = _make_object_tag(competency_taxonomy, tag, "block-v1:Org1+Python100+Fall2026+problem+p2")
    deleted = _make_criterion(leaf, other_object_tag, default_rule_profile)
    counts = (
        _history_count("HistoricalCompetencyCriteriaGroup", leaf.id),
        _history_count("HistoricalCompetencyCriteriaGroup", root.id),
    )

    result = delete_or_archive_competency_criterion(deleted.id, user)

    assert result.archived is False
    assert CompetencyCriteriaGroup.objects.filter(pk__in=[leaf.id, root.id], archived=True).count() == 2
    assert counts == (
        _history_count("HistoricalCompetencyCriteriaGroup", leaf.id),
        _history_count("HistoricalCompetencyCriteriaGroup", root.id),
    )


def test_delete_leaves_an_emptied_archived_group_that_has_its_own_status(
    user, tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """An archived group left empty by a hard delete stays when a learner group status still references it."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag, archived=True)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, archived=True)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    # Only a criterion archived by an earlier delete sits under an archived group.
    criterion.archived = True
    criterion.save()
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=leaf, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    result = delete_or_archive_competency_criterion(criterion.id, user)

    assert result.archived is False
    assert CompetencyCriteriaGroup.objects.filter(pk__in=[leaf.id, root.id], archived=True).count() == 2
