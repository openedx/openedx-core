"""
Rule payload shapes for CBE evaluation rules, and the parser that validates a raw payload against
the shape its rule_type defines.

See :ref:`openedx-learning-adr-0002` Decision 3 for the payload contract this enforces. Each
supported rule_type has exactly one spec class here (currently only :class:`GradeRule`) and one
matching entry in ``_RULE_PAYLOAD_SPECS``; :data:`RuleType` declares only the rule types that have
both, so a rule type can never be offered as a choice (see ``models/criteria.py``, which derives
both models' ``choices=`` from this same registry) without also being possible to save. Adding a
new rule type (for example ``MasteryLevel``) is one spec class plus one registry entry, and the
``RuleType`` member that goes with them.
"""
from __future__ import annotations

from typing import Any

from attrs import Attribute, define, field, fields
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

__all__ = [
    "GradeRule",
    "RuleType",
    "parse_rule_payload",
    "validate_rule_payload",
]


class RuleType(models.TextChoices):
    """
    The evaluation rule types a CompetencyRuleProfile or CompetencyCriterion override can use.

    Declares exactly the rule types with a defined rule_payload shape below, i.e. exactly the keys
    of ``_RULE_PAYLOAD_SPECS``: see this module's own docstring for why the two are never allowed
    to drift apart.
    """

    GRADE = "Grade", _("Grade")


def _validate_op(_instance: object, _attribute: Attribute, value: object) -> None:
    """Reject an 'op' outside the Grade rule's allowed comparison operators."""
    if value not in {"gte", "lte", "eq"}:
        raise ValueError(_("The 'op' in a 'Grade' rule_payload must be one of: gte, lte, eq."))


def _validate_grade_value(_instance: object, _attribute: Attribute, value: object) -> None:
    """Reject a Grade rule's 'value' unless it's a non-boolean number in [0.0, 1.0]."""
    # isinstance(True, int) is True in Python, so a bool would otherwise pass the numeric check below.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(_("The 'value' in a 'Grade' rule_payload must be a number, not a boolean."))
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            _(
                "The 'value' in a 'Grade' rule_payload must be a fraction between 0.0 and 1.0 inclusive "
                "(e.g. 0.8 for a passing grade of 80%%), not %(value)r."
            )
            % {"value": value}
        )


def _validate_scale(_instance: object, _attribute: Attribute, value: object) -> None:
    """Reject a Grade rule's 'scale' unless it's exactly 'percent'."""
    if value != "percent":
        raise ValueError(_("The 'scale' in a 'Grade' rule_payload must be 'percent'."))


@define(frozen=True, kw_only=True)
class GradeRule:
    """
    The rule_payload shape for RuleType.GRADE, per ADR-0002 Decision 3.

    Constructing one *is* the validation: kw_only means an unknown key raises TypeError
    ("unexpected keyword argument") and a missing key raises TypeError ("missing ... required
    keyword-only argument") from Python's own call handling, and a present-but-bad value raises
    ValueError or TypeError from the field validators below. Frozen because this describes a
    fixed spec and is never mutated after construction.
    """

    op: str = field(validator=_validate_op)
    value: float = field(validator=_validate_grade_value)
    scale: str = field(validator=_validate_scale)


# One spec class per RuleType with a defined rule_payload shape. Add a class and an entry here (and
# the matching RuleType member above) to support a new rule type.
_RULE_PAYLOAD_SPECS: dict[str, type] = {
    RuleType.GRADE: GradeRule,
}


def parse_rule_payload(rule_type: str, payload: Any) -> Any:
    """
    Validate ``payload`` against the shape ADR-0002 Decision 3 defines for ``rule_type``, and
    return the constructed, frozen spec object (for example a :class:`GradeRule`) on success.

    Raises ``django.core.exceptions.ValidationError`` on any mismatch, including a ``rule_type``
    with no defined payload shape at all. Prefer this over :func:`validate_rule_payload` at a call
    site that wants the parsed, typed fields (for example read-time rule evaluation), not just the
    pass/fail check.
    """
    spec_class = _RULE_PAYLOAD_SPECS.get(rule_type)
    if spec_class is None:
        raise ValidationError(
            _("Rule type '%(rule_type)s' is not supported yet; only 'Grade' has a defined rule_payload shape.")
            % {"rule_type": rule_type}
        )
    # Checked separately, before construction: `spec_class(**payload)` on a non-dict payload
    # (e.g. a list) raises "argument after ** must be a mapping", which is a confusing message to
    # surface to an author.
    if not isinstance(payload, dict):
        raise ValidationError(_("A '%(rule_type)s' rule_payload must be a JSON object.") % {"rule_type": rule_type})

    # Also checked separately, before construction, rather than left to Python's own kw_only
    # TypeError: that TypeError's text is "GradeRule.__init__() missing/got an unexpected keyword
    # argument ...", a Python traceback fragment that leaks an internal class name to whoever
    # edits this payload (a course author, via the admin). Deriving the expected keys from the
    # spec class itself keeps that class the single source of truth for the key set, while owning
    # the message in our own domain language instead of Python's.
    expected_keys = {attr.name for attr in fields(spec_class)}
    missing_keys = sorted(expected_keys - payload.keys())
    unexpected_keys = sorted(payload.keys() - expected_keys)
    if missing_keys or unexpected_keys:
        problems = []
        if missing_keys:
            problems.append(_("missing %(keys)s") % {"keys": ", ".join(missing_keys)})
        if unexpected_keys:
            problems.append(_("unexpected %(keys)s") % {"keys": ", ".join(unexpected_keys)})
        raise ValidationError(
            _("A '%(rule_type)s' rule_payload has the wrong keys: %(problems)s.")
            % {"rule_type": rule_type, "problems": "; ".join(str(problem) for problem in problems)}
        )

    # Past the key check above, this can only fail on a value a field validator rejects.
    try:
        return spec_class(**payload)
    except (TypeError, ValueError) as exc:
        # Surface the underlying message (our custom validators' text) rather than replacing it
        # with something generic: that message is what full_clean() or save() surfaces to an
        # admin or API caller.
        raise ValidationError(str(exc)) from exc


def validate_rule_payload(rule_type: str, payload: Any) -> None:
    """
    Validate ``payload`` against the shape ADR-0002 Decision 3 defines for ``rule_type``.

    A thin wrapper around :func:`parse_rule_payload`, for a call site (``clean()``/``save()`` on
    both CBE models) that only needs the pass/fail check and has no use for the parsed object.
    """
    parse_rule_payload(rule_type, payload)
