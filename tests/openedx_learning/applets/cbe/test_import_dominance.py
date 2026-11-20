"""Tests for validate_tag_import_dominance(), the competency dominance check on taxonomy re-imports."""
import json
from io import BytesIO

import pytest

from openedx_catalog.models import CourseRun
from openedx_learning.applets.cbe.api import get_blocked_courses, validate_tag_import_dominance
from openedx_learning.models import CompetencyCriterion, CompetencyTaxonomy
from openedx_tagging.import_export import ParserFormat
from openedx_tagging.import_export.api import import_tags
from openedx_tagging.import_export.import_plan import TagImportPlan, TagItem
from openedx_tagging.models import Tag, TagImportTaskState, Taxonomy

from .utils import make_course_run, make_leaf_criterion

pytestmark = pytest.mark.django_db

TAGS = [  # (identifier, value, parent identifier): file row N is position N in this list
    ("cognitive-skills", "Cognitive Skills", None),  # row 1
    ("critical-thinking", "Critical Thinking", "cognitive-skills"),  # row 2
    ("inference", "Inference", "critical-thinking"),  # row 3
    ("analysis", "Analysis", "critical-thinking"),  # row 4
    ("evaluation", "Evaluation", None),  # row 5
    ("deduction", "Deduction", "evaluation"),  # row 6
    ("synthesis", "Synthesis", None),  # row 7
]

COGNITIVE_SKILLS = ("Cognitive Skills", "cognitive-skills")
CRITICAL_THINKING = ("Critical Thinking", "critical-thinking")
INFERENCE = ("Inference", "inference")
DEDUCTION = ("Deduction", "deduction")
SYNTHESIS = ("Synthesis", "synthesis")


def make_skills(taxonomy: Taxonomy) -> dict[str, Tag]:
    """Create the Thinking Skills tags on `taxonomy`, keyed by identifier."""
    tags: dict[str, Tag] = {}
    for identifier, value, parent in TAGS:
        tags[identifier] = Tag.objects.create(
            taxonomy=taxonomy, value=value, external_id=identifier, parent=tags[parent] if parent else None,
        )
    return tags


@pytest.fixture(name="skills")
def _skills(competency_taxonomy: CompetencyTaxonomy) -> dict[str, Tag]:
    """Create the Thinking Skills tags, keyed by identifier."""
    return make_skills(competency_taxonomy)


@pytest.fixture(name="course_x")
def _course_x(organization) -> CourseRun:
    """Course run X, in `organization`."""
    return make_course_run(organization, "CT101", "2026")


@pytest.fixture(name="course_y")
def _course_y(organization2) -> CourseRun:
    """Course run Y, in `organization2`."""
    return make_course_run(organization2, "CT201", "2026")


def rows(**changes: dict) -> list[dict]:
    """Return TAGS as file rows ({"id", "value", "parent_id"}), with `changes[identifier]` merged into that row."""
    result = []
    for identifier, value, parent in TAGS:
        row = {"id": identifier, "value": value, "parent_id": parent}
        row.update(changes.get(identifier.replace("-", "_"), {}))
        result.append(row)
    return result


def reimport(taxonomy, file_rows, *, plan_only=True, replace=True, validators=(validate_tag_import_dominance,)):
    """Run import_tags() on a JSON file of `file_rows` and return (success, task, plan)."""
    file = BytesIO(json.dumps({"tags": file_rows}).encode())
    return import_tags(taxonomy, file, ParserFormat.JSON, replace=replace, plan_only=plan_only, validators=validators)


def hold(tag, course_run, default_rule_profile, block_id="b1") -> CompetencyCriterion:
    """Give `tag` one live criterion in `course_run` via make_leaf_criterion."""
    return make_leaf_criterion(tag, course_run, default_rule_profile, block_id)


def conflict(row, descendant, ancestor, course_run) -> str:
    """Return the exact error message for a (value, identifier) descendant and ancestor."""
    message = (
        f'Competency "{descendant[0]}" ({descendant[1]}) and its ancestor "{ancestor[0]}" ({ancestor[1]}) '
        f"both have criteria in course {course_run.course_key}. "
        f'Change the file so that "{ancestor[0]}" is not an ancestor of "{descendant[0]}", '
        "or archive or remove the criteria of one of the two competencies in that course first."
    )
    return message if row is None else f"Row {row}: {message}"


