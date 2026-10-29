"""Tests for delete_or_archive_competency_criteria_group(), which reuses _cascade_from_group() unchanged."""
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
import rules
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from rules.permissions import permissions as rule_permissions

from openedx_catalog.models import CourseRun
from openedx_learning.api import delete_or_archive_competency_criteria_group, resolve_competency_criteria_group_course
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
CHANGE_OBJECTTAG_TAXONOMY = "oel_tagging.change_objecttag_taxonomy"


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
# Hard-delete vs. archive, and the reported counts
# =================================================================================================


def test_leaf_group_with_no_descendants_or_criteria_hard_deletes_with_zero_counts(
    user, tag: Tag, course_run: CourseRun,
) -> None:
    """Deleting an empty, childless target group reports both cascade counts as zero."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)

    result = delete_or_archive_competency_criteria_group(target.id, user)

    assert result.id == target.id
    assert result.archived is False
    assert result.cascaded_group_count == 0
    assert result.cascaded_criteria_count == 0
    assert not CompetencyCriteriaGroup.objects.filter(id=target.id).exists()


def test_multilevel_cascade_hard_delete_counts_the_descendant_group_and_both_criteria(
    user, competency_taxonomy: CompetencyTaxonomy, tag: Tag, course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A target group with one child group holding two criteria reports exactly those counts."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=target)
    object_tag_a = _make_object_tag(competency_taxonomy, tag, "obj-a")
    object_tag_b = _make_object_tag(competency_taxonomy, tag, "obj-b")
    criterion_a = _make_criterion(child, object_tag_a, default_rule_profile)
    criterion_b = _make_criterion(child, object_tag_b, default_rule_profile)

    result = delete_or_archive_competency_criteria_group(target.id, user)

    assert result.archived is False
    assert result.cascaded_group_count == 1
    assert result.cascaded_criteria_count == 2
    assert not CompetencyCriteriaGroup.objects.filter(id=target.id).exists()
    assert not CompetencyCriteriaGroup.objects.filter(id=child.id).exists()
    assert not CompetencyCriterion.objects.filter(id__in=[criterion_a.id, criterion_b.id]).exists()


def test_archive_when_status_exists_at_a_buried_grandchild_criterion(
    user, *, tag: Tag, course_run: CourseRun, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    A learner status on a criterion several levels below the target archives the whole subtree
    -- every group and criterion in it -- with the cascade counts still reported accurately.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    mid = CompetencyCriteriaGroup.objects.create(tag=tag, parent=target)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=mid)
    criterion = _make_criterion(leaf, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    result = delete_or_archive_competency_criteria_group(target.id, user)

    assert result.archived is True
    assert result.cascaded_group_count == 2
    assert result.cascaded_criteria_count == 1
    target.refresh_from_db()
    mid.refresh_from_db()
    leaf.refresh_from_db()
    criterion.refresh_from_db()
    assert target.archived is True
    assert mid.archived is True
    assert leaf.archived is True
    assert criterion.archived is True


def _history_count(model_name: str, pk: int) -> int:
    """Return how many historical rows exist for the ``model_name`` row ``pk``."""
    return apps.get_model("openedx_learning", model_name).objects.filter(id=pk).count()


def _latest_history_user(model_name: str, pk: int) -> Any:
    """Return the user recorded on the newest historical row of the ``model_name`` row ``pk``."""
    historical = apps.get_model("openedx_learning", model_name)
    return historical.objects.filter(id=pk).order_by("-history_date", "-history_id").first().history_user


def test_subtree_archive_records_the_acting_user_in_history(
    user, *, tag: Tag, course_run: CourseRun, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """Archiving a subtree records the acting user on the newest historical row of each archived group and criterion."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=target)
    criterion = _make_criterion(child, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    delete_or_archive_competency_criteria_group(target.id, user)

    for group in (target, child, root):
        assert _latest_history_user("HistoricalCompetencyCriteriaGroup", group.id) == user
    assert _latest_history_user("HistoricalCompetencyCriterion", criterion.id) == user


def test_repeat_delete_on_an_archived_subtree_writes_no_history(
    user, *, tag: Tag, course_run: CourseRun, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    A second delete of an already-archived subtree returns archived=True and writes no new historical
    row for the target, its descendants, its criteria, or its ancestors.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=target)
    criterion = _make_criterion(child, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    delete_or_archive_competency_criteria_group(target.id, user)

    def counts() -> tuple[int, ...]:
        return (
            *(_history_count("HistoricalCompetencyCriteriaGroup", g.id) for g in (target, child, root)),
            _history_count("HistoricalCompetencyCriterion", criterion.id),
        )

    before = counts()

    result = delete_or_archive_competency_criteria_group(target.id, user)

    assert result.id == target.id
    assert result.archived is True
    target.refresh_from_db()
    assert target.archived is True
    assert counts() == before


# =================================================================================================
# ObjectTag cleanup
# =================================================================================================


def test_orphaned_object_tag_is_removed_on_hard_delete(
    user, competency_taxonomy: CompetencyTaxonomy, tag: Tag, course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """An object_tag referenced only by a criterion inside the deleted subtree is untagged too."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    object_tag = _make_object_tag(competency_taxonomy, tag, "obj-only-in-subtree")
    _make_criterion(target, object_tag, default_rule_profile)

    delete_or_archive_competency_criteria_group(target.id, user)

    assert not ObjectTag.objects.filter(id=object_tag.id).exists()


def test_shared_object_tag_survives_when_a_criterion_outside_the_subtree_still_references_it(
    user, tag: Tag, course_run: CourseRun, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Two criteria, in two different groups, referencing the same object_tag (a deliberate second
    association per ADR-0002): hard-deleting one group's subtree leaves the other group, its
    criterion, and the shared object_tag, all intact.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)
    sibling = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)
    _make_criterion(target, object_tag, default_rule_profile)
    sibling_criterion = _make_criterion(sibling, object_tag, default_rule_profile)

    delete_or_archive_competency_criteria_group(target.id, user)

    assert ObjectTag.objects.filter(id=object_tag.id).exists()
    assert CompetencyCriterion.objects.filter(id=sibling_criterion.id).exists()


def test_hard_delete_keeps_an_object_tag_still_referenced_outside_the_subtree(
    user, competency_taxonomy: CompetencyTaxonomy, tag: Tag, course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    One object carries two competency tags of the same taxonomy. The tag only the deleted subtree
    references is removed, and the tag a criterion outside the subtree references stays, with that criterion.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)
    sibling = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)
    tag_a = Tag.objects.create(taxonomy=competency_taxonomy, value="Tag A")
    tag_b = Tag.objects.create(taxonomy=competency_taxonomy, value="Tag B")
    object_tag_a = _make_object_tag(competency_taxonomy, tag_a, "obj-two-tags")
    object_tag_b = _make_object_tag(competency_taxonomy, tag_b, "obj-two-tags")
    _make_criterion(target, object_tag_a, default_rule_profile)
    sibling_criterion = _make_criterion(sibling, object_tag_b, default_rule_profile)

    delete_or_archive_competency_criteria_group(target.id, user)

    assert not ObjectTag.objects.filter(id=object_tag_a.id).exists()
    assert ObjectTag.objects.filter(id=object_tag_b.id).exists()
    assert CompetencyCriterion.objects.filter(id=sibling_criterion.id).exists()


def test_untag_with_the_competency_taxonomy_disabled_removes_only_the_orphaned_object_tag(
    user, competency_taxonomy: CompetencyTaxonomy, course_run: CourseRun, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    With the taxonomy disabled the tagging API hides its tags, but the delete removes only the
    subtree's orphaned ObjectTag, so it succeeds and the object's other ObjectTag and its criterion
    stay. The taxonomy-level permission rule denies every user on a disabled taxonomy, so the test
    substitutes a permissive one to reach the untag step.
    """
    assert object_tag.tag is not None
    tag = object_tag.tag
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    criterion = _make_criterion(target, object_tag, default_rule_profile)
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Reading Poetry")
    other_object_tag = _make_object_tag(competency_taxonomy, other_tag, object_tag.object_id)
    other_criterion = _make_criterion(
        CompetencyCriteriaGroup.objects.create(tag=other_tag), other_object_tag, default_rule_profile
    )
    competency_taxonomy.enabled = False
    competency_taxonomy.save()
    original = rule_permissions[CHANGE_OBJECTTAG_TAXONOMY]
    rules.set_perm(CHANGE_OBJECTTAG_TAXONOMY, lambda _user, _taxonomy: True)

    try:
        result = delete_or_archive_competency_criteria_group(target.id, user)
    finally:
        rules.set_perm(CHANGE_OBJECTTAG_TAXONOMY, original)

    assert result.archived is False
    assert not CompetencyCriteriaGroup.objects.filter(id=target.id).exists()
    assert not CompetencyCriterion.objects.filter(id=criterion.id).exists()
    assert not ObjectTag.objects.filter(id=object_tag.id).exists()
    assert CompetencyCriterion.objects.filter(id=other_criterion.id).exists()
    assert ObjectTag.objects.filter(id=other_object_tag.id).exists()


def test_hard_delete_leaves_a_tag_of_another_competency_on_the_same_object(
    user, *, monkeypatch: pytest.MonkeyPatch, competency_taxonomy: CompetencyTaxonomy, tag: Tag, course_run: CourseRun,
    object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    An ObjectTag for another competency tag on the same object, committed after a tag list was
    read, survives the delete together with its criterion. The tag list is stubbed to omit it, as
    a read taken before that commit would; the delete must not depend on reading it.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    _make_criterion(target, object_tag, default_rule_profile)
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Reading Poetry")
    other_object_tag = _make_object_tag(competency_taxonomy, other_tag, object_tag.object_id)
    other_criterion = _make_criterion(
        CompetencyCriteriaGroup.objects.create(tag=other_tag), other_object_tag, default_rule_profile
    )
    stale_list = [ObjectTag.objects.get(pk=object_tag.pk)]
    monkeypatch.setattr("openedx_learning.applets.cbe.api.get_object_tags", lambda *_args, **_kwargs: stale_list)

    delete_or_archive_competency_criteria_group(target.id, user)

    assert not ObjectTag.objects.filter(id=object_tag.id).exists()
    assert ObjectTag.objects.filter(id=other_object_tag.id).exists()
    assert CompetencyCriterion.objects.filter(id=other_criterion.id).exists()


def test_hard_delete_removes_an_object_tag_shared_by_two_criteria_in_the_subtree_once(
    user, tag: Tag, course_run: CourseRun, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Two criteria in the deleted subtree share one orphaned ObjectTag: the delete succeeds and the row is gone."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=target)
    _make_criterion(target, object_tag, default_rule_profile)
    _make_criterion(child, object_tag, default_rule_profile)

    result = delete_or_archive_competency_criteria_group(target.id, user)

    assert result.archived is False
    assert not CompetencyCriterion.objects.filter(group_id__in=[target.id, child.id]).exists()
    assert not ObjectTag.objects.filter(id=object_tag.id).exists()


def test_archive_when_status_exists_only_on_the_target_group(
    user, *, tag: Tag, course_run: CourseRun, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """A group-level status on the target alone, with no criterion status, archives the whole subtree."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=target)
    criterion = _make_criterion(child, object_tag, default_rule_profile)
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=target, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    result = delete_or_archive_competency_criteria_group(target.id, user)

    assert result.archived is True
    for node in (target, child, criterion):
        node.refresh_from_db()
        assert node.archived is True
    assert ObjectTag.objects.filter(id=object_tag.id).exists()


def test_archive_when_status_exists_only_on_a_descendant_group(
    user, *, tag: Tag, course_run: CourseRun, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """A group-level status on a descendant alone, with no criterion status, archives the whole subtree."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=target)
    criterion = _make_criterion(target, object_tag, default_rule_profile)
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=child, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    result = delete_or_archive_competency_criteria_group(target.id, user)

    assert result.archived is True
    for node in (target, child, criterion):
        node.refresh_from_db()
        assert node.archived is True
    assert ObjectTag.objects.filter(id=object_tag.id).exists()


def test_object_tag_is_untouched_on_archive(
    user, *, tag: Tag, course_run: CourseRun, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """Archiving a subtree never calls tag_object(): the object_tag row is left exactly as it was."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    criterion = _make_criterion(target, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    result = delete_or_archive_competency_criteria_group(target.id, user)

    assert result.archived is True
    assert ObjectTag.objects.filter(id=object_tag.id).exists()


# =================================================================================================
# The ancestor cascade, above the target
# =================================================================================================


def test_ancestor_cascade_collapses_the_chain_on_hard_delete(
    user, tag: Tag, course_run: CourseRun, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Hard-deleting a subtree that was its course-level ancestor's only child collapses that
    ancestor and the root above it too, via _cascade_from_group().
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)
    _make_criterion(target, object_tag, default_rule_profile)

    delete_or_archive_competency_criteria_group(target.id, user)

    assert not CompetencyCriteriaGroup.objects.filter(id=target.id).exists()
    assert not CompetencyCriteriaGroup.objects.filter(id=course_level.id).exists()
    assert not CompetencyCriteriaGroup.objects.filter(id=root.id).exists()


def test_ancestor_cascade_archives_the_chain_when_the_target_carries_status(
    user, *, tag: Tag, course_run: CourseRun, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """Archiving a subtree that was its ancestor's only live child archives that ancestor too, up to the root."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)
    criterion = _make_criterion(target, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    delete_or_archive_competency_criteria_group(target.id, user)

    course_level.refresh_from_db()
    root.refresh_from_db()
    assert course_level.archived is True
    assert root.archived is True
    assert CompetencyCriteriaGroup.objects.filter(id=course_level.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=root.id).exists()


def test_cascade_stops_at_an_ancestor_with_a_surviving_sibling_group(
    user, *, competency_taxonomy: CompetencyTaxonomy, tag: Tag, course_run: CourseRun,
    object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A sibling group under the same course-level ancestor, with its own live criterion, keeps that
    ancestor from being touched, even though the target's own subtree is fully removed.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)
    _make_criterion(target, object_tag, default_rule_profile)
    sibling = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)
    sibling_object_tag = _make_object_tag(competency_taxonomy, tag, "obj-sibling")
    sibling_criterion = _make_criterion(sibling, sibling_object_tag, default_rule_profile)

    delete_or_archive_competency_criteria_group(target.id, user)

    assert not CompetencyCriteriaGroup.objects.filter(id=target.id).exists()
    course_level.refresh_from_db()
    root.refresh_from_db()
    sibling.refresh_from_db()
    assert course_level.archived is False
    assert root.archived is False
    assert sibling.archived is False
    assert CompetencyCriteriaGroup.objects.filter(id=course_level.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=sibling.id).exists()
    assert CompetencyCriterion.objects.filter(id=sibling_criterion.id).exists()


def test_archive_cascade_stops_at_an_ancestor_with_a_surviving_sibling_group(
    user, *, competency_taxonomy: CompetencyTaxonomy, tag: Tag, course_run: CourseRun,
    object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    When the target subtree is archived because of learner progress, a live sibling group under the
    same parent keeps the parent and the root from being archived.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)
    criterion = _make_criterion(target, object_tag, default_rule_profile)
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    sibling = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)
    sibling_object_tag = _make_object_tag(competency_taxonomy, tag, "obj-sibling")
    _make_criterion(sibling, sibling_object_tag, default_rule_profile)

    result = delete_or_archive_competency_criteria_group(target.id, user)

    assert result.archived is True
    for group in (target, course_level, root, sibling):
        group.refresh_from_db()
    assert target.archived is True
    assert course_level.archived is False
    assert root.archived is False
    assert sibling.archived is False


# =================================================================================================
# Root-group rejection, permission-course resolution, and not-found
# =================================================================================================


def test_root_group_rejection_raises_before_any_write(
    user, tag: Tag, course_run: CourseRun, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A root target_group is rejected with ValidationError before any write: the root, its child,
    and its criterion are all untouched afterward.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    criterion = _make_criterion(child, object_tag, default_rule_profile)

    with pytest.raises(ValidationError):
        delete_or_archive_competency_criteria_group(root.id, user)

    root.refresh_from_db()
    child.refresh_from_db()
    criterion.refresh_from_db()
    assert root.archived is False
    assert child.archived is False
    assert criterion.archived is False
    assert CompetencyCriteriaGroup.objects.filter(id=root.id).exists()
    assert CompetencyCriteriaGroup.objects.filter(id=child.id).exists()
    assert CompetencyCriterion.objects.filter(id=criterion.id).exists()


def test_permission_course_rejects_a_root_group(tag: Tag) -> None:
    """A root group has no single course to check permission against, so it is rejected."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)

    with pytest.raises(ValidationError) as exc_info:
        resolve_competency_criteria_group_course(root)

    assert "group_id" in exc_info.value.message_dict


def test_permission_course_is_the_targets_own_course_for_a_course_level_group(tag: Tag, course_run: CourseRun) -> None:
    """A course-level target group (its own course_id set) resolves to that course."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)

    assert resolve_competency_criteria_group_course(course_level) == course_run


def test_permission_course_is_the_parents_course_for_a_non_course_level_target(tag: Tag, course_run: CourseRun) -> None:
    """A target group with no course_id of its own (one level under a course-level group) resolves to its parent's."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level)

    assert resolve_competency_criteria_group_course(leaf) == course_run


@pytest.mark.parametrize("depth", [2, 3])
def test_permission_course_is_the_nearest_course_level_ancestors_course_at_any_depth(
    tag: Tag, course_run: CourseRun, depth: int,
) -> None:
    """A target nested `depth` levels below its course-level group resolves to that group's course."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    for _ in range(depth):
        group = CompetencyCriteriaGroup.objects.create(tag=tag, parent=group)

    assert resolve_competency_criteria_group_course(group) == course_run


def test_permission_course_rejects_a_group_with_no_course_level_group_in_its_lineage(tag: Tag) -> None:
    """A non-root group with no course anywhere above it is invalid data, rejected as a group_id error."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    middle = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, parent=middle)

    with pytest.raises(ValidationError) as exc_info:
        resolve_competency_criteria_group_course(target)

    assert "group_id" in exc_info.value.message_dict


def test_does_not_exist_for_an_unknown_group_id(user) -> None:
    """A group_id with no matching row raises CompetencyCriteriaGroup.DoesNotExist."""
    with pytest.raises(CompetencyCriteriaGroup.DoesNotExist):
        delete_or_archive_competency_criteria_group(999999999, user)


def test_a_user_without_permission_is_refused_and_nothing_changes(
    user, tag: Tag, course_run: CourseRun, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A user who may not tag the course is refused, and the subtree, its criteria and ObjectTags survive."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=target)
    criterion = _make_criterion(child, object_tag, default_rule_profile)
    rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, lambda _user, _object_id: False)

    with pytest.raises(PermissionDenied):
        delete_or_archive_competency_criteria_group(target.id, user)

    for group in (root, target, child):
        group.refresh_from_db()
        assert group.archived is False
    criterion.refresh_from_db()
    assert criterion.archived is False
    assert ObjectTag.objects.filter(id=object_tag.id).exists()
