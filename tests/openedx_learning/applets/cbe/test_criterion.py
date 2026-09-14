"""
Tests for CompetencyCriterion, a leaf of a CompetencyAchievementCriteria tree.

Each test name states the behavior it pins. A leaf points at one ObjectTag, meaning one specific
piece of tagged content, and takes its pass rule either from a shared CompetencyRuleProfile or
from its own inline override pair, never from both and never from neither.

Reading top to bottom gives the model's contract: its columns, the either-profile-or-overrides
invariant and every way it can be violated, that an override payload is validated on save, that
the stored profile is never re-resolved at read time, and its indexes and history.

Delete behavior is not covered here. Nothing in this module deletes a row that another row
points at. See test_criterion_deletion.py, in this same change, for this model's own
`on_delete` values, the transitive and scope-owner cases that only exist once this model
completes the tree, and test_criteria_trees.py for the tree-wide integration test.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.utils import IntegrityError

from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    RuleType,
)
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
# Schema


# ---------------------------------------------------------------------------------------------


def test_criterion_has_exactly_the_columns_adr_0002_decision_4_lists() -> None:
    """
    CompetencyCriterion's columns are exactly the ones ADR-0002 Decision 4 lists, with
    `rule_profile`, `rule_type_override`, and `rule_payload_override` optional and the rest
    required. No `archived` column yet; that arrives with #642. Carries no Meta.db_table
    override, so the table is Django's default name for the class.
    """
    fields = [f for f in CompetencyCriterion._meta.get_fields() if f.concrete]
    assert {f.name for f in fields} == {
        "id", "uuid", "group", "object_tag", "rule_profile", "rule_type_override", "rule_payload_override",
    }
    assert {f.name for f in fields if f.null} == {"rule_profile", "rule_type_override", "rule_payload_override"}
    assert CompetencyCriterion._meta.get_field("group").db_column == "competency_criteria_group_id"
    assert CompetencyCriterion._meta.get_field("object_tag").db_column == "oel_tagging_objecttag_id"
    assert CompetencyCriterion._meta.get_field("rule_profile").db_column == "competency_rule_profile_id"
    assert CompetencyCriterion._meta.db_table == "openedx_learning_competencycriterion"


# ---------------------------------------------------------------------------------------------
# Either a rule_profile or both overrides. Never both, never neither.
# ADR-0002 Decision 4. Three of the four invalid states reach the database check constraint
# and raise IntegrityError. The fourth, rule_type_override set with no payload, is caught
# earlier by save()'s payload validation and raises ValidationError instead.


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

    Covers the three invalid states that reach the database's check constraint: both set, neither
    set, and only rule_payload_override set. The fourth invalid state, only rule_type_override set,
    is caught earlier by save()'s own validation instead and raises ValidationError before the
    database is ever touched; see test_setting_a_rule_type_override_without_a_payload_is_rejected_by_save
    below for that case, and why it raises a different exception type than these three.
    """
    use_profile = invalid_kwargs.pop("use_profile")
    kwargs = dict(invalid_kwargs)
    if use_profile:
        kwargs["rule_profile"] = default_rule_profile

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CompetencyCriterion.objects.create(group=group, object_tag=object_tag, **kwargs)


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
    """
    Setting only rule_type_override, leaving rule_payload_override null, is caught by save()'s
    own validation before it ever reaches the database: save() validates rule_payload_override's
    shape whenever rule_type_override is set, and None is not a valid shape for any rule type, so
    this raises ValidationError. The database's check constraint would also reject this same row,
    for the same underlying reason (an override with no real payload), but save() never lets it
    get there. This is why two similar-looking invalid override states raise different exception
    types: this one is caught by save()'s validate_rule_payload call, while the other three (see
    test_criterion_profile_xor_override_check_constraint_rejects_invalid_states above) reach the
    database's check constraint, because the payload save() inspects for them is either valid or,
    when rule_type_override itself is null, not inspected at all.
    """
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


def test_criterion_rule_profile_is_not_recomputed_once_a_more_specific_profile_appears(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """
    A criterion's stored rule_profile is not resolved dynamically at read time: creating a new,
    more specific profile later does not silently re-govern a criterion that already resolved to a
    less specific one. See ADR-0002 Decision 4, which lists the specific write events that DO
    reassign a criterion (not exercised here) and states that no other path may recompute it. This
    guards against a property, manager method, or signal handler being added that would violate
    that rule by resolving the FK on every read instead of only at those write events.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )

    CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    criterion.refresh_from_db()
    assert criterion.rule_profile_id == default_rule_profile.pk


# ---------------------------------------------------------------------------------------------
# Indexes 4 and 5, and history


# ---------------------------------------------------------------------------------------------


def test_the_database_carries_adr_0002_decision_5_indexes_4_and_5() -> None:
    """
    The real table carries ADR-0002 Decision 5's index 4 on object_tag and index 5 on group. Both
    come from Django's automatic per-ForeignKey index rather than an explicit models.Index, so
    this introspects the database rather than the model and holds either way.
    """
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, CompetencyCriterion._meta.db_table)

    def is_indexed(columns: list[str]) -> bool:
        return any(c["columns"] == columns and c["index"] for c in constraints.values())

    assert is_indexed(["oel_tagging_objecttag_id"])
    assert is_indexed(["competency_criteria_group_id"])


def test_editing_a_criterion_writes_a_historical_row(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    HistoricalRecords() is applied to CompetencyCriterion: creating a criterion and then switching
    it from a profile to overrides leaves two rows in the Historical model. See ADR-0003
    Decision 1, and Decision 4 for why that switch is an authoring event worth recording.
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