def assert_rejected(taxonomy, file_rows, expected, **kwargs) -> None:
    """Assert the preview of `file_rows` fails with exactly the `expected` error messages, in order."""
    success, _task, plan = reimport(taxonomy, file_rows, **kwargs)
    assert success is False
    assert [str(e) for e in plan.errors] == expected


def assert_allowed(taxonomy, file_rows) -> None:
    """Assert the preview and then the import of `file_rows` both succeed with no errors."""
    for plan_only in (True, False):
        success, _task, plan = reimport(taxonomy, file_rows, plan_only=plan_only)
        assert success is True
        assert not plan.errors


def parent_of(tag: Tag) -> str | None:
    """Return the stored parent identifier of `tag`, re-read from the database."""
    tag.refresh_from_db()
    return tag.parent.external_id if tag.parent else None


# ---------------------------------------------------------------------------------------------
# Rejected re-imports


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(({"parent_id": "critical-thinking"}, DEDUCTION), id="moved_under_tag_with_criteria"),
        pytest.param(
            (
                {"id": "deductive-reasoning", "previous_id": "deduction", "parent_id": "critical-thinking"},
                ("Deduction", "deductive-reasoning"),
            ),
            id="identifier_change_and_move",
        ),
        pytest.param(
            ({"parent_id": "critical-thinking", "value": "Deductive Logic"}, ("Deductive Logic", "deduction")),
            id="value_change_is_named_from_the_file",
        ),
        pytest.param(({"parent_id": "Critical-Thinking"}, DEDUCTION), id="parent_identifier_differs_by_case"),
    ],
)
def test_move_under_tag_with_criteria_is_rejected(
    case, competency_taxonomy, skills, course_x, default_rule_profile,
) -> None:
    deduction_row, descendant = case
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["deduction"], course_x, default_rule_profile)
    assert_rejected(
        competency_taxonomy,
        rows(deduction=deduction_row),
        [conflict(6, descendant, CRITICAL_THINKING, course_x)],
    )


def test_descendant_of_moved_tag_holds_criteria(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["deduction"], course_x, default_rule_profile)
    assert_rejected(
        competency_taxonomy,
        rows(evaluation={"parent_id": "critical-thinking"}),
        [conflict(6, DEDUCTION, CRITICAL_THINKING, course_x)],
    )


def test_conflict_with_a_grandparent_is_reported(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["cognitive-skills"], course_x, default_rule_profile)
    hold(skills["deduction"], course_x, default_rule_profile)
    assert_rejected(
        competency_taxonomy,
        rows(deduction={"parent_id": "inference"}),
        [conflict(6, DEDUCTION, COGNITIVE_SKILLS, course_x)],
    )


def test_new_tag_linking_a_pair_is_rejected(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["deduction"], course_x, default_rule_profile)
    file_rows = rows(deduction={"parent_id": "argumentation"}) + [
        {"id": "argumentation", "value": "Argumentation", "parent_id": "critical-thinking"},
    ]
    assert_rejected(competency_taxonomy, file_rows, [conflict(6, DEDUCTION, CRITICAL_THINKING, course_x)])


def test_two_moves_creating_a_pair_together_are_rejected(
    competency_taxonomy, skills, course_x, default_rule_profile,
) -> None:
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["synthesis"], course_x, default_rule_profile)
    assert_rejected(
        competency_taxonomy,
        rows(evaluation={"parent_id": "critical-thinking"}, synthesis={"parent_id": "evaluation"}),
        [conflict(7, SYNTHESIS, CRITICAL_THINKING, course_x)],
    )


