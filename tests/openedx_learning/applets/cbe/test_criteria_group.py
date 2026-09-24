"""
Tests for CompetencyCriteriaGroup, the internal AND/OR node of a Competency Criteria
tree.

Each test name states the behavior it pins. Reading top to bottom gives the model's contract:
its columns, its tree shape, the two constraints ADR-0002 Decision 2 deliberately leaves out,
then its indexes and history.

Delete behavior is not covered here. Nothing in this module deletes a row that another row
points at. See test_criteria_group_deletion.py, in this same change, for this model's own
`on_delete` values and the tests that exercise them.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.apps import apps
from django.db import models

from openedx_learning.models import CompetencyCriteriaGroup, LogicOperator
from openedx_tagging.models import Tag

pytestmark = pytest.mark.django_db


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

    parent = CompetencyCriteriaGroup.objects.create(tag=tag, logic_operator=logic_operator)
    CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent)
    CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent)
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
    unique_constraints = [
        c for c in CompetencyCriteriaGroup._meta.constraints if isinstance(c, models.UniqueConstraint)
    ]
    assert not any({"parent", "ordering"} <= set(c.fields) for c in unique_constraints)

    parent = CompetencyCriteriaGroup.objects.create(tag=tag)
    sibling_a = CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent, ordering=1)
    sibling_b = CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent, ordering=1)
    assert sibling_a.ordering == sibling_b.ordering == 1


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
