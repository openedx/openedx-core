"""
Integrative tests for CompetencyAchievementCriteria trees.

test_criteria_deletion.py proves each foreign key cascades or protects correctly in isolation.
That is not the same claim as "deleting somewhere in the middle of a realistic tree leaves exactly
the right rows behind and nothing else": a per-foreign-key test can pass while a wider tree still
ends up with an orphaned group, a criterion pointing at nothing, or a sibling branch disturbed by
a delete that should not have touched it. The tests here build a wider tree on purpose and assert
the full surviving/removed row set, not just that a cascade fired somewhere.

Fixtures shared with test_criteria_models.py and test_criteria_deletion.py live in this directory's
conftest.py.
"""
import pytest

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


def test_deleting_a_middle_group_removes_its_subtree_but_leaves_the_rest_of_the_tree_untouched(
    tag: Tag, competency_taxonomy: CompetencyTaxonomy, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyCriteriaGroup partway down a realistic tree removes exactly that group,
    every descendant beneath it, and every criterion under any of them -- and nothing else. A
    sibling branch of the deleted group, with its own criterion, survives completely unchanged.

    Tree built here, all under one root:

        root
        |-- branch_to_delete (criterion: profile-assigned, via default_rule_profile)
        |     `-- grandchild (criterion: override, no rule_profile)
        `-- surviving_sibling (criterion: profile-assigned, via a taxonomy-scoped profile)

    `branch_to_delete` is deleted. This exercises criteria at two different tree depths (on
    `branch_to_delete` itself and on its child `grandchild`) with a genuine mix of the two ways a
    criterion can be governed (a stored `rule_profile` vs. per-criterion overrides), and confirms
    `surviving_sibling` and its own criterion are byte-for-byte untouched: same primary keys, still
    present, in a tree that shares a root with the subtree that just got removed. A test that only
    checks "the deleted branch is gone" cannot tell a correct cascade apart from one that
    over-deletes into a sibling it should never have reached; this test can.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag, name="root")
    branch_to_delete = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, name="branch_to_delete")
    grandchild = CompetencyCriteriaGroup.objects.create(tag=tag, parent=branch_to_delete, name="grandchild")
    surviving_sibling = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, name="surviving_sibling")

    branch_object_tag = ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+branch", taxonomy=competency_taxonomy, tag=tag
    )
    grandchild_object_tag = ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+grandchild", taxonomy=competency_taxonomy, tag=tag
    )
    sibling_object_tag = ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+sibling", taxonomy=competency_taxonomy, tag=tag
    )

    taxonomy_scoped_profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    branch_criterion = CompetencyCriterion.objects.create(
        group=branch_to_delete, object_tag=branch_object_tag, rule_profile=default_rule_profile
    )
    grandchild_criterion = CompetencyCriterion.objects.create(
        group=grandchild,
        object_tag=grandchild_object_tag,
        rule_type_override=RuleType.GRADE,
        rule_payload_override=_GRADE_PAYLOAD,
    )
    sibling_criterion = CompetencyCriterion.objects.create(
        group=surviving_sibling, object_tag=sibling_object_tag, rule_profile=taxonomy_scoped_profile
    )

    all_group_pks = {root.pk, branch_to_delete.pk, grandchild.pk, surviving_sibling.pk}
    all_criterion_pks = {branch_criterion.pk, grandchild_criterion.pk, sibling_criterion.pk}
    existing_group_pks = set(CompetencyCriteriaGroup.objects.filter(pk__in=all_group_pks).values_list("pk", flat=True))
    existing_criterion_pks = set(
        CompetencyCriterion.objects.filter(pk__in=all_criterion_pks).values_list("pk", flat=True)
    )
    assert existing_group_pks == all_group_pks
    assert existing_criterion_pks == all_criterion_pks

    branch_to_delete.delete()

    remaining_group_pks = set(
        CompetencyCriteriaGroup.objects.filter(pk__in=all_group_pks).values_list("pk", flat=True)
    )
    remaining_criterion_pks = set(
        CompetencyCriterion.objects.filter(pk__in=all_criterion_pks).values_list("pk", flat=True)
    )

    # Exactly the root and the surviving sibling remain; the deleted branch and its child are gone.
    assert remaining_group_pks == {root.pk, surviving_sibling.pk}
    # Exactly the sibling's criterion remains; both criteria under the deleted branch are gone,
    # regardless of whether they were profile-assigned or override-governed.
    assert remaining_criterion_pks == {sibling_criterion.pk}

    # The surviving sibling and its criterion are not merely "still present somewhere" but the
    # exact same rows, untouched by the delete of an unrelated branch under the same root.
    surviving_sibling.refresh_from_db()
    sibling_criterion.refresh_from_db()
    assert surviving_sibling.parent_id == root.pk
    assert sibling_criterion.group_id == surviving_sibling.pk
    assert sibling_criterion.rule_profile_id == taxonomy_scoped_profile.pk