def test_every_conflict_is_reported(competency_taxonomy, skills, course_x, course_y, default_rule_profile) -> None:
    for course_run in (course_x, course_y):
        hold(skills["critical-thinking"], course_run, default_rule_profile)
        hold(skills["deduction"], course_run, default_rule_profile)
    hold(skills["synthesis"], course_x, default_rule_profile)
    assert_rejected(
        competency_taxonomy,
        rows(deduction={"parent_id": "critical-thinking"}, synthesis={"parent_id": "analysis"}),
        [
            conflict(6, DEDUCTION, CRITICAL_THINKING, course_x),
            conflict(6, DEDUCTION, CRITICAL_THINKING, course_y),
            conflict(7, SYNTHESIS, CRITICAL_THINKING, course_x),
        ],
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(({"synthesis": {"value": "Creative Synthesis"}}, CRITICAL_THINKING), id="rename_only_file"),
        pytest.param(
            (
                {
                    "critical_thinking": {"id": "crit-think", "previous_id": "critical-thinking"},
                    "inference": {"parent_id": "crit-think"},
                    "analysis": {"parent_id": "crit-think"},
                },
                ("Critical Thinking", "crit-think"),
            ),
            id="ancestor_identifier_changes",
        ),
    ],
)
def test_existing_pair_is_rejected(case, competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    changes, ancestor = case
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["inference"], course_x, default_rule_profile)
    assert_rejected(competency_taxonomy, rows(**changes), [conflict(3, INFERENCE, ancestor, course_x)])


# ---------------------------------------------------------------------------------------------
# Allowed re-imports


def test_archived_criteria_do_not_block(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["deduction"], course_x, default_rule_profile)
    criterion = hold(skills["critical-thinking"], course_x, default_rule_profile)
    criterion.archived = True
    criterion.save()
    assert_allowed(competency_taxonomy, rows(deduction={"parent_id": "critical-thinking"}))
    assert parent_of(skills["deduction"]) == "critical-thinking"


def test_archived_association_does_not_block(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["deduction"], course_x, default_rule_profile)
    criterion = hold(skills["critical-thinking"], course_x, default_rule_profile)
    course_level_group = criterion.group.parent
    assert course_level_group is not None
    course_level_group.archived = True
    course_level_group.save()
    assert_allowed(competency_taxonomy, rows(deduction={"parent_id": "critical-thinking"}))
    assert parent_of(skills["deduction"]) == "critical-thinking"


def test_different_course_runs_do_not_block(
    competency_taxonomy, skills, course_x, course_y, default_rule_profile,
) -> None:
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["deduction"], course_y, default_rule_profile)
    assert_allowed(competency_taxonomy, rows(deduction={"parent_id": "critical-thinking"}))
    assert parent_of(skills["deduction"]) == "critical-thinking"


def test_siblings_do_not_block(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["analysis"], course_x, default_rule_profile)
    hold(skills["deduction"], course_x, default_rule_profile)
    assert_allowed(competency_taxonomy, rows(deduction={"parent_id": "critical-thinking"}))
    assert parent_of(skills["deduction"]) == "critical-thinking"
    assert parent_of(skills["analysis"]) == "critical-thinking"


def test_new_tag_under_tag_with_criteria(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    assert_allowed(
        competency_taxonomy,
        rows() + [{"id": "argumentation", "value": "Argumentation", "parent_id": "critical-thinking"}],
    )
    argumentation = Tag.objects.get(taxonomy=competency_taxonomy, value="Argumentation")
    assert parent_of(argumentation) == "critical-thinking"


def test_no_tag_with_criteria_moves(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["deduction"], course_x, default_rule_profile)
    file_rows = rows(synthesis={"value": "Creative Synthesis"}, analysis={"parent_id": "evaluation"}) + [
        {"id": "induction", "value": "Induction", "parent_id": "evaluation"},
    ]
    assert_allowed(competency_taxonomy, file_rows)
    stored = {
        (tag.external_id, tag.value, tag.parent.external_id if tag.parent else None)
        for tag in Tag.objects.filter(taxonomy=competency_taxonomy).select_related("parent")
    }
    assert stored == {(row["id"], row["value"], row["parent_id"]) for row in file_rows}


def test_existing_pair_moved_to_root_clears_it(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["inference"], course_x, default_rule_profile)
    assert_allowed(competency_taxonomy, rows(inference={"parent_id": None}))
    assert parent_of(skills["inference"]) is None


def test_plain_taxonomy_without_criteria_passes() -> None:
    taxonomy = Taxonomy.objects.create(name="Thinking Skills", export_id="thinking-skills")
    skills = make_skills(taxonomy)
    assert_allowed(taxonomy, rows(deduction={"parent_id": "critical-thinking"}))
    assert parent_of(skills["deduction"]) == "critical-thinking"


# ---------------------------------------------------------------------------------------------
# Import behavior around the check


def test_rejected_import_applies_none_of_the_file(
    competency_taxonomy, skills, course_x, default_rule_profile,
) -> None:
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["deduction"], course_x, default_rule_profile)
    file_rows = rows(deduction={"parent_id": "critical-thinking"}, synthesis={"value": "Creative Synthesis"})
    success, task, _plan = reimport(competency_taxonomy, file_rows, plan_only=False)
    assert not success and task.status == TagImportTaskState.ERROR.value
    assert conflict(6, DEDUCTION, CRITICAL_THINKING, course_x) in task.log
    assert parent_of(skills["deduction"]) == "evaluation"
    skills["synthesis"].refresh_from_db()
    assert skills["synthesis"].value == "Synthesis"


