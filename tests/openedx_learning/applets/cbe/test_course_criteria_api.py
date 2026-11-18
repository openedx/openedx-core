"""
Tests for get_course_competency_criteria(), the course-run read of competency criteria (#733).

Fixtures live in this directory's conftest.py.
"""
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

import pytest
import rules
from django.db.models.signals import post_init
from opaque_keys.edx.keys import CourseKey
from organizations.models import Organization
from rules.permissions import permissions as rule_permissions

from openedx_catalog.models import CourseRun
from openedx_learning.api import delete_or_archive_competency_criteria_group, delete_or_archive_competency_criterion
from openedx_learning.applets.cbe.api import _create_leaf_group, get_course_competency_criteria
from openedx_learning.applets.cbe.data import (
    LearnerCompetency,
    LearnerCourseCriteria,
    LearnerCriteriaGroup,
    LearnerCriterion,
    LearnerTaxonomy,
)
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    MasteryStatus,
    RuleType,
    StudentCompetencyCriterionStatus,
)
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

from .utils import make_course_run, make_tag, usage_key

pytestmark = pytest.mark.django_db

CHANGE_OBJECTTAG_OBJECTID = "oel_tagging.change_objecttag_objectid"


@pytest.fixture(autouse=True)
def _allow_object_level_tagging() -> Iterator[None]:
    """
    Let any user tag any object; restore the real rule afterwards.

    openedx_tagging denies change_objecttag_objectid to everyone and leaves the real Studio-role
    check to openedx-platform, so the tests that archive through the authoring API substitute a
    permissive one.
    """
    original = rule_permissions[CHANGE_OBJECTTAG_OBJECTID]
    rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, lambda _user, _object_id: True)
    yield
    rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, original)


@pytest.fixture(name="taxonomy_b")
def _taxonomy_b() -> CompetencyTaxonomy:
    """A second competency taxonomy, created after (so with a higher id than) `competency_taxonomy`."""
    return CompetencyTaxonomy.objects.create(name="Accounting", export_id="accounting-v1")


class Skills(NamedTuple):
    """Competency tags in two taxonomies: a three-level chain in the first, a top-level tag in the second."""

    problem_solving: Tag
    problem_definition: Tag
    root_cause: Tag
    communication: Tag


@pytest.fixture(name="skills")
def _skills(competency_taxonomy: CompetencyTaxonomy, taxonomy_b: CompetencyTaxonomy) -> Skills:
    """Tags "Problem Solving" > "Problem Definition" > "Root Cause Analysis" in A, and "Communication" in B."""
    problem_solving = make_tag(competency_taxonomy, "Problem Solving")
    problem_definition = make_tag(competency_taxonomy, "Problem Definition", parent=problem_solving)
    root_cause = make_tag(competency_taxonomy, "Root Cause Analysis", parent=problem_definition)
    communication = make_tag(taxonomy_b, "Communication")
    return Skills(problem_solving, problem_definition, root_cause, communication)


@dataclass
class Branch:
    """The groups and criteria one competency has in one course run, built by ``_make_branch``."""

    root: CompetencyCriteriaGroup
    course_level: CompetencyCriteriaGroup
    leaf: CompetencyCriteriaGroup
    criteria: list[CompetencyCriterion]


def _add_criterion(
    group: CompetencyCriteriaGroup,
    tag: Tag,
    course_run: CourseRun,
    block_id: str,
    rule_profile: CompetencyRuleProfile,
) -> CompetencyCriterion:
    """Create a live criterion for the subsection `block_id` of `course_run` under `group`."""
    object_tag = ObjectTag.objects.create(
        object_id=usage_key(course_run, block_id), taxonomy=tag.taxonomy, tag=tag,
    )
    return CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=rule_profile)


def _make_branch(
    tag: Tag, course_run: CourseRun, rule_profile: CompetencyRuleProfile, block_ids: list[str],
) -> Branch:
    """Create `tag`'s root, course-level and one bottom-tier group in `course_run`, with a criterion per block id."""
    leaf = _create_leaf_group(tag, course_run)
    course_level = leaf.parent
    assert course_level is not None
    root = course_level.parent
    assert root is not None
    criteria = [_add_criterion(leaf, tag, course_run, block_id, rule_profile) for block_id in block_ids]
    return Branch(root=root, course_level=course_level, leaf=leaf, criteria=criteria)


