"""
Rule payload shapes for CBE evaluation rules, and the validator that checks a raw payload against
the shape its rule_type defines. See :ref:`openedx-learning-adr-0002` Decision 3 for the payload
contract. ``RuleType`` declares exactly the rule types with a shape defined here, so a rule type
can never be offered as a choice without also being saveable. These messages reach an API caller
or admin form, so they must not leak internal class or function names.
"""
from __future__ import annotations

from typing import Literal, TypedDict, get_args

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

__all__ = [
    "GradeRulePayload",
    "RuleType",
    "validate_rule_payload",
]


class RuleType(models.TextChoices):
    """
    The evaluation rule types a CompetencyRuleProfile or CompetencyCriterion override can use.

    Only rule types with a defined ``rule_payload`` shape are listed here. ADR-0002 Decision 3
    also names ``View`` and ``MasteryLevel`` as rule types to consider adding later, once someone defines
    their payload shape; until then, adding a member here without a matching branch in
    ``validate_rule_payload`` fails a test rather than shipping an option nothing can validate.
    """

    GRADE = "Grade", _("Grade")


GradeOperator = Literal["gte", "lte", "eq"]

_GRADE_OPERATORS: frozenset[str] = frozenset(get_args(GradeOperator))


class GradeRulePayload(TypedDict):
    """
    The stored shape of a ``RuleType.GRADE`` rule_payload, for annotating a dict already known to be
    well-formed. Declarative only: ``_validate_grade_rule_payload`` is what rejects a bad payload, while
    these annotations are the single declaration of the payload's key set.
    """

    op: GradeOperator
    value: float
    scale: Literal["percent"]


def _validate_grade_rule_payload(payload: dict[str, object]) -> None:
    """Validate a Grade payload's op, value, and scale. Keys are already checked."""
    if payload["op"] not in _GRADE_OPERATORS:
        raise ValidationError(_("The 'op' in a 'Grade' rule_payload must be one of: gte, lte, eq."))
    value = payload["value"]
    # isinstance(True, int) is True in Python, so a bool would slip through a plain numeric
    # check and read as the fraction 1.0, silently meaning "100%". bool subclasses int, so
    # the type checker doesn't catch this either.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise ValidationError(
            _(
                "The 'value' in a 'Grade' rule_payload must be a fraction between 0.0 and 1.0 inclusive "
                "(e.g. 0.8 for a passing grade of 80%%), not %(value)r."
            )
            % {"value": value}
        )
    if payload["scale"] != "percent":
        raise ValidationError(_("The 'scale' in a 'Grade' rule_payload must be 'percent'."))


_GRADE_PAYLOAD_KEYS: frozenset[str] = frozenset(GradeRulePayload.__annotations__)


def _validate_payload_keys(rule_type: str, payload: object, expected_keys: frozenset[str]) -> dict[str, object]:
    """Raise ValidationError unless ``payload`` is a JSON object with exactly ``expected_keys``."""
    if not isinstance(payload, dict):
        raise ValidationError(_("A '%(rule_type)s' rule_payload must be a JSON object.") % {"rule_type": rule_type})
    missing = sorted(expected_keys - payload.keys())
    unexpected = sorted(payload.keys() - expected_keys)
    if missing or unexpected:
        raise ValidationError(
            _("A '%(rule_type)s' rule_payload has the wrong keys: missing %(missing)s; unexpected %(unexpected)s.")
            % {
                "rule_type": rule_type,
                "missing": ", ".join(missing) or _("none"),
                "unexpected": ", ".join(unexpected) or _("none"),
            }
        )
    return payload


def validate_rule_payload(rule_type: str, payload: object) -> None:
    """
    Raise ValidationError unless ``payload`` is a JSON object with exactly the keys ``rule_type``'s
    shape requires and values within that shape's constraints -- for ``Grade``, ``op`` one of
    ``gte``, ``lte``, ``eq``, ``value`` a fraction from 0.0 to 1.0, and ``scale`` equal to
    ``"percent"`` -- including when ``rule_type`` has no defined shape at all.
    """
    match rule_type:
        case RuleType.GRADE:
            grade_payload = _validate_payload_keys(rule_type, payload, _GRADE_PAYLOAD_KEYS)
            _validate_grade_rule_payload(grade_payload)
        case _:
            raise ValidationError(
                _("Rule type '%(rule_type)s' is not supported yet; only 'Grade' has a defined rule_payload shape.")
                % {"rule_type": rule_type}
            )