def test_criterion_added_between_preview_and_import_is_caught(
    competency_taxonomy, skills, course_x, default_rule_profile,
) -> None:
    hold(skills["deduction"], course_x, default_rule_profile)
    file_rows = rows(deduction={"parent_id": "critical-thinking"})
    success, _task, _plan = reimport(competency_taxonomy, file_rows)
    assert success is True
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    assert_rejected(
        competency_taxonomy, file_rows, [conflict(6, DEDUCTION, CRITICAL_THINKING, course_x)], plan_only=False,
    )
    assert parent_of(skills["deduction"]) == "evaluation"


def test_allowed_move_keeps_criteria_and_blocks_no_course(
    competency_taxonomy, skills, course_x, default_rule_profile,
) -> None:
    criterion = hold(skills["deduction"], course_x, default_rule_profile)
    success, _task, _plan = reimport(competency_taxonomy, rows(deduction={"parent_id": "inference"}), plan_only=False)
    assert success is True
    assert parent_of(skills["deduction"]) == "inference"
    stored = CompetencyCriterion.objects.get(pk=criterion.pk)
    assert (stored.group_id, stored.object_tag_id) == (criterion.group_id, criterion.object_tag_id)
    assert get_blocked_courses(skills["deduction"]) == []


def test_unknown_parent_and_conflict_are_reported_together(
    competency_taxonomy, skills, course_x, default_rule_profile,
) -> None:
    for identifier in ("critical-thinking", "deduction", "synthesis"):
        hold(skills[identifier], course_x, default_rule_profile)
    success, _task, plan = reimport(
        competency_taxonomy,
        rows(deduction={"parent_id": "critical-thinking"}, synthesis={"parent_id": "metacognition"}),
    )
    assert success is False
    messages = [str(e) for e in plan.errors]
    assert len(messages) == 2
    assert conflict(6, DEDUCTION, CRITICAL_THINKING, course_x) in messages
    assert len([message for message in messages if "metacognition" in message]) == 1


def test_parent_cycle_still_gets_a_complete_preview(
    competency_taxonomy, skills, course_x, default_rule_profile,
) -> None:
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["synthesis"], course_x, default_rule_profile)
    success, _task, plan = reimport(
        competency_taxonomy, rows(evaluation={"parent_id": "deduction"}, synthesis={"parent_id": "analysis"}),
    )
    assert success is False
    messages = [str(e) for e in plan.errors]
    assert len([message for message in messages if "creates a parent cycle" in message]) == 1
    assert conflict(7, SYNTHESIS, CRITICAL_THINKING, course_x) in messages


def test_tag_with_criteria_on_a_cycle_is_skipped(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["evaluation"], course_x, default_rule_profile)
    hold(skills["deduction"], course_x, default_rule_profile)
    success, _task, plan = reimport(competency_taxonomy, rows(evaluation={"parent_id": "deduction"}))
    assert success is False
    messages = [str(e) for e in plan.errors]
    assert len(messages) == 1
    assert "creates a parent cycle" in messages[0]


