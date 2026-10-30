"""
Tests for the pure combination rules used by the rollup. These need no database.
"""
from itertools import permutations

import pytest

from openedx_learning.applets.cbe.rollup import combine_children, combine_parent_competency_status
from openedx_learning.models import LogicOperator, MasteryStatus

DEMONSTRATED = MasteryStatus.DEMONSTRATED
PARTIAL = MasteryStatus.PARTIALLY_ATTEMPTED
FAILED = MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED

AND = LogicOperator.AND
OR = LogicOperator.OR


@pytest.mark.parametrize(
    ("operator", "children", "expected"),
    [
        (AND, [DEMONSTRATED, DEMONSTRATED], DEMONSTRATED),
        (AND, [DEMONSTRATED, None], PARTIAL),
        (AND, [None, None], None),
        (AND, [DEMONSTRATED, FAILED], FAILED),
        (AND, [None, FAILED], FAILED),
        (AND, [PARTIAL, DEMONSTRATED], PARTIAL),
        (AND, [FAILED, FAILED], FAILED),
        (OR, [DEMONSTRATED, None], DEMONSTRATED),
        (OR, [FAILED, DEMONSTRATED], DEMONSTRATED),
        (OR, [FAILED, FAILED], FAILED),
        (OR, [FAILED, None], PARTIAL),
        (OR, [PARTIAL, FAILED], PARTIAL),
        (OR, [None, None], None),
        (None, [FAILED, DEMONSTRATED], DEMONSTRATED),
        (None, [FAILED], FAILED),
        (AND, [], None),
        (OR, [], None),
    ],
)
def test_combine_children(operator, children, expected) -> None:
    """Each row is one acceptance-criteria shape; a null operator behaves as OR."""
    assert combine_children(operator, children) == expected


@pytest.mark.parametrize("operator", [AND, OR, None])
@pytest.mark.parametrize(
    "children", [[DEMONSTRATED, FAILED, None], [None, FAILED, PARTIAL], [DEMONSTRATED, DEMONSTRATED, PARTIAL]],
)
def test_combine_children_ignores_child_order(operator, children) -> None:
    """The result depends only on the set of child values, never on the order they are read in."""
    results = {combine_children(operator, list(order)) for order in permutations(children)}
    assert len(results) == 1


@pytest.mark.parametrize(
    ("own", "from_sub_competencies", "expected"),
    [
        (DEMONSTRATED, None, DEMONSTRATED),
        (DEMONSTRATED, PARTIAL, DEMONSTRATED),
        (None, DEMONSTRATED, DEMONSTRATED),
        (PARTIAL, DEMONSTRATED, DEMONSTRATED),
        (PARTIAL, None, PARTIAL),
        (None, PARTIAL, PARTIAL),
        (PARTIAL, PARTIAL, PARTIAL),
        (None, None, None),
    ],
)
def test_combine_parent_competency_status(own, from_sub_competencies, expected) -> None:
    """A parent is demonstrated when either source is, and partially attempted on any progress."""
    assert combine_parent_competency_status(own, from_sub_competencies) == expected
