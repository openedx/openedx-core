"""
Tests for `record_graded_object_statuses`, exercising each acceptance-criteria scenario for
leaf-level competency status recording.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from django.db import DatabaseError, transaction
from freezegun import freeze_time

from openedx_learning.api import GradedObjectScore, record_graded_object_statuses
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    MasteryStatus,
    RuleType,
    StudentCompetencyCriterionStatus,
)
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

pytestmark = pytest.mark.django_db


def _make_criterion(
    *,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    rule_profile: CompetencyRuleProfile | None = None,
    override: tuple[str, dict] | None = None,
) -> CompetencyCriterion:
    """Build a leaf CompetencyCriterion using whichever of rule_profile or override the caller passes."""
    rule_type_override, rule_payload_override = override if override is not None else (None, None)
    return CompetencyCriterion.objects.create(
        group=group,
        object_tag=object_tag,
        rule_profile=rule_profile,
        rule_type_override=rule_type_override,
        rule_payload_override=rule_payload_override,
    )


@pytest.fixture(name="criterion")
def _criterion(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> CompetencyCriterion:
    """A leaf CompetencyCriterion directly under the root `group`, using the seeded default profile."""
    return _make_criterion(group=group, object_tag=object_tag, rule_profile=default_rule_profile)


@pytest.fixture(name="rule_profile_75")
def _rule_profile_75(competency_taxonomy: CompetencyTaxonomy) -> CompetencyRuleProfile:
    """A CompetencyRuleProfile scoped to `competency_taxonomy`, requiring at least 0.75."""
    return CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy,
        rule_type=RuleType.GRADE,
        rule_payload={"op": "gte", "value": 0.75, "scale": "percent"},
    )


def test_grade_meeting_threshold_demonstrates(
    user, group: CompetencyCriteriaGroup, object_tag: ObjectTag, rule_profile_75: CompetencyRuleProfile
) -> None:
    """A grade of 80% against a 75% threshold demonstrates the criterion."""
    criterion = _make_criterion(group=group, object_tag=object_tag, rule_profile=rule_profile_75)

    changed = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.80"))],
    )

    assert changed == 1
    assert StudentCompetencyCriterionStatus.objects.get(criterion=criterion).status_id == MasteryStatus.DEMONSTRATED


def test_grade_below_threshold_is_attempted_not_demonstrated(
    user, group: CompetencyCriteriaGroup, object_tag: ObjectTag, rule_profile_75: CompetencyRuleProfile
) -> None:
    """A grade of 60% against a 75% threshold is attempted but not demonstrated."""
    criterion = _make_criterion(group=group, object_tag=object_tag, rule_profile=rule_profile_75)

    changed = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.60"))],
    )

    assert changed == 1
    assert (
        StudentCompetencyCriterionStatus.objects.get(criterion=criterion).status_id
        == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED
    )


@pytest.mark.parametrize(
    "fraction, expected_status",
    [
        pytest.param(Decimal("0.74"), MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED, id="just_below"),
        pytest.param(Decimal("0.76"), MasteryStatus.DEMONSTRATED, id="just_above"),
        pytest.param(Decimal("0.749999999999"), MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED, id="fractionally_below"),
        pytest.param(Decimal("0.7500000000"), MasteryStatus.DEMONSTRATED, id="exact_with_trailing_zeros"),
        pytest.param(Decimal("0.7500000001"), MasteryStatus.DEMONSTRATED, id="fractionally_above"),
    ],
)
def test_threshold_boundary_precision(
    user, group: CompetencyCriteriaGroup, object_tag: ObjectTag, rule_profile_75: CompetencyRuleProfile,
    *, fraction: Decimal, expected_status: MasteryStatus,
) -> None:
    """
    Decimal arithmetic is exact, so a fraction on either side of a 0.75 threshold -- including at
    extreme precision, and including one with trailing zeros -- resolves without rounding error.
    """
    criterion = _make_criterion(group=group, object_tag=object_tag, rule_profile=rule_profile_75)

    record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=fraction)],
    )

    assert StudentCompetencyCriterionStatus.objects.get(criterion=criterion).status_id == expected_status


def test_score_exactly_at_default_threshold_demonstrates(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag
) -> None:
    """
    A score of exactly Decimal("0.8") against the seeded default profile (at least 0.8) demonstrates
    the criterion. This is the regression test for comparing a Decimal fraction against a rule_payload
    value that Django's JSONField returns as a plain float.
    """
    changed = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.8"))],
    )

    assert changed == 1
    assert StudentCompetencyCriterionStatus.objects.get(criterion=criterion).status_id == MasteryStatus.DEMONSTRATED


def test_object_with_no_criteria_costs_one_query_and_writes_nothing(user, django_assert_num_queries) -> None:
    """Content with no competency criteria is unaffected, costing exactly one query."""
    score = GradedObjectScore(object_id="block-v1:Org1+Python100+Fall2026+problem+none", fraction=Decimal("0.9"))
    with django_assert_num_queries(1):
        changed = record_graded_object_statuses(user_id=user.id, scores=[score])

    assert changed == 0
    assert not StudentCompetencyCriterionStatus.objects.exists()


def test_object_tagged_with_unrelated_tag_writes_nothing(user) -> None:
    """A subsection tagged with a tag no criterion points at is unaffected."""
    plain_taxonomy = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1")
    plain_tag = Tag.objects.create(taxonomy=plain_taxonomy, value="Unrelated")
    plain_object_tag = ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+unrelated", taxonomy=plain_taxonomy, tag=plain_tag,
    )

    changed = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=plain_object_tag.object_id, fraction=Decimal("0.9"))],
    )

    assert changed == 0
    assert not StudentCompetencyCriterionStatus.objects.exists()


def test_empty_scores_costs_no_queries(user, django_assert_num_queries) -> None:
    """An empty scores sequence returns 0 and issues no queries at all."""
    with django_assert_num_queries(0):
        changed = record_graded_object_statuses(user_id=user.id, scores=[])

    assert changed == 0


def test_one_score_evaluates_each_criterion_on_its_own_threshold(
    user, competency_taxonomy: CompetencyTaxonomy, group: CompetencyCriteriaGroup, object_tag: ObjectTag,
    rule_profile_75: CompetencyRuleProfile,
) -> None:
    """
    One grade satisfies two criteria with different thresholds: two competency tags each carry their
    own ObjectTag sharing one object_id, one criterion resolved via a profile, the other via a
    per-criterion override, and a single score demonstrates one but not the other.
    """
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Other Competency")
    other_object_tag = ObjectTag.objects.create(
        object_id=object_tag.object_id, taxonomy=competency_taxonomy, tag=other_tag,
    )
    other_group = CompetencyCriteriaGroup.objects.create(tag=other_tag)
    criterion = _make_criterion(group=group, object_tag=object_tag, rule_profile=rule_profile_75)
    other_criterion = _make_criterion(
        group=other_group, object_tag=other_object_tag,
        override=(RuleType.GRADE, {"op": "gte", "value": 0.9, "scale": "percent"}),
    )

    changed = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.8"))],
    )

    assert changed == 2
    assert StudentCompetencyCriterionStatus.objects.get(criterion=criterion).status_id == MasteryStatus.DEMONSTRATED
    assert (
        StudentCompetencyCriterionStatus.objects.get(criterion=other_criterion).status_id
        == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED
    )


def test_lower_grade_does_not_lower_demonstrated(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag
) -> None:
    """A later, lower grade does not take away an already-demonstrated status."""
    first = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.8"))],
    )
    assert first == 1

    second = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.6"))],
    )

    assert second == 0
    assert StudentCompetencyCriterionStatus.objects.get(criterion=criterion).status_id == MasteryStatus.DEMONSTRATED


def test_higher_grade_raises_attempted_to_demonstrated(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag
) -> None:
    """A later, higher grade raises a stored status, and only `modified` moves."""
    first_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second_time = datetime(2026, 1, 2, tzinfo=timezone.utc)

    with freeze_time(first_time):
        first = record_graded_object_statuses(
            user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.6"))],
        )
    assert first == 1

    with freeze_time(second_time):
        second = record_graded_object_statuses(
            user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.8"))],
        )
    assert second == 1

    row = StudentCompetencyCriterionStatus.objects.get(criterion=criterion)
    assert row.status_id == MasteryStatus.DEMONSTRATED
    assert row.created == first_time
    assert row.modified == second_time


def test_identical_repeat_changes_nothing(user, criterion: CompetencyCriterion, object_tag: ObjectTag) -> None:
    """Repeating an identical evaluation changes nothing, including `modified`."""
    first = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.9"))],
    )
    assert first == 1
    modified_after_first = StudentCompetencyCriterionStatus.objects.get(criterion=criterion).modified

    second = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.9"))],
    )

    assert second == 0
    assert StudentCompetencyCriterionStatus.objects.get(criterion=criterion).modified == modified_after_first


@pytest.mark.parametrize(
    "corrupt_kwargs",
    [
        pytest.param({"rule_type_override": "MasteryLevel"}, id="unsupported_rule_type"),
        pytest.param(
            {"rule_payload_override": {"op": "invalid_op", "value": 0.8, "scale": "percent"}},
            id="invalid_operator",
        ),
    ],
)
def test_unappliable_rule_is_skipped_and_logged(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag,
    caplog: pytest.LogCaptureFixture, corrupt_kwargs: dict,
) -> None:
    """
    A criterion whose rule cannot be applied is passed over: the well-formed sibling criterion still
    gets its row and the function still returns 1, and a WARNING names the skipped criterion.

    The bad criterion is first created with a valid override, then corrupted with a direct queryset
    `update()`, which bypasses `CompetencyCriterion.save()`'s own validation: `save()` itself would
    reject either corrupted shape.
    """
    competency_taxonomy = object_tag.taxonomy
    bad_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Bad Rule")
    bad_group = CompetencyCriteriaGroup.objects.create(tag=bad_tag)
    bad_object_tag = ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+bad", taxonomy=competency_taxonomy, tag=bad_tag,
    )
    bad_criterion = _make_criterion(
        group=bad_group, object_tag=bad_object_tag,
        override=(RuleType.GRADE, {"op": "gte", "value": 0.8, "scale": "percent"}),
    )
    CompetencyCriterion.objects.filter(pk=bad_criterion.pk).update(**corrupt_kwargs)

    with caplog.at_level(logging.WARNING, logger="openedx_learning.applets.cbe.api"):
        changed = record_graded_object_statuses(
            user_id=user.id,
            scores=[
                GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.9")),
                GradedObjectScore(object_id=bad_object_tag.object_id, fraction=Decimal("0.9")),
            ],
        )

    assert changed == 1
    assert StudentCompetencyCriterionStatus.objects.get(criterion=criterion).status_id == MasteryStatus.DEMONSTRATED
    assert not StudentCompetencyCriterionStatus.objects.filter(criterion=bad_criterion).exists()
    assert any(
        record.levelname == "WARNING" and str(bad_criterion.pk) in record.getMessage()
        for record in caplog.records
    )


def test_invalid_profile_payload_is_skipped(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A criterion resolved through a profile whose stored payload is invalid is skipped and logged, the
    same as an invalid per-criterion override. The profile is corrupted with a direct queryset `update()`,
    since `save()` itself would reject an invalid payload.
    """
    CompetencyRuleProfile.objects.filter(pk=default_rule_profile.pk).update(
        rule_payload={"op": "invalid_op", "value": 0.8, "scale": "percent"},
    )

    with caplog.at_level(logging.WARNING, logger="openedx_learning.applets.cbe.api"):
        changed = record_graded_object_statuses(
            user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.9"))],
        )

    assert changed == 0
    assert not StudentCompetencyCriterionStatus.objects.filter(criterion=criterion).exists()
    assert any(
        record.levelname == "WARNING" and str(criterion.pk) in record.getMessage()
        for record in caplog.records
    )


