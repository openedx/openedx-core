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
    "GradePayload",
    "RuleType",
    "validate_rule_payload",
]


class RuleType(models.TextChoices):
    """
    The evaluation rule types a CompetencyRuleProfile or CompetencyCriterion override can use.

    Declares exactly the rule types with a defined rule_payload shape below, i.e. exactly the
    cases ``validate_rule_payload`` matches on: a member with no matching case falls through to
    that function's ``case _``, which always rejects, so drift between the two fails a test
    instead of shipping.
    """

    GRADE = "Grade", _("Grade")


GradeOperator = Literal["gte", "lte", "eq"]

_GRADE_OPERATORS: frozenset[str] = frozenset(get_args(GradeOperator))


class GradePayload(TypedDict):
    """
    The stored shape of a ``RuleType.GRADE`` rule_payload, for annotating a dict already known to be
    well-formed. Declarative only: ``_validate_grade_payload`` is what rejects a bad payload, while
    these annotations are the single declaration of the payload's key set.
    """

    op: GradeOperator
    value: float
    scale: Literal["percent"]


def _validate_grade_payload(payload: dict[str, object]) -> None:
    """Validate a Grade payload's op, value, and scale. Keys are already checked."""
    if payload["op"] not in _GRADE_OPERATORS:
        raise ValidationError(_("The 'op' in a 'Grade' rule_payload must be one of: gte, lte, eq."))
    value = payload["value"]
    # isinstance(True, int) is True in Python, so a bool needs excluding explicitly. The type
    # checker does not catch this either: bool subclasses int, which satisfies GradePayload's
    # ``value: float`` under mypy's numeric tower.
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


_GRADE_PAYLOAD_KEYS: frozenset[str] = frozenset(GradePayload.__annotations__)


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
    Raise ValidationError unless ``payload`` matches the shape ADR-0002 Decision 3 defines for
    ``rule_type``, including when ``rule_type`` has no defined shape at all.
    """
    match rule_type:
        case RuleType.GRADE:
            grade_payload = _validate_payload_keys(rule_type, payload, _GRADE_PAYLOAD_KEYS)
            _validate_grade_payload(grade_payload)
        case _:
            raise ValidationError(
                _("Rule type '%(rule_type)s' is not supported yet; only 'Grade' has a defined rule_payload shape.")
                % {"rule_type": rule_type}
            )
