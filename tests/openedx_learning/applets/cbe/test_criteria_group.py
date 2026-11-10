"""
Tests for CompetencyCriteriaGroup, the internal AND/OR node of a Competency Criteria
tree.

Each test name states the behavior it pins. Reading top to bottom gives the model's contract:
its columns, its tree shape, the two constraints ADR-0002 Decision 2 deliberately leaves out,
one root per competency and one course-level group per competency and course, then its indexes
and history.

Delete behavior is not covered here. Nothing in this module deletes a row that another row
points at. See test_criteria_group_deletion.py, in this same change, for this model's own
`on_delete` values and the tests that exercise them.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from openedx_catalog.models import CourseRun
from openedx_learning.models import CompetencyCriteriaGroup, LogicOperator
from openedx_tagging.models import Tag

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------------------------
# Columns


# ---------------------------------------------------------------------------------------------


def test_group_archived_defaults_to_false(tag: Tag) -> None:
    group = CompetencyCriteriaGroup.objects.create(tag=tag)
    assert group.archived is False


# ---------------------------------------------------------------------------------------------
# Tree shape, and the two constraints ADR-0002 Decision 2 deliberately leaves out


# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "logic_operator",
    [
        pytest.param(LogicOperator.AND, id="and"),
        pytest.param(LogicOperator.OR, id="or"),
        pytest.param(None, id="null"),
    ],
)
def test_group_logic_operator_accepts_and_or_and_null_regardless_of_child_count(
    logic_operator: str | None, tag: Tag
) -> None:
    """
    logic_operator accepts AND, OR, or null. Nothing at the data layer constrains it by how many
    children the group actually has: a group with zero children and a group with two children both
    save successfully with any of the three values. See ADR-0002 Decision 2; the database cannot
    see a group's future children at save time (a child's parent FK cannot point at a row that
    doesn't have a primary key yet), so this is enforced nowhere at this layer, deliberately.
    """
    childless = CompetencyCriteriaGroup.objects.create(tag=tag, logic_operator=logic_operator)
    assert childless.pk is not None

    # A second competency, since each competency tag may have only one root group.
    other_tag = Tag.objects.create(taxonomy=tag.taxonomy, value="Writing Prose")
    parent = CompetencyCriteriaGroup.objects.create(tag=other_tag, logic_operator=logic_operator)
    CompetencyCriteriaGroup.objects.create(tag=other_tag, parent=parent)
    CompetencyCriteriaGroup.objects.create(tag=other_tag, parent=parent)
    parent.refresh_from_db()
    assert parent.logic_operator == logic_operator
    assert CompetencyCriteriaGroup.objects.filter(parent=parent).count() == 2


def test_a_root_group_has_a_null_parent_and_a_child_points_at_the_group_it_was_created_under(tag: Tag) -> None:
    """
    A CompetencyCriteriaGroup's parent is null for a root and points at its parent for a child.
    See ADR-0002 Decision 2.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag, logic_operator=None)
    assert root.parent is None

    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, logic_operator=LogicOperator.AND)
    assert child.parent == root


def test_group_has_no_unique_constraint_on_parent_and_ordering(tag: Tag) -> None:
    """
    No UniqueConstraint on (parent, ordering) exists: two sibling groups may share the same
    `ordering` value. A parent's clean() cannot see its own future children at save time (a
    child's FK can't point at a not-yet-existing parent row), so there is no single-row state to
    check a per-parent uniqueness rule against, and none is declared. See ADR-0002 Decision 2.
    """
    parent = CompetencyCriteriaGroup.objects.create(tag=tag)
    sibling_a = CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent, ordering=1)
    sibling_b = CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent, ordering=1)
    assert sibling_a.ordering == sibling_b.ordering == 1


# ---------------------------------------------------------------------------------------------
# One root per competency, and one course-level group per competency and course


# ---------------------------------------------------------------------------------------------


def test_a_root_group_gets_a_scope_code_and_other_groups_do_not(tag: Tag, course_run: CourseRun) -> None:
    second_run = CourseRun.objects.create(catalog_course=course_run.catalog_course, run_code="Spring2027")
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    second_course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=second_run, parent=root)
    leaves = [
        CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level, archived=archived)
        for archived in (False, False, True)
    ]

    assert root.scope_code == f"tag:{tag.pk}"
    assert course_level.scope_code is None
    assert second_course_level.scope_code is None
    assert all(leaf.scope_code is None for leaf in leaves)


def test_a_second_root_for_a_competency_is_rejected(tag: Tag) -> None:
    CompetencyCriteriaGroup.objects.create(tag=tag)

    with pytest.raises(IntegrityError), transaction.atomic():
        CompetencyCriteriaGroup.objects.create(tag=tag)

    assert CompetencyCriteriaGroup.objects.filter(tag=tag, parent=None).count() == 1