def test_criterion_on_non_competency_taxonomy_tag_is_not_evaluated(
    user, group: CompetencyCriteriaGroup, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    A criterion on a tag outside a competency taxonomy is not evaluated, even when its parent group
    sits on a competency-taxonomy tag.
    """
    plain_taxonomy = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v2")
    plain_tag = Tag.objects.create(taxonomy=plain_taxonomy, value="Plain")
    plain_object_tag = ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+plain", taxonomy=plain_taxonomy, tag=plain_tag,
    )
    _make_criterion(group=group, object_tag=plain_object_tag, rule_profile=default_rule_profile)

    changed = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=plain_object_tag.object_id, fraction=Decimal("0.9"))],
    )

    assert changed == 0
    assert not StudentCompetencyCriterionStatus.objects.exists()


def test_omitted_zero_point_object_gets_no_status(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag, competency_taxonomy: CompetencyTaxonomy,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A subsection worth zero points is simply omitted from scores, and only the scored object gets a status."""
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Unscored")
    other_object_tag = ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+unscored", taxonomy=competency_taxonomy, tag=other_tag,
    )
    other_criterion = _make_criterion(
        group=CompetencyCriteriaGroup.objects.create(tag=other_tag),
        object_tag=other_object_tag,
        rule_profile=default_rule_profile,
    )

    changed = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.9"))],
    )

    assert changed == 1
    assert StudentCompetencyCriterionStatus.objects.filter(criterion=criterion).exists()
    assert not StudentCompetencyCriterionStatus.objects.filter(criterion=other_criterion).exists()


