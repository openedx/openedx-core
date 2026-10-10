"""
Tests for the CBE rule payload contract: RuleType and validate_rule_payload.

ADR-0002 Decision 3 defines one payload shape per rule_type. The single supported type is
"Grade", whose payload is {"op": ..., "value": ..., "scale": ...} where op is one of gte, lte or
eq, value is a fraction from 0.0 to 1.0 rather than a number out of 100, and scale is "percent".
"""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from openedx_learning.applets.cbe.rule_payloads import (
    _GRADE_OPERATOR_FUNCS,
    _GRADE_OPERATORS,
    GradeRulePayload,
    RuleType,
    evaluate_rule,
    validate_rule_payload,
)

_GRADE_PAYLOAD: GradeRulePayload = {"op": "gte", "value": 0.8, "scale": "percent"}


def test_a_well_formed_grade_payload_is_accepted() -> None:
    """A Grade payload with a valid op, a fraction value, and the percent scale raises nothing."""
    validate_rule_payload(RuleType.GRADE, _GRADE_PAYLOAD)


@pytest.mark.parametrize(
    "op",
    [pytest.param("gte", id="gte"), pytest.param("lte", id="lte"), pytest.param("eq", id="eq")],
)
def test_every_documented_comparison_operator_is_accepted(op: str) -> None:
    """All three operators ADR-0002 Decision 3 lists are accepted, not just the seeded gte."""
    validate_rule_payload(RuleType.GRADE, {**_GRADE_PAYLOAD, "op": op})


@pytest.mark.parametrize(
    "value",
    [pytest.param(0.0, id="lower_bound"), pytest.param(1.0, id="upper_bound"), pytest.param(1, id="int_one")],
)
def test_the_ends_of_the_zero_to_one_range_are_accepted(value: float) -> None:
    """0.0 and 1.0 are both inside the range, and an int is a number as far as this rule cares."""
    validate_rule_payload(RuleType.GRADE, {**_GRADE_PAYLOAD, "value": value})


# One (rule_type, payload) pair per way ADR-0002 Decision 3 says a rule_payload can be invalid.
_INVALID_PAYLOADS = [
    pytest.param(RuleType.GRADE, {"op": "startswith", "value": 0.8, "scale": "percent"}, id="bad_op"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": 80, "scale": "percent"}, id="value_80_not_0_8"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": 1.5, "scale": "percent"}, id="value_above_range"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": -0.1, "scale": "percent"}, id="value_below_range"),
    pytest.param(RuleType.GRADE, {"op": "gte", "scale": "percent"}, id="missing_key"),
    pytest.param(RuleType.GRADE, {**_GRADE_PAYLOAD, "extra": 1}, id="extra_key"),
    pytest.param(RuleType.GRADE, ["not", "a", "dict"], id="non_dict"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": 0.8, "scale": "raw"}, id="wrong_scale"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": True, "scale": "percent"}, id="boolean_value"),
    # "View" is a plain string, not RuleType.VIEW: RuleType declares only rule types that have a
    # payload spec, so an unsupported rule type is by construction not a RuleType member at all.
    pytest.param("View", _GRADE_PAYLOAD, id="unsupported_rule_type"),
]


@pytest.mark.parametrize("rule_type, payload", _INVALID_PAYLOADS)
def test_every_documented_way_a_payload_can_be_wrong_raises_validation_error(
    rule_type: str, payload: object
) -> None:
    """
    Each invalid shape raises ValidationError rather than passing or raising something the caller
    would not expect: a bad op, a value given out of 100 instead of as a fraction, a value outside
    the range at either end, a missing or extra key, a non-dict payload, a wrong scale, a boolean
    masquerading as a number, and a rule_type with no defined payload shape.
    """
    with pytest.raises(ValidationError):
        validate_rule_payload(rule_type, payload)


def test_a_boolean_value_is_rejected_even_though_python_calls_it_an_int() -> None:
    """
    True is rejected. isinstance(True, int) is True in Python, so a bool would slip through a
    plain numeric check, and True would then read as the fraction 1.0, silently meaning "100%".
    """
    with pytest.raises(ValidationError):
        validate_rule_payload(RuleType.GRADE, {**_GRADE_PAYLOAD, "value": True})


