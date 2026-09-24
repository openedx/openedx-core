"""Tests for CompetencyCriterion, a leaf of a Competency Criteria tree."""
import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError

from openedx_learning.models import CompetencyCriteriaGroup, CompetencyCriterion, CompetencyRuleProfile, RuleType
from openedx_tagging.models import ObjectTag

pytestmark = pytest.mark.django_db

_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}

# One (rule_type, payload) pair per way ADR-0002 Decision 3 says a rule_payload can be invalid.
# test_rule_payloads.py covers these shapes directly; here they only have to reach clean().
_INVALID_GRADE_PAYLOADS = [
    pytest.param(RuleType.GRADE, {"op": "startswith", "value": 0.8, "scale": "percent"}, id="bad_op"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": 80, "scale": "percent"}, id="value_80_not_0_8"),
    pytest.param(RuleType.GRADE, {"op": "gte", "scale": "percent"}, id="missing_key"),
    pytest.param(RuleType.GRADE, ["not", "a", "dict"], id="non_dict"),
]


# ---------------------------------------------------------------------------------------------
# Either a rule_profile or both overrides. Never both, never neither.


# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_kwargs",
    [
        pytest.param(
            {"rule_type_override": RuleType.GRADE, "rule_payload_override": _GRADE_PAYLOAD, "use_profile": True},
            id="both_set",
        ),
        pytest.param({"use_profile": False}, id="neither_set"),
        pytest.param({"rule_payload_override": _GRADE_PAYLOAD, "use_profile": False}, id="only_payload_override_set"),
    ],
)
def test_criterion_profile_xor_override_check_constraint_rejects_invalid_states(
    invalid_kwargs: dict,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A CompetencyCriterion must have either a rule_profile with no overrides, or both override
    fields set with no rule_profile, never both and never neither. See ADR-0002 Decision 4.
    """
    use_profile = invalid_kwargs.pop("use_profile")
    kwargs = dict(invalid_kwargs)
    if use_profile:
        kwargs["rule_profile"] = default_rule_profile

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CompetencyCriterion.objects.create(group=group, object_tag=object_tag, **kwargs)


def test_criterion_profile_xor_override_check_constraint_holds_via_bulk_create(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    The profile-xor-overrides check constraint also rejects a bulk_create() that sets both a
    rule_profile and the override fields, even though bulk_create() never builds and saves an
    individual model instance, so clean()/full_clean() never runs. This confirms the invariant is
    enforced by the database's own check constraint, not merely by save()'s validation.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CompetencyCriterion.objects.bulk_create([
                CompetencyCriterion(
                    group=group,
                    object_tag=object_tag,
                    rule_profile=default_rule_profile,
                    rule_type_override=RuleType.GRADE,
                    rule_payload_override=_GRADE_PAYLOAD,
                )
            ])


def test_criterion_accepts_either_a_rule_profile_or_both_overrides(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Both valid states of the profile-xor-overrides check constraint save successfully: a
    rule_profile with no overrides, and both override fields set with no rule_profile.
    See ADR-0002 Decision 4.
    """
    with_profile = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert with_profile.pk is not None

    with_overrides = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_type_override=RuleType.GRADE, rule_payload_override=_GRADE_PAYLOAD
    )
    assert with_overrides.pk is not None


def test_setting_a_rule_type_override_without_a_payload_is_rejected_by_save(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag
) -> None:
    with pytest.raises(ValidationError):
        CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_type_override=RuleType.GRADE)


# ---------------------------------------------------------------------------------------------
# Override payload validation, and the profile that is never re-resolved


# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("rule_type, payload", _INVALID_GRADE_PAYLOADS)
def test_criterion_full_clean_rejects_invalid_override_payload(
    rule_type: str, payload: object, group: CompetencyCriteriaGroup, object_tag: ObjectTag
) -> None:
    """
    full_clean() raises ValidationError for a CompetencyCriterion's rule_payload_override on the
    same invalid shapes as CompetencyRuleProfile.rule_payload. See ADR-0002 Decision 3.
    """
    criterion = CompetencyCriterion(
        group=group, object_tag=object_tag, rule_type_override=rule_type, rule_payload_override=payload
    )
    with pytest.raises(ValidationError):
        criterion.full_clean()


# ---------------------------------------------------------------------------------------------
# History


# ---------------------------------------------------------------------------------------------


def test_editing_a_criterion_writes_a_historical_row(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    HistoricalRecords() is applied to CompetencyCriterion: creating a criterion and then switching
    it from a profile to overrides leaves two rows in the Historical model. See ADR-0003
    Decision 1 & 4 for more info.
    """
    historical_criterion = apps.get_model("openedx_learning", "HistoricalCompetencyCriterion")
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )

    criterion.rule_profile = None
    criterion.rule_type_override = RuleType.GRADE
    criterion.rule_payload_override = _GRADE_PAYLOAD
    criterion.save()

    assert historical_criterion.objects.filter(id=criterion.pk).count() == 2