def test_rolled_back_grade_leaves_no_status(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag
) -> None:
    """A grade that is not saved leaves no competency status behind, since this function opens no transaction."""

    class _BoomError(Exception):
        """Raised deliberately to force the enclosing transaction to roll back."""

    with pytest.raises(_BoomError), transaction.atomic():
        record_graded_object_statuses(
            user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.9"))],
        )
        raise _BoomError()

    assert not StudentCompetencyCriterionStatus.objects.filter(criterion=criterion).exists()


def test_storage_error_propagates(
    user, criterion: CompetencyCriterion, object_tag: ObjectTag, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine storage failure propagates out uncaught, rather than being mistaken for an unappliable rule."""
    def _boom(*args, **kwargs):
        raise DatabaseError("boom")

    monkeypatch.setattr(StudentCompetencyCriterionStatus.objects, "get_or_create", _boom)

    with pytest.raises(DatabaseError):
        record_graded_object_statuses(
            user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.9"))],
        )

    assert not StudentCompetencyCriterionStatus.objects.filter(criterion=criterion).exists()


def test_duplicate_object_id_raises_value_error(user, django_assert_num_queries) -> None:
    """A repeated object_id in one call raises ValueError before any query runs."""
    scores = [
        GradedObjectScore(object_id="dup", fraction=Decimal("0.5")),
        GradedObjectScore(object_id="dup", fraction=Decimal("0.9")),
    ]

    with django_assert_num_queries(0):
        with pytest.raises(ValueError):
            record_graded_object_statuses(user_id=user.id, scores=scores)


def test_graded_object_score_validation() -> None:
    """
    Decimal("0") and Decimal("1") are valid; a fraction outside 0-1, NaN, or Infinity raises ValueError;
    a plain float raises TypeError.
    """
    GradedObjectScore(object_id="x", fraction=Decimal("0"))
    GradedObjectScore(object_id="x", fraction=Decimal("1"))

    for bad_fraction in (Decimal("-0.01"), Decimal("1.01"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError):
            GradedObjectScore(object_id="x", fraction=bad_fraction)

    with pytest.raises(TypeError):
        GradedObjectScore(object_id="x", fraction=0.8)  # type: ignore[arg-type]


def test_archived_rule_profile_still_evaluates(
    user, competency_taxonomy: CompetencyTaxonomy, group: CompetencyCriteriaGroup, object_tag: ObjectTag
) -> None:
    """
    An archived CompetencyRuleProfile remains queryable and is still evaluated normally, per ADR-0002
    Decision 3.
    """
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE,
        rule_payload={"op": "gte", "value": 0.75, "scale": "percent"},
    )
    criterion = _make_criterion(group=group, object_tag=object_tag, rule_profile=profile)
    profile.archived = True
    profile.save()

    changed = record_graded_object_statuses(
        user_id=user.id, scores=[GradedObjectScore(object_id=object_tag.object_id, fraction=Decimal("0.8"))],
    )

    assert changed == 1
    assert StudentCompetencyCriterionStatus.objects.get(criterion=criterion).status_id == MasteryStatus.DEMONSTRATED
