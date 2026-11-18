"""
Value objects passed across the CBE applet's public API boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, kw_only=True, slots=True)
class GradedObjectScore:
    """
    One learner's final, override-adjusted fraction earned on one graded object.

    ``fraction`` is the caller's final answer for how much of ``object_id`` this learner earned;
    this library cannot detect whether an override was already folded into it. Leave an object
    worth zero possible points out of the list entirely rather than passing it with a
    ``Decimal("0")`` fraction, since it has no meaningful fraction to evaluate. ``Decimal("0")``
    itself is a valid, meaningful input: it means the learner attempted the object and earned
    nothing on it.

    ``fraction`` must be built from a string or from ``Decimal`` arithmetic, never from a
    ``float``: ``Decimal(0.7)`` is a ``Decimal`` instance, so it passes this class's own type
    check, but its value is ``0.69999999999999995559...``, not ``0.7``.
    """

    object_id: str
    fraction: Decimal

    def __post_init__(self) -> None:
        """Enforce this class's type and range contract once, at construction time."""
        if not isinstance(self.fraction, Decimal):
            raise TypeError(f"fraction must be a Decimal, not {type(self.fraction)!r}")
        # is_finite() must run first: 0 <= Decimal("NaN") raises decimal.InvalidOperation, not
        # ValueError, so checking finiteness first is what makes every invalid input, including
        # NaN and Infinity, actually raise the documented ValueError.
        if not self.fraction.is_finite() or not 0 <= self.fraction <= 1:
            raise ValueError(f"fraction must be finite and between 0 and 1 inclusive, not {self.fraction!r}")


@dataclass(frozen=True, kw_only=True, slots=True)
class CriterionDeletionResult:
    """
    The outcome of removing a CompetencyCriterion: whether its row was hard-deleted or archived.

    UNSTABLE: the criteria-authoring family is incomplete, so this shape may change without a
    deprecation cycle.
    """

    id: int
    archived: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupDeletionResult:
    """
    The outcome of removing a CompetencyCriteriaGroup subtree: hard-deleted or archived, with counts.

    The counts cover the target subtree only: ``cascaded_group_count`` counts the target's descendant
    groups, not the target itself and not the ancestors the removal cascaded to, and
    ``cascaded_criteria_count`` is the number of criteria in the subtree.

    UNSTABLE: the criteria-authoring family is incomplete, so this shape may change without a
    deprecation cycle.
    """

    id: int
    archived: bool
    cascaded_group_count: int
    cascaded_criteria_count: int


@dataclass(frozen=True, kw_only=True, slots=True)
class LearnerTaxonomy:
    """
    A competency taxonomy that has at least one competency with criteria in a course run.

    UNSTABLE: see :func:`openedx_learning.applets.cbe.api.get_course_competency_criteria`.
    """

    id: int
    name: str


@dataclass(frozen=True, kw_only=True, slots=True)
class LearnerCompetency:
    """
    A competency (a Tag of a competency taxonomy) that has criteria in a course run.

    ``id`` is the Tag id. ``lineage`` holds the tag values from the taxonomy's top level down to the
    competency itself, so a top-level competency's lineage is a single entry.

    UNSTABLE: see :func:`openedx_learning.applets.cbe.api.get_course_competency_criteria`.
    """

    id: int
    name: str
    external_id: str | None
    lineage: list[str]
    taxonomy_id: int


@dataclass(frozen=True, kw_only=True, slots=True)
class LearnerCriteriaGroup:
    """
    One CompetencyCriteriaGroup of a competency's criteria tree in a course run.

    ``competency_id`` is the id of the competency the group's tree belongs to: the tag of its course-level group.
    ``parent_id`` is None only for the root group. ``logic_operator`` is the stored value: ``"AND"``,
    ``"OR"``, or None.

    UNSTABLE: see :func:`openedx_learning.applets.cbe.api.get_course_competency_criteria`.
    """

    id: int
    competency_id: int
    parent_id: int | None
    name: str
    logic_operator: str | None
    ordering: int


@dataclass(frozen=True, kw_only=True, slots=True)
class LearnerCriterion:
    """
    One CompetencyCriterion of a course run.

    ``usage_key`` is the object id of the criterion's ObjectTag. ``rule_type`` and ``rule_payload`` are
    the effective rule: the rule profile's when the criterion has one, its own override otherwise.

    UNSTABLE: see :func:`openedx_learning.applets.cbe.api.get_course_competency_criteria`.
    """

    id: int
    group_id: int
    usage_key: str
    rule_type: str
    rule_payload: dict


@dataclass(frozen=True, kw_only=True, slots=True)
class LearnerCourseCriteria:
    """
    The competency criteria of one course run, as four flat lists that reference each other by id.

    Every ``parent_id``, ``competency_id``, ``group_id`` and ``taxonomy_id`` in one list names an
    item in the list it refers to.

    UNSTABLE: see :func:`openedx_learning.applets.cbe.api.get_course_competency_criteria`.
    """

    taxonomies: list[LearnerTaxonomy]
    competencies: list[LearnerCompetency]
    groups: list[LearnerCriteriaGroup]
    criteria: list[LearnerCriterion]