def test_an_out_of_range_value_message_names_the_fraction_convention() -> None:
    """
    The message for a value given out of 100 (for example 80) names the 0.0 to 1.0 fraction
    convention, so an author who wrote 80 meaning 80% is told what to write instead.
    """
    with pytest.raises(ValidationError) as exc_info:
        validate_rule_payload(RuleType.GRADE, {"op": "gte", "value": 80, "scale": "percent"})

    assert "fraction between 0.0 and 1.0" in " ".join(exc_info.value.messages)


def test_a_wrong_keys_message_names_the_offending_keys() -> None:
    """
    The message for a wrong key set names both what is missing and what is unexpected, so an
    author can see which key to fix rather than being told only that the payload is invalid.
    """
    with pytest.raises(ValidationError) as exc_info:
        validate_rule_payload(RuleType.GRADE, {"op": "gte", "extra": 1})

    message = " ".join(exc_info.value.messages)
    assert "missing scale, value" in message
    assert "unexpected extra" in message


def test_an_unsupported_rule_type_says_only_grade_is_defined() -> None:
    """
    A rule_type with no payload shape is rejected with a message saying so, rather than being
    silently accepted. ADR-0002 Decision 3 lists View and MasteryLevel as future types; neither
    has a defined shape yet.
    """
    with pytest.raises(ValidationError) as exc_info:
        validate_rule_payload("MasteryLevel", {"level": 3})

    assert "not supported yet" in " ".join(exc_info.value.messages)


@pytest.mark.parametrize("rule_type", list(RuleType))
def test_every_rule_type_choice_has_a_validation_branch(rule_type: RuleType) -> None:
    """
    Guards against a future RuleType member shipping with no matching validate_rule_payload
    branch: adding one without a branch would offer an author a choice that always rejects,
    since it falls through to the catch-all "not supported yet" case regardless of payload.

    Runs against every current and future RuleType member automatically, since it parametrizes
    over list(RuleType) rather than naming Grade specifically. An empty payload is used because
    it is wrong for every currently defined shape; the assertion below only checks that the
    rejection reason is shape-specific (e.g. missing keys), not "unsupported rule type."
    """
    with pytest.raises(ValidationError) as exc_info:
        validate_rule_payload(rule_type, {})

    assert "not supported yet" not in " ".join(exc_info.value.messages)


@pytest.mark.parametrize(
    "op, fraction, expected",
    [
        pytest.param("gte", Decimal("0.81"), True, id="gte_above"),
        pytest.param("gte", Decimal("0.8"), True, id="gte_exact_threshold"),
        pytest.param("gte", Decimal("0.79"), False, id="gte_below"),
        pytest.param("lte", Decimal("0.8"), True, id="lte_exact_threshold"),
        pytest.param("lte", Decimal("0.79"), True, id="lte_below"),
        pytest.param("lte", Decimal("0.81"), False, id="lte_above"),
        pytest.param("eq", Decimal("0.8"), True, id="eq_exact"),
        pytest.param("eq", Decimal("0.79"), False, id="eq_not_exact"),
    ],
)
def test_evaluate_rule(op: str, fraction: Decimal, expected: bool) -> None:
    """
    evaluate_rule() applies each supported operator correctly, including the exact-threshold case
    that regresses comparing a Decimal fraction against the float this rule_payload's "value" comes
    back as from a JSONField: gte 0.8 against exactly Decimal("0.8") must return True, not False.
    """
    payload = {**_GRADE_PAYLOAD, "op": op}
    assert evaluate_rule(RuleType.GRADE, payload, fraction) is expected


@pytest.mark.parametrize(
    "rule_type, payload",
    [
        pytest.param("MasteryLevel", {"level": 3}, id="unsupported_rule_type"),
        pytest.param(RuleType.GRADE, {"op": "startswith", "value": 0.8, "scale": "percent"}, id="unsupported_operator"),
    ],
)
def test_evaluate_rule_raises_for_unappliable_rule(rule_type: str, payload: dict) -> None:
    """
    ValidationError propagates out of evaluate_rule() uncaught for an unsupported rule type or
    operator. validate_rule_payload() itself already has thorough, separate coverage above; this
    only confirms evaluate_rule() surfaces the same exception, not that the validator is correct.
    """
    with pytest.raises(ValidationError):
        evaluate_rule(rule_type, payload, Decimal("0.8"))


def test_grade_operators_frozenset_matches_evaluator_map() -> None:
    """
    _GRADE_OPERATORS is derived from _GRADE_OPERATOR_FUNCS's own keys, so the two cannot drift apart:
    a future operator added to one without the other would fail this test immediately.
    """
    assert _GRADE_OPERATORS == frozenset(_GRADE_OPERATOR_FUNCS)