def _ids(items) -> list[int]:
    """Return the ids of `items`, in order."""
    return [item.id for item in items]


def _course_key(course_run: CourseRun) -> CourseKey:
    """Return `course_run`'s course key."""
    key = course_run.course_key
    assert key is not None
    return key


def _assert_references_resolve(result) -> None:
    """Assert that every parent, group, competency and taxonomy a returned item names is itself returned."""
    group_ids = {group.id for group in result.groups}
    competency_ids = {competency.id for competency in result.competencies}
    taxonomy_ids = {taxonomy.id for taxonomy in result.taxonomies}
    assert all(group.parent_id is None or group.parent_id in group_ids for group in result.groups)
    assert all(group.competency_id in competency_ids for group in result.groups)
    assert all(criterion.group_id in group_ids for criterion in result.criteria)
    assert all(competency.taxonomy_id in taxonomy_ids for competency in result.competencies)


def _empty_result():
    """Return the result for a course run with no criteria."""
    return LearnerCourseCriteria(taxonomies=[], competencies=[], groups=[], criteria=[])


# =================================================================================================
# Result contents
# =================================================================================================


def test_full_reassembly_returns_every_array_with_exact_fields(
    course_run: CourseRun, competency_taxonomy: CompetencyTaxonomy, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    One competency with a root, a course-level group, one bottom-tier group and two criteria on two
    subsections comes back as the four arrays, with each name, id, operator, ordering and rule as stored.
    """
    tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Writing Poetry", external_id="WP-101")
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1", "s2"])
    CompetencyCriteriaGroup.objects.filter(pk=branch.leaf.pk).update(ordering=3)

    result = get_course_competency_criteria(_course_key(course_run))

    assert result == LearnerCourseCriteria(
        taxonomies=[LearnerTaxonomy(id=competency_taxonomy.id, name="Nursing")],
        competencies=[
            LearnerCompetency(
                id=tag.id,
                name="Writing Poetry",
                external_id="WP-101",
                lineage=["Writing Poetry"],
                taxonomy_id=competency_taxonomy.id,
            ),
        ],
        groups=[
            LearnerCriteriaGroup(
                id=branch.root.id,
                competency_id=tag.id,
                parent_id=None,
                name="Writing Poetry (root)",
                logic_operator=None,
                ordering=0,
            ),
            LearnerCriteriaGroup(
                id=branch.course_level.id,
                competency_id=tag.id,
                parent_id=branch.root.id,
                name=branch.course_level.name,
                logic_operator=None,
                ordering=0,
            ),
            LearnerCriteriaGroup(
                id=branch.leaf.id,
                competency_id=tag.id,
                parent_id=branch.course_level.id,
                name="",
                logic_operator="OR",
                ordering=3,
            ),
        ],
        criteria=[
            LearnerCriterion(
                id=criterion.id,
                group_id=branch.leaf.id,
                usage_key=usage_key(course_run, block_id),
                rule_type="Grade",
                rule_payload=default_rule_profile.rule_payload,
            )
            for criterion, block_id in zip(branch.criteria, ["s1", "s2"], strict=True)
        ],
    )


def test_shared_root_returns_only_the_requested_courses_branch(
    course_run: CourseRun,
    organization: Organization,
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Two courses under one root, plus a second competency only in the second course: asking for the first
    course returns its own branch and the shared root, and nothing of the other course or competency.
    """
    course_y = make_course_run(organization, "Python200", "Fall2026")
    only_in_y = make_tag(competency_taxonomy, "Reading Poetry")
    branch_x = _make_branch(tag, course_run, default_rule_profile, ["x1"])
    branch_y = _make_branch(tag, course_y, default_rule_profile, ["y1"])
    branch_only_y = _make_branch(only_in_y, course_y, default_rule_profile, ["y2"])
    assert branch_x.root.id == branch_y.root.id

    result_x = get_course_competency_criteria(_course_key(course_run))
    result_y = get_course_competency_criteria(_course_key(course_y))

    assert _ids(result_x.competencies) == [tag.id]
    assert _ids(result_x.groups) == [branch_x.root.id, branch_x.course_level.id, branch_x.leaf.id]
    assert _ids(result_x.criteria) == [branch_x.criteria[0].id]
    assert _ids(result_y.competencies) == [tag.id, only_in_y.id]
    assert {group.id for group in result_y.groups} == {
        branch_y.root.id, branch_y.course_level.id, branch_y.leaf.id,
        branch_only_y.root.id, branch_only_y.course_level.id, branch_only_y.leaf.id,
    }
    assert _ids(result_y.criteria) == [branch_y.criteria[0].id, branch_only_y.criteria[0].id]


def test_competency_taxonomies_are_listed_and_other_taxonomies_are_not(
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    taxonomy_b: CompetencyTaxonomy,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Two competency taxonomies each with one competency come back with their names, while tags of a
    taxonomy that is not a competency taxonomy, put on the same subsections, appear nowhere.
    """
    tag_a = make_tag(competency_taxonomy, "Writing Poetry")
    tag_b = make_tag(taxonomy_b, "Ledger Keeping")
    branch_a = _make_branch(tag_a, course_run, default_rule_profile, ["s1"])
    branch_b = _make_branch(tag_b, course_run, default_rule_profile, ["s2"])
    topics = Taxonomy.objects.create(name="Topics")
    topic_tag = Tag.objects.create(taxonomy=topics, value="Poetry")
    for block_id in ["s1", "s2"]:
        ObjectTag.objects.create(object_id=usage_key(course_run, block_id), taxonomy=topics, tag=topic_tag)

    result = get_course_competency_criteria(_course_key(course_run))

    assert {taxonomy.id: taxonomy.name for taxonomy in result.taxonomies} == {
        competency_taxonomy.id: "Nursing", taxonomy_b.id: "Accounting",
    }
    assert {competency.name: competency.taxonomy_id for competency in result.competencies} == {
        "Writing Poetry": competency_taxonomy.id, "Ledger Keeping": taxonomy_b.id,
    }
    assert {group.id: group.competency_id for group in result.groups} == {
        branch_a.root.id: tag_a.id, branch_a.course_level.id: tag_a.id, branch_a.leaf.id: tag_a.id,
        branch_b.root.id: tag_b.id, branch_b.course_level.id: tag_b.id, branch_b.leaf.id: tag_b.id,
    }
    assert sorted(criterion.usage_key for criterion in result.criteria) == [
        usage_key(course_run, "s1"), usage_key(course_run, "s2"),
    ]


def test_competency_lineage_runs_from_the_taxonomy_top_down_to_the_tag(
    course_run: CourseRun, skills: Skills, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A nested competency carries all three of its levels as its lineage, and a top-level one only itself."""
    _make_branch(skills.root_cause, course_run, default_rule_profile, ["s1"])
    _make_branch(skills.communication, course_run, default_rule_profile, ["s2"])

    result = get_course_competency_criteria(_course_key(course_run))

    assert {competency.name: competency.lineage for competency in result.competencies} == {
        "Root Cause Analysis": ["Problem Solving", "Problem Definition", "Root Cause Analysis"],
        "Communication": ["Communication"],
    }


def test_rule_from_the_system_default_profile(
    course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A criterion on the system default profile reports that profile's current type and payload."""
    default_rule_profile.rule_payload = {"op": "gte", "value": 0.75, "scale": "percent"}
    default_rule_profile.save()
    _make_branch(tag, course_run, default_rule_profile, ["s1"])

    result = get_course_competency_criteria(_course_key(course_run))

    assert [(c.rule_type, c.rule_payload) for c in result.criteria] == [
        ("Grade", {"op": "gte", "value": 0.75, "scale": "percent"}),
    ]


@pytest.mark.parametrize(
    ("rule_type", "rule_payload"),
    [
        ("Grade", {"op": "gte", "value": 0.82, "scale": "percent"}),
        ("Novel", {"depth": 3, "tags": ["a", "b"]}),
    ],
    ids=["known rule type", "unknown rule type"],
)
def test_rule_from_the_criterion_override_is_returned_as_stored(
    rule_type: str, rule_payload: dict, course_run: CourseRun, tag: Tag,
) -> None:
    """
    A criterion with no profile reports its own override type and payload exactly as stored, including
    a rule type and payload the validators don't know, written by bypassing validation.
    """
    leaf = _create_leaf_group(tag, course_run)
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "s1"), taxonomy=tag.taxonomy, tag=tag)
    criterion = CompetencyCriterion.objects.create(
        group=leaf,
        object_tag=object_tag,
        rule_profile=None,
        rule_type_override=RuleType.GRADE,
        rule_payload_override={"op": "gte", "value": 0.5, "scale": "percent"},
    )
    CompetencyCriterion.objects.filter(pk=criterion.pk).update(
        rule_type_override=rule_type, rule_payload_override=rule_payload,
    )

    result = get_course_competency_criteria(_course_key(course_run))

    assert [(c.rule_type, c.rule_payload) for c in result.criteria] == [(rule_type, rule_payload)]


def test_rule_of_an_archived_profile_is_still_reported(
    course_run: CourseRun, organization: Organization, tag: Tag,
) -> None:
    """A criterion stays in the result, with its profile's rule, after that profile is archived."""
    profile = CompetencyRuleProfile.objects.create(
        organization=organization,
        rule_type=RuleType.GRADE,
        rule_payload={"op": "gte", "value": 0.6, "scale": "percent"},
    )
    branch = _make_branch(tag, course_run, profile, ["s1"])
    profile.archived = True
    profile.save()

    result = get_course_competency_criteria(_course_key(course_run))

    assert _ids(result.criteria) == [branch.criteria[0].id]
    assert [(c.rule_type, c.rule_payload) for c in result.criteria] == [
        ("Grade", {"op": "gte", "value": 0.6, "scale": "percent"}),
    ]


def test_result_order_is_deterministic(
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    taxonomy_b: CompetencyTaxonomy,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Groups are ordered by (ordering, id) and everything else by id, whatever the names or the groups'
    parentage, and a second call returns an identical result.
    """
    # "Zulu" and the first taxonomy get lower ids than "Alpha" and the second, so id order differs from name order.
    tag_z = make_tag(competency_taxonomy, "Zulu")
    tag_a = make_tag(taxonomy_b, "Alpha")
    branch_z = _make_branch(tag_z, course_run, default_rule_profile, ["z1"])
    branch_a = _make_branch(tag_a, course_run, default_rule_profile, ["a1"])
    # Siblings created with their ordering out of id order: "late" has the lowest id of the three.
    late = CompetencyCriteriaGroup.objects.create(tag=tag_a, parent=branch_a.course_level, ordering=5)
    early_1 = CompetencyCriteriaGroup.objects.create(tag=tag_a, parent=branch_a.course_level, ordering=1)
    early_2 = CompetencyCriteriaGroup.objects.create(tag=tag_a, parent=branch_a.course_level, ordering=1)
    criterion_late = _add_criterion(late, tag_a, course_run, "a2", default_rule_profile)
    criterion_early_1 = _add_criterion(early_1, tag_a, course_run, "a3", default_rule_profile)
    criterion_early_2 = _add_criterion(early_2, tag_a, course_run, "a4", default_rule_profile)

    result = get_course_competency_criteria(_course_key(course_run))

    assert _ids(result.taxonomies) == [competency_taxonomy.id, taxonomy_b.id]
    assert _ids(result.competencies) == [tag_z.id, tag_a.id]
    assert _ids(result.groups) == [
        branch_z.root.id, branch_z.course_level.id, branch_z.leaf.id,
        branch_a.root.id, branch_a.course_level.id, branch_a.leaf.id,
        early_1.id, early_2.id, late.id,
    ]
    assert _ids(result.criteria) == [
        branch_z.criteria[0].id, branch_a.criteria[0].id,
        criterion_late.id, criterion_early_1.id, criterion_early_2.id,
    ]
    assert get_course_competency_criteria(_course_key(course_run)) == result


def test_group_nested_under_a_bottom_tier_group_is_returned_with_its_parent(
    course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A group below a bottom-tier group, with a criterion of its own, is returned and names that group as parent."""
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1"])
    nested = CompetencyCriteriaGroup.objects.create(tag=tag, parent=branch.leaf)
    nested_criterion = _add_criterion(nested, tag, course_run, "s2", default_rule_profile)

    result = get_course_competency_criteria(_course_key(course_run))

    assert {group.id: group.parent_id for group in result.groups} == {
        branch.root.id: None,
        branch.course_level.id: branch.root.id,
        branch.leaf.id: branch.course_level.id,
        nested.id: branch.leaf.id,
    }
    assert {criterion.id: criterion.group_id for criterion in result.criteria} == {
        branch.criteria[0].id: branch.leaf.id, nested_criterion.id: nested.id,
    }


def test_bottom_tier_group_with_a_tag_other_than_the_roots_is_returned_with_its_criterion(
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A group below this run's course-level group is found through its parent, whatever tag it carries."""
    other_tag = make_tag(competency_taxonomy, "Reading Poetry")
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1"])
    odd_group = CompetencyCriteriaGroup.objects.create(tag=other_tag, parent=branch.course_level)
    odd_criterion = _add_criterion(odd_group, other_tag, course_run, "s2", default_rule_profile)

    result = get_course_competency_criteria(_course_key(course_run))

    assert odd_group.id in _ids(result.groups)
    assert _ids(result.criteria) == [branch.criteria[0].id, odd_criterion.id]
    assert {group.competency_id for group in result.groups} == {tag.id}
    _assert_references_resolve(result)


def test_group_below_the_course_level_group_is_returned_even_when_it_has_a_course(
    course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A group below this run's course-level group is found through its parent, whether or not it has a course."""
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1"])
    scoped_group = CompetencyCriteriaGroup.objects.create(tag=tag, parent=branch.course_level, course=course_run)
    scoped_criterion = _add_criterion(scoped_group, tag, course_run, "s2", default_rule_profile)

    result = get_course_competency_criteria(_course_key(course_run))

    assert scoped_group.id in _ids(result.groups)
    assert _ids(result.criteria) == [branch.criteria[0].id, scoped_criterion.id]


def test_criterion_on_the_course_level_group_is_returned_and_one_on_the_root_is_not(
    course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A criterion attached to the course-level group is returned; one attached to the root never is."""
    branch = _make_branch(tag, course_run, default_rule_profile, [])
    on_course_level = _add_criterion(branch.course_level, tag, course_run, "s1", default_rule_profile)
    _add_criterion(branch.root, tag, course_run, "s2", default_rule_profile)

    result = get_course_competency_criteria(_course_key(course_run))

    assert _ids(result.criteria) == [on_course_level.id]
    assert result.criteria[0].group_id == branch.course_level.id
    assert _ids(result.groups) == [branch.root.id, branch.course_level.id]


# =================================================================================================
# Exclusions
# =================================================================================================


def test_criterion_archived_through_the_authoring_api_is_left_out_and_its_sibling_stays(
    user, now: datetime, course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Archiving one of two criteria in a group, with a learner status on it, hides only that criterion."""
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1", "s2"])
    archived, sibling = branch.criteria
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=archived, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    outcome = delete_or_archive_competency_criterion(archived.id, user)
    assert outcome.archived is True

    result = get_course_competency_criteria(_course_key(course_run))

    assert _ids(result.criteria) == [sibling.id]
    assert _ids(result.groups) == [branch.root.id, branch.course_level.id, branch.leaf.id]


@pytest.mark.parametrize("target_name", ["bottom_tier", "course_level"])
@pytest.mark.parametrize("archived_by", ["authoring api", "flag only"])
def test_archived_group_hides_its_whole_subtree(
    target_name: str,
    archived_by: str,
    *,
    user,
    now: datetime,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    An archived bottom-tier group removes it, its nested groups and their criteria while the rest of
    the competency stays. An archived course-level group removes that competency and its root, and the
    other competency stays. The same holds when only the archived flag is set on the group, leaving its
    descendants live. No returned item names a parent that was left out.
    """
    other_tag = make_tag(competency_taxonomy, "Reading Poetry")
    branch = _make_branch(tag, course_run, default_rule_profile, ["a1"])
    nested = CompetencyCriteriaGroup.objects.create(tag=tag, parent=branch.leaf)
    nested_criterion = _add_criterion(nested, tag, course_run, "a2", default_rule_profile)
    sibling_leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=branch.course_level)
    sibling_criterion = _add_criterion(sibling_leaf, tag, course_run, "a3", default_rule_profile)
    other = _make_branch(other_tag, course_run, default_rule_profile, ["b1"])
    target = branch.leaf if target_name == "bottom_tier" else branch.course_level
    if archived_by == "authoring api":
        StudentCompetencyCriterionStatus.objects.create(
            user=user, criterion=branch.criteria[0], status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
        )
        assert delete_or_archive_competency_criteria_group(target.id, user).archived is True
    else:
        CompetencyCriteriaGroup.objects.filter(pk=target.pk).update(archived=True)
        assert CompetencyCriteriaGroup.objects.filter(pk=nested.pk, archived=False).exists()
        assert CompetencyCriterion.objects.filter(pk=nested_criterion.pk, archived=False).exists()

    result = get_course_competency_criteria(_course_key(course_run))

    other_group_ids = {other.root.id, other.course_level.id, other.leaf.id}
    if target_name == "bottom_tier":
        assert _ids(result.competencies) == [tag.id, other_tag.id]
        assert {group.id for group in result.groups} == (
            {branch.root.id, branch.course_level.id, sibling_leaf.id} | other_group_ids
        )
        assert _ids(result.criteria) == [sibling_criterion.id, other.criteria[0].id]
    else:
        assert _ids(result.competencies) == [other_tag.id]
        assert {group.id for group in result.groups} == other_group_ids
        assert _ids(result.criteria) == [other.criteria[0].id]
    _assert_references_resolve(result)


def test_archived_flag_on_a_root_group_hides_the_whole_competency(
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """With only the flag set on a competency's root, none of that competency's branch is returned."""
    other_tag = make_tag(competency_taxonomy, "Reading Poetry")
    branch = _make_branch(tag, course_run, default_rule_profile, ["a1"])
    other = _make_branch(other_tag, course_run, default_rule_profile, ["b1"])
    CompetencyCriteriaGroup.objects.filter(pk=branch.root.pk).update(archived=True)
    assert CompetencyCriteriaGroup.objects.filter(pk=branch.leaf.pk, archived=False).exists()

    result = get_course_competency_criteria(_course_key(course_run))

    assert _ids(result.competencies) == [other_tag.id]
    assert _ids(result.groups) == [other.root.id, other.course_level.id, other.leaf.id]
    assert _ids(result.criteria) == [other.criteria[0].id]


@pytest.mark.parametrize(
    "condition",
    ["tag archived", "taxonomy archived", "taxonomy disabled", "parent tag archived", "grandparent tag archived"],
)
def test_competency_in_an_unavailable_tag_or_taxonomy_is_left_out(
    condition: str,
    *,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    taxonomy_b: CompetencyTaxonomy,
    skills: Skills,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    An archived tag, an archived parent or grandparent tag above a live tag, an archived taxonomy whose
    tags are not archived, or a disabled taxonomy removes the competency with everything beneath it,
    and the other taxonomy's competency stays.
    """
    _make_branch(skills.root_cause, course_run, default_rule_profile, ["s1"])
    kept = _make_branch(skills.communication, course_run, default_rule_profile, ["s2"])
    if condition == "tag archived":
        Tag.objects.filter(pk=skills.root_cause.pk).update(archived=True)
    elif condition == "taxonomy archived":
        Taxonomy.objects.filter(pk=competency_taxonomy.pk).update(archived=True)
    elif condition == "taxonomy disabled":
        Taxonomy.objects.filter(pk=competency_taxonomy.pk).update(enabled=False)
    else:
        ancestor = skills.problem_definition if condition == "parent tag archived" else skills.problem_solving
        Tag.objects.filter(pk=ancestor.pk).update(archived=True)
        assert Tag.objects.filter(pk=skills.root_cause.pk, archived=False).exists()

    result = get_course_competency_criteria(_course_key(course_run))

    assert _ids(result.taxonomies) == [taxonomy_b.id]
    assert _ids(result.competencies) == [skills.communication.id]
    assert _ids(result.groups) == [kept.root.id, kept.course_level.id, kept.leaf.id]
    assert _ids(result.criteria) == [kept.criteria[0].id]


def test_groups_and_criteria_on_a_tag_of_a_plain_taxonomy_are_not_returned(
    course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A complete branch with a criterion on a tag of a plain taxonomy, not a competency taxonomy, is left out."""
    kept = _make_branch(tag, course_run, default_rule_profile, ["s1"])
    topics = Taxonomy.objects.create(name="Topics")
    topic_tag = Tag.objects.create(taxonomy=topics, value="Poetry")
    topic_root = CompetencyCriteriaGroup.objects.create(tag=topic_tag)
    topic_course_level = CompetencyCriteriaGroup.objects.create(tag=topic_tag, parent=topic_root, course=course_run)
    topic_leaf = CompetencyCriteriaGroup.objects.create(tag=topic_tag, parent=topic_course_level)
    _add_criterion(topic_leaf, topic_tag, course_run, "s2", default_rule_profile)

    result = get_course_competency_criteria(_course_key(course_run))

    assert _ids(result.competencies) == [tag.id]
    assert _ids(result.groups) == [kept.root.id, kept.course_level.id, kept.leaf.id]
    assert _ids(result.criteria) == [kept.criteria[0].id]


def test_criterion_with_an_archived_object_tag_is_left_out(
    course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A live criterion whose ObjectTag is archived is left out and its sibling in the same group stays."""
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1", "s2"])
    hidden, sibling = branch.criteria
    ObjectTag.objects.filter(pk=hidden.object_tag_id).update(archived=True)

    result = get_course_competency_criteria(_course_key(course_run))

    assert _ids(result.criteria) == [sibling.id]
    assert _ids(result.groups) == [branch.root.id, branch.course_level.id, branch.leaf.id]


def test_group_whose_only_criterion_is_archived_is_pruned(
    course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A live bottom-tier group left with no live criterion is left out, and a sibling group with one stays."""
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1"])
    sibling_leaf = CompetencyCriteriaGroup.objects.create(tag=tag, parent=branch.course_level)
    sibling_criterion = _add_criterion(sibling_leaf, tag, course_run, "s2", default_rule_profile)
    CompetencyCriterion.objects.filter(pk=branch.criteria[0].pk).update(archived=True)

    result = get_course_competency_criteria(_course_key(course_run))

    assert _ids(result.groups) == [branch.root.id, branch.course_level.id, sibling_leaf.id]
    assert _ids(result.criteria) == [sibling_criterion.id]


@pytest.mark.parametrize("exclusion", ["criterion archived", "object tag archived"])
def test_competency_whose_only_criterion_is_excluded_is_pruned_with_its_unarchived_groups(
    exclusion: str, course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A competency whose single criterion is excluded, while its root, course-level and bottom-tier groups
    stay unarchived, leaves nothing to return: the competency, its groups and its taxonomy are pruned.
    """
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1"])
    criterion = branch.criteria[0]
    if exclusion == "criterion archived":
        CompetencyCriterion.objects.filter(pk=criterion.pk).update(archived=True)
    else:
        ObjectTag.objects.filter(pk=criterion.object_tag_id).update(archived=True)
    assert CompetencyCriteriaGroup.objects.filter(
        pk__in=[branch.root.pk, branch.course_level.pk, branch.leaf.pk], archived=False,
    ).count() == 3

    result = get_course_competency_criteria(_course_key(course_run))

    assert result == _empty_result()


@pytest.mark.parametrize("cause", ["all criteria archived", "taxonomy disabled"])
def test_course_left_with_nothing_returns_empty_arrays(
    cause: str,
    *,
    user,
    now: datetime,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """With every criterion archived through the authoring API, or the only taxonomy disabled, the result is empty."""
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1", "s2"])
    if cause == "all criteria archived":
        for criterion in branch.criteria:
            StudentCompetencyCriterionStatus.objects.create(
                user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
            )
            assert delete_or_archive_competency_criterion(criterion.id, user).archived is True
    else:
        Taxonomy.objects.filter(pk=competency_taxonomy.pk).update(enabled=False)

    result = get_course_competency_criteria(_course_key(course_run))

    assert result == _empty_result()


@pytest.mark.parametrize("requested", ["run without criteria", "key without a run"])
def test_course_with_no_criteria_or_no_course_run_returns_empty_arrays(
    requested: str,
    course_run: CourseRun,
    organization: Organization,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A course run that never had criteria, and a well-formed key with no CourseRun, both give empty arrays."""
    other_run = make_course_run(organization, "Python200", "Fall2026")
    _make_branch(tag, other_run, default_rule_profile, ["s1"])
    key = _course_key(course_run) if requested == "run without criteria" else CourseKey.from_string(
        "course-v1:Org1+Unknown999+Never2026"
    )

    result = get_course_competency_criteria(key)

    assert result == _empty_result()


# =================================================================================================
# Query counts
# =================================================================================================

# For a root, a course-level group and a bottom-tier group: the course-level groups (with tag, taxonomy and
# ancestor-tag checks joined in), the bottom-tier groups, the empty level below them, then the criteria.
QUERY_COUNT_FOR_ONE_BOTTOM_TIER_LEVEL = 4


def test_course_without_criteria_costs_one_query(course_run: CourseRun, django_assert_num_queries) -> None:
    """The first query finds no course-level group, so nothing else runs."""
    key = _course_key(course_run)

    with django_assert_num_queries(1):
        result = get_course_competency_criteria(key)

    assert result == _empty_result()


def test_query_count_stays_fixed_as_the_course_grows(
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    taxonomy_b: CompetencyTaxonomy,
    default_rule_profile: CompetencyRuleProfile,
    django_assert_num_queries,
) -> None:
    """
    One criterion on a nested competency costs the same number of queries as a course of the same depth
    with several taxonomies, competencies, groups and criteria, nested tags and an archived parent tag.
    """
    key = _course_key(course_run)
    parent = make_tag(competency_taxonomy, "Problem Solving")
    first_tag = make_tag(competency_taxonomy, "Problem Definition", parent=parent)
    _make_branch(first_tag, course_run, default_rule_profile, ["first"])

    with django_assert_num_queries(QUERY_COUNT_FOR_ONE_BOTTOM_TIER_LEVEL):
        small = get_course_competency_criteria(key)
    assert len(small.criteria) == 1

    taxonomies = [competency_taxonomy, taxonomy_b, CompetencyTaxonomy.objects.create(name="Law", export_id="law-v1")]
    for t_index, taxonomy in enumerate(taxonomies):
        top = make_tag(taxonomy, f"Top {t_index}")
        middle = make_tag(taxonomy, f"Middle {t_index}", parent=top)
        for c_index in range(3):
            competency = make_tag(taxonomy, f"Skill {t_index}-{c_index}", parent=middle)
            branch = _make_branch(competency, course_run, default_rule_profile, [f"t{t_index}c{c_index}a"])
            sibling = CompetencyCriteriaGroup.objects.create(tag=competency, parent=branch.course_level)
            _add_criterion(sibling, competency, course_run, f"t{t_index}c{c_index}b", default_rule_profile)
            _add_criterion(sibling, competency, course_run, f"t{t_index}c{c_index}c", default_rule_profile)
    retired_parent = make_tag(competency_taxonomy, "Retired Parent")
    retired_child = make_tag(competency_taxonomy, "Child Of Retired", parent=retired_parent)
    _make_branch(retired_child, course_run, default_rule_profile, ["retired"])
    Tag.objects.filter(pk=retired_parent.pk).update(archived=True)

    with django_assert_num_queries(QUERY_COUNT_FOR_ONE_BOTTOM_TIER_LEVEL):
        big = get_course_competency_criteria(key)

    assert len(big.competencies) == 10
    assert len(big.criteria) == 1 + 9 * 3
    assert len(big.taxonomies) == 3


@pytest.mark.parametrize("other_run_count", [1, 5])
def test_other_course_runs_using_the_same_competency_add_no_query_and_no_rows(
    other_run_count: int,
    *,
    course_run: CourseRun,
    organization: Organization,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
    django_assert_num_queries,
) -> None:
    """Branches that other course runs hang under the same root are never loaded, so they cost no query and no row."""
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1"])
    for index in range(other_run_count):
        other_run = make_course_run(organization, f"Other{index}", "Fall2026")
        other = _make_branch(tag, other_run, default_rule_profile, [f"o{index}"])
        nested = CompetencyCriteriaGroup.objects.create(tag=tag, parent=other.leaf)
        _add_criterion(nested, tag, other_run, f"n{index}", default_rule_profile)
    key = _course_key(course_run)
    loaded_group_ids: list[int] = []

    def _record_loaded_group(instance, **kwargs) -> None:
        loaded_group_ids.append(instance.pk)

    post_init.connect(_record_loaded_group, sender=CompetencyCriteriaGroup)
    try:
        with django_assert_num_queries(QUERY_COUNT_FOR_ONE_BOTTOM_TIER_LEVEL):
            result = get_course_competency_criteria(key)
    finally:
        post_init.disconnect(_record_loaded_group, sender=CompetencyCriteriaGroup)

    assert len(result.criteria) == 1
    assert sorted(loaded_group_ids) == sorted([branch.root.pk, branch.course_level.pk, branch.leaf.pk])


def test_each_extra_level_of_nesting_adds_one_query(
    course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile, django_assert_num_queries,
) -> None:
    """The groups below the course-level group are read one level per query, so depth, not volume, sets the count."""
    branch = _make_branch(tag, course_run, default_rule_profile, ["s1"])
    key = _course_key(course_run)
    parent = branch.leaf
    for extra_levels in (1, 2):
        parent = CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent)
        _add_criterion(parent, tag, course_run, f"n{extra_levels}", default_rule_profile)

        with django_assert_num_queries(QUERY_COUNT_FOR_ONE_BOTTOM_TIER_LEVEL + extra_levels):
            result = get_course_competency_criteria(key)

        assert len(result.criteria) == 1 + extra_levels
