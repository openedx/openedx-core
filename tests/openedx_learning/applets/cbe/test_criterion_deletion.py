"""
Delete-behavior tests for CompetencyCriterion's own foreign keys, and for the transitive and
scope-owner cases that only exist once this model completes the criteria tree.

| Foreign key | Value | Why |
| CompetencyCriterion.group | CASCADE | a leaf is meaningless without its group |
| CompetencyCriterion.object_tag | CASCADE | a leaf is meaningless without its content association |
| CompetencyCriterion.rule_profile | RESTRICT | a profile is never hard-deleted out from under a leaf |

``on_delete`` expresses containment rather than protection (ADR-0002 Decision 7): it governs
deletion of the row a foreign key points *at*, never the row holding it.

``rule_profile`` is RESTRICT rather than PROTECT because the two differ exactly where it matters
here. Both refuse a direct profile delete while a criterion is assigned to it. Only RESTRICT
ignores referencing rows that the same operation is already deleting, which is what lets a scope
owner's deletion carry its profile away instead of failing on a criterion that delete was about to
remove anyway.

Only the cascade half of each case is asserted. Every matching "raises ProtectedError because a
learner status row exists" case needs #642's three Student*Status tables, and #642 is the change
that creates them, so those assertions belong there. Nothing here stubs or fakes a status model
to stand in for them. Until #642 merges, main carries a cascade chain with no PROTECT at the
bottom, so deleting a tag removes the whole authored tree and nothing objects. That window is
expected and harmless, because the learner status tables do not exist yet.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.db.models import RestrictedError

from openedx_catalog.models import CourseRun
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    RuleType,
)
from openedx_tagging.models import ObjectTag, Tag

pytestmark = pytest.mark.django_db

_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}


# ---------------------------------------------------------------------------------------------
# CompetencyCriterion's three foreign keys


# ---------------------------------------------------------------------------------------------


def test_deleting_a_group_also_deletes_its_criteria(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyCriteriaGroup cascades to any CompetencyCriterion referencing it via
    `group`: the delete succeeds and the criterion row is gone too.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    group.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_deleting_an_object_tag_also_deletes_its_criteria(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an ObjectTag cascades to any CompetencyCriterion referencing it via `object_tag`: the
    delete succeeds and the criterion row is gone too. Doubles as the "OURS" half of #641's
    Deletions criterion for oel_tagging_objecttag, since ObjectTag has only this one hop down to
    CompetencyCriterion.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    object_tag.delete()

    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_deleting_a_rule_profile_referenced_by_a_criterion_raises_restricted_error(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyRuleProfile that a CompetencyCriterion references via `rule_profile`
    raises RestrictedError, which is what holds ADR-0002 Decision 7's "a profile is never
    hard-deleted by a direct delete" at the ORM layer.

    Nothing cascades from a profile down to a criterion, so the criterion is not part of this
    delete and RESTRICT refuses, exactly as PROTECT would have.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )

    with pytest.raises(RestrictedError) as exc_info:
        default_rule_profile.delete()

    restricted = exc_info.value.restricted_objects
    assert any(isinstance(obj, CompetencyCriterion) and obj.pk == criterion.pk for obj in restricted)


def test_object_tag_delete_leaves_a_childless_criteria_group_behind(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an ObjectTag cascades away the CompetencyCriterion that references it, but leaves the
    CompetencyCriteriaGroup that housed that criterion in place, even when it was the group's only
    criterion and the group now has no children of any kind (no criteria, no child groups).

    This is a deliberately accepted outcome, not a bug: CompetencyCriteriaGroup does not reference
    ObjectTag at all (only CompetencyCriterion does), so nothing about deleting an ObjectTag gives
    the collector a reason to reach the group. A childless group left behind this way is inert (it
    evaluates no criteria and contributes nothing to its parent's logic_operator combination) and
    is exactly the state authoring tooling must already handle for a group edited down to zero
    children, so no additional cleanup path exists for this narrower case either. Pinned here so a
    future change one way or the other (cascading the now-childless group away, or continuing to
    leave it) is a deliberate decision, not an accidental side effect of something else.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    object_tag.delete()

    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriteriaGroup.objects.get(pk=group.pk).criteria.exists()


# ---------------------------------------------------------------------------------------------
# Transitive deletes required by issue #641
# Deleting a Tag, a group at depth, or a Taxonomy takes the whole referencing criteria tree
# with it. Tag.taxonomy is already CASCADE in openedx_tagging, which is what makes the tag
# case hold transitively from a taxonomy. These only exist as of this PR, because they need
# CompetencyCriterion to complete the tree down to a leaf.


# ---------------------------------------------------------------------------------------------


def test_tag_delete_with_no_status_cascades_whole_criteria_tree(
    tag: Tag, group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an oel_tagging.Tag with no learner status beneath it succeeds and cascades away
    every CompetencyCriteriaGroup and CompetencyCriterion that references it, transitively:
    Tag -> CompetencyCriteriaGroup.tag (CASCADE) -> CompetencyCriterion.group (CASCADE).
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    tag.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_group_delete_at_depth_cascades_descendants_and_their_criteria(
    tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyCriteriaGroup that is not a root removes it, every descendant group, and
    every CompetencyCriterion under any of them, while leaving the rest of the tree (here, the
    root) alone.

    Builds a genuinely nested tree, root -> child -> grandchild, with criteria at two different
    levels (on `child` and on `grandchild`), so "at depth" and "every descendant" both mean
    something: a shallower tree could pass this by accident.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    grandchild = CompetencyCriteriaGroup.objects.create(tag=tag, parent=child)
    child_criterion = CompetencyCriterion.objects.create(
        group=child, object_tag=object_tag, rule_profile=default_rule_profile
    )
    grandchild_criterion = CompetencyCriterion.objects.create(
        group=grandchild, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=grandchild.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=child_criterion.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=grandchild_criterion.pk).exists()

    child.delete()

    assert CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=grandchild.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=child_criterion.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=grandchild_criterion.pk).exists()


def test_taxonomy_delete_cascades_every_tag_and_its_criteria(
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Deleting an oel_tagging.Taxonomy collects every Tag beneath it (Tag.taxonomy is CASCADE), so
    the tag-deletion cases above hold transitively through a taxonomy delete too. This asserts the
    succeeding case (no learner status beneath the tag), which is what #641's Deletions criterion
    for taxonomy-level deletion requires "at minimum".

    Chain exercised: CompetencyTaxonomy -> Tag (CASCADE) -> CompetencyCriteriaGroup.tag (CASCADE)
    -> CompetencyCriterion.group (CASCADE).
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert Tag.objects.filter(pk=tag.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    competency_taxonomy.delete()

    assert not Tag.objects.filter(pk=tag.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


# ---------------------------------------------------------------------------------------------
# Scope-owner deletes that reach a profile a criterion is assigned to
# These are what RESTRICT on CompetencyCriterion.rule_profile buys, and what it still refuses.
# Both are unreachable until a taxonomy- or course-scoped profile can be authored, which no code
# path does yet. See ADR-0002 Decision 7.


# ---------------------------------------------------------------------------------------------


def test_taxonomy_delete_reaching_its_scoped_profile_through_a_criterion_succeeds(
    competency_taxonomy: CompetencyTaxonomy, group: CompetencyCriteriaGroup, object_tag: ObjectTag
) -> None:
    """
    Deleting a CompetencyTaxonomy whose taxonomy-scoped profile is itself assigned to a criterion
    succeeds, and takes the profile and the criterion with it.

    This is the case RESTRICT exists for. The delete reaches the profile through
    `competency_taxonomy` (CASCADE) and reaches the criterion through the tag chain
    (Tag -> CompetencyCriteriaGroup.tag -> CompetencyCriterion.group, all CASCADE). RESTRICT then
    finds nothing left restricting the profile, because the only row referencing it is one this
    same operation is already deleting. Under PROTECT this raised ProtectedError naming that
    criterion, which was a spurious failure: an author deleting a taxonomy was told a criterion
    was in the way, when nothing about that criterion survived the delete either.
    """
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    criterion = CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=profile)

    competency_taxonomy.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


def test_course_run_delete_is_refused_by_a_criterion_outside_its_scope(
    course_run: CourseRun, group: CompetencyCriteriaGroup, object_tag: ObjectTag
) -> None:
    """
    Deleting a CourseRun whose course-scoped profile is assigned to a criterion that the same
    delete does NOT reach raises RestrictedError, and nothing is removed.

    A criterion's profile assignment is independent of its tree's `course` scope (ADR-0002
    Decision 4), so a criterion in a tree with `course=None`, which `group` is, can still be
    assigned a course-scoped profile. Deleting that run collects the profile but not the
    criterion, so RESTRICT correctly refuses: unlike the taxonomy case above, this criterion
    really would have been left pointing at a deleted profile. ADR-0002 Decision 7 records this
    as the residual case, whose fix is a fifth reassignment event on Decision 4.
    """
    profile = CompetencyRuleProfile.objects.create(
        course=course_run, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    criterion = CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=profile)
    assert group.course is None

    with pytest.raises(RestrictedError) as exc_info:
        course_run.delete()

    restricted = exc_info.value.restricted_objects
    assert any(isinstance(obj, CompetencyCriterion) and obj.pk == criterion.pk for obj in restricted)
    # Nothing was removed: the whole operation raised before any DELETE executed.
    assert CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert CourseRun.objects.filter(pk=course_run.pk).exists()