def test_a_second_root_is_rejected_when_the_existing_root_is_archived(tag: Tag) -> None:
    root = CompetencyCriteriaGroup.objects.create(tag=tag, archived=True)

    with pytest.raises(IntegrityError), transaction.atomic():
        CompetencyCriteriaGroup.objects.create(tag=tag)

    root.refresh_from_db()
    assert root.archived is True
    assert root.scope_code == f"tag:{tag.pk}"


@pytest.mark.parametrize("existing_archived", [False, True], ids=["live", "archived"])
@pytest.mark.parametrize("insert", ["create", "bulk_create"])
def test_a_second_course_level_group_for_the_same_course_is_rejected(
    existing_archived: bool, insert: str, tag: Tag, course_run: CourseRun
) -> None:
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root, archived=existing_archived)

    with pytest.raises(IntegrityError), transaction.atomic():
        if insert == "create":
            CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
        else:
            CompetencyCriteriaGroup.objects.bulk_create(
                [CompetencyCriteriaGroup(tag=tag, course=course_run, parent=root)]
            )


def test_archiving_a_root_keeps_it_blocking_a_second_root(tag: Tag) -> None:
    root = CompetencyCriteriaGroup.objects.create(tag=tag)

    root.archived = True
    root.save(update_fields=["archived"])

    root.refresh_from_db()
    assert root.scope_code == f"tag:{tag.pk}"
    with pytest.raises(IntegrityError), transaction.atomic():
        CompetencyCriteriaGroup.objects.create(tag=tag)


@pytest.mark.parametrize("changed_field", ["tag", "parent", "course"])
def test_a_saved_group_cannot_change_its_tag_parent_or_course(
    changed_field: str, tag: Tag, course_run: CourseRun
) -> None:
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)

    if changed_field == "tag":
        course_level.tag = Tag.objects.create(taxonomy=tag.taxonomy, value="Writing Prose")
    elif changed_field == "parent":
        course_level.parent = None
    else:
        course_level.course = CourseRun.objects.create(catalog_course=course_run.catalog_course, run_code="Spring2027")

    with pytest.raises(ValidationError):
        course_level.save()
    course_level.refresh_from_db()
    assert course_level.tag_id == tag.pk
    assert course_level.parent_id == root.pk
    assert course_level.course_id == course_run.pk


def test_bulk_archiving_a_root_keeps_it_blocking_a_second_root(tag: Tag) -> None:
    root = CompetencyCriteriaGroup.objects.create(tag=tag)

    CompetencyCriteriaGroup.objects.filter(pk=root.pk).update(archived=True)

    root.refresh_from_db()
    assert root.scope_code == f"tag:{tag.pk}"
    with pytest.raises(IntegrityError), transaction.atomic():
        CompetencyCriteriaGroup.objects.create(tag=tag)


def test_reactivating_an_archived_root_keeps_it_the_only_root(tag: Tag) -> None:
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    root.archived = True
    root.save(update_fields=["archived"])

    root.archived = False
    root.save()

    root.refresh_from_db()
    assert root.archived is False
    assert root.scope_code == f"tag:{tag.pk}"
    with pytest.raises(IntegrityError), transaction.atomic():
        CompetencyCriteriaGroup.objects.create(tag=tag)
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, parent=None).count() == 1


def test_a_root_cannot_be_given_a_parent_behind_save(tag: Tag) -> None:
    other_tag = Tag.objects.create(taxonomy=tag.taxonomy, value="Writing Prose")
    root_a = CompetencyCriteriaGroup.objects.create(tag=tag)
    root_b = CompetencyCriteriaGroup.objects.create(tag=other_tag)

    with pytest.raises(IntegrityError), transaction.atomic():
        CompetencyCriteriaGroup.objects.filter(pk=root_b.pk).update(parent=root_a)


# ---------------------------------------------------------------------------------------------
# Indexes and history


# ---------------------------------------------------------------------------------------------


def test_editing_a_group_writes_a_historical_row(tag: Tag) -> None:
    """
    HistoricalRecords() is applied to CompetencyCriteriaGroup: the Historical model is registered
    under its expected name, and creating then editing a group leaves two rows in it. See
    ADR-0003 Decision 1.

    The Historical model is looked up through the app registry rather than the `.history`
    attribute because simple_history installs `.history` as a runtime descriptor with no type
    stubs, which mypy cannot type.
    """
    historical_group = apps.get_model("openedx_learning", "HistoricalCompetencyCriteriaGroup")
    group = CompetencyCriteriaGroup.objects.create(tag=tag)

    group.name = "Poetry Mastery"
    group.save()

    assert historical_group.objects.filter(id=group.pk).count() == 2