def test_tag_below_a_cycle_keeps_its_conflicts(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    hold(skills["critical-thinking"], course_x, default_rule_profile)
    hold(skills["synthesis"], course_x, default_rule_profile)
    success, _task, plan = reimport(
        competency_taxonomy,
        rows(
            critical_thinking={"parent_id": "evaluation"},
            evaluation={"parent_id": "critical-thinking"},
            synthesis={"parent_id": "critical-thinking"},
        ),
    )
    assert success is False
    messages = [str(e) for e in plan.errors]
    assert len([message for message in messages if "creates a parent cycle" in message]) == 1
    assert conflict(7, SYNTHESIS, CRITICAL_THINKING, course_x) in messages


def test_nearest_ancestor_is_reported_first(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    for identifier in ("cognitive-skills", "critical-thinking", "deduction"):
        hold(skills[identifier], course_x, default_rule_profile)
    assert_rejected(
        competency_taxonomy,
        rows(deduction={"parent_id": "inference"}),
        [
            conflict(2, CRITICAL_THINKING, COGNITIVE_SKILLS, course_x),
            conflict(6, DEDUCTION, CRITICAL_THINKING, course_x),
            conflict(6, DEDUCTION, COGNITIVE_SKILLS, course_x),
        ],
    )


def test_conflicts_are_reported_in_file_row_order(competency_taxonomy, skills, course_x, default_rule_profile) -> None:
    for identifier in ("critical-thinking", "inference", "analysis"):
        hold(skills[identifier], course_x, default_rule_profile)
    assert_rejected(
        competency_taxonomy,
        rows(),
        [
            conflict(3, INFERENCE, CRITICAL_THINKING, course_x),
            conflict(4, ("Analysis", "analysis"), CRITICAL_THINKING, course_x),
        ],
    )


def test_tags_without_a_row_are_reported_after_tags_with_one(
    competency_taxonomy, skills, course_x, default_rule_profile,
) -> None:
    for identifier in ("critical-thinking", "inference", "deduction"):
        hold(skills[identifier], course_x, default_rule_profile)
    file_rows = [row for row in rows(deduction={"parent_id": "critical-thinking"}) if row["id"] != "inference"]
    assert_rejected(
        competency_taxonomy,
        file_rows,
        [
            conflict(5, DEDUCTION, CRITICAL_THINKING, course_x),
            conflict(None, INFERENCE, CRITICAL_THINKING, course_x),
        ],
        replace=False,
    )


def test_tags_without_a_row_are_reported_in_key_order(
    competency_taxonomy, course_x, default_rule_profile,
) -> None:
    ancestor = Tag.objects.create(taxonomy=competency_taxonomy, value="Reasoning", external_id="reasoning")
    zeta = Tag.objects.create(taxonomy=competency_taxonomy, value="Zeta", external_id="zeta", parent=ancestor)
    alpha = Tag.objects.create(taxonomy=competency_taxonomy, value="Alpha", external_id="alpha", parent=ancestor)
    for tag in (ancestor, zeta, alpha):
        hold(tag, course_x, default_rule_profile)
    reasoning = ("Reasoning", "reasoning")
    assert_rejected(
        competency_taxonomy,
        [{"id": "reasoning", "value": "Reasoning", "parent_id": None}],
        [
            conflict(None, ("Alpha", "alpha"), reasoning, course_x),
            conflict(None, ("Zeta", "zeta"), reasoning, course_x),
        ],
        replace=False,
    )


def test_validator_makes_one_query(skills, course_x, default_rule_profile, django_assert_num_queries) -> None:
    for identifier in ("critical-thinking", "deduction", "synthesis"):
        hold(skills[identifier], course_x, default_rule_profile)
    file_rows = rows(deduction={"parent_id": "critical-thinking"}, synthesis={"parent_id": "critical-thinking"})
    tag_items = [TagItem(index=index, **row) for index, row in enumerate(file_rows)]
    plan = TagImportPlan(skills["deduction"].taxonomy)
    plan.generate_actions(tag_items, replace=True)
    with django_assert_num_queries(1):
        validate_tag_import_dominance(plan)
