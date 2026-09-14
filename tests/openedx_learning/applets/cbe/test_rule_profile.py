"""
Tests for CompetencyRuleProfile, the reusable evaluation rule a CompetencyCriterion draws from.

Each test name states the behavior it pins. Reading top to bottom gives the model's contract:
its columns, the at-most-one-scope rule, how scope_code encodes that scope and what archiving
does to it, that the payload validator is wired into save(), that a profile's scope can never
change after creation, and the index, history and seeded row.

The payload shapes themselves are covered exhaustively and without a database in
test_rule_payloads.py. What matters here is only that a model save reaches that validator.

Delete behavior is not covered here, except where a test frees the seeded system-default scope,
which nothing references. Nothing else in this module deletes a row that another row points at.
See test_rule_profile_deletion.py, in this same change, for this model's own `on_delete` values
and the tests that exercise them.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.utils import IntegrityError
from organizations.models import Organization

from openedx_catalog.models import CatalogCourse, CourseRun
from openedx_learning.applets.cbe.rule_payloads import validate_rule_payload
from openedx_learning.models import CompetencyRuleProfile, CompetencyTaxonomy, RuleType

pytestmark = pytest.mark.django_db

_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}


# ---------------------------------------------------------------------------------------------
# Schema


# ---------------------------------------------------------------------------------------------


def test_rule_profile_has_exactly_the_columns_adr_0002_decision_3_lists() -> None:
    """
    CompetencyRuleProfile's columns are exactly the ones ADR-0002 Decision 3 lists, with
    `organization`, `course`, `competency_taxonomy`, and `scope_code` nullable and the rest
    required. `scope_code` is nullable, not "never null": it is null exactly while a profile is
    archived, which is what frees that scope's unique slot for a replacement. See ADR-0002
    Decision 3.
    """
    fields = [f for f in CompetencyRuleProfile._meta.get_fields() if f.concrete]
    assert {f.name for f in fields} == {
        "id", "uuid", "organization", "course", "competency_taxonomy", "scope_code", "rule_type",
        "rule_payload", "archived",
    }
    assert {f.name for f in fields if f.null} == {"organization", "course", "competency_taxonomy", "scope_code"}
    assert CompetencyRuleProfile._meta.get_field("organization").remote_field.model is Organization
    assert CompetencyRuleProfile._meta.get_field("course").remote_field.model is CourseRun
    assert CompetencyRuleProfile._meta.get_field("competency_taxonomy").remote_field.model is CompetencyTaxonomy


# ---------------------------------------------------------------------------------------------
# Scope: at most one of organization, course, competency_taxonomy


# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope_kwargs",
    [
        pytest.param({"organization": True}, id="organization_only"),
        pytest.param({"course": True}, id="course_only"),
        pytest.param({"competency_taxonomy": True}, id="competency_taxonomy_only"),
        pytest.param({}, id="no_scope_system_default"),
    ],
)
def test_rule_profile_scope_check_constraint_accepts_at_most_one_scope_field(
    scope_kwargs: dict,
    organization: Organization,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """
    The scope check constraint accepts a CompetencyRuleProfile scoped to at most one of
    organization, course, or competency_taxonomy, including none of them (the system default).
    See ADR-0002 Decision 3.
    """
    # Free the all-null slot the seed migration (0003) occupies, so the "no scope" case can be
    # tested in isolation from scope_code's own uniqueness constraint, which has its own tests.
    CompetencyRuleProfile.objects.filter(
        organization__isnull=True, course__isnull=True, competency_taxonomy__isnull=True
    ).delete()

    resolved_kwargs: dict[str, object] = {}
    if scope_kwargs.get("organization"):
        resolved_kwargs["organization"] = organization
    if scope_kwargs.get("course"):
        resolved_kwargs["course"] = course_run
    if scope_kwargs.get("competency_taxonomy"):
        resolved_kwargs["competency_taxonomy"] = competency_taxonomy

    profile = CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD, **resolved_kwargs
    )
    assert profile.pk is not None


@pytest.mark.parametrize(
    "scoped_fields",
    [
        pytest.param(("organization", "course"), id="organization_and_course"),
        pytest.param(("organization", "competency_taxonomy"), id="organization_and_taxonomy"),
        pytest.param(("course", "competency_taxonomy"), id="course_and_taxonomy"),
        pytest.param(("organization", "course", "competency_taxonomy"), id="all_three"),
    ],
)
def test_rule_profile_scope_check_constraint_rejects_more_than_one_scope_field(
    scoped_fields: tuple[str, ...],
    organization: Organization,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """
    The scope check constraint rejects a CompetencyRuleProfile scoped to any two of organization,
    course, and competency_taxonomy, or to all three. See ADR-0002 Decision 3.
    """
    available_values = {"organization": organization, "course": course_run, "competency_taxonomy": competency_taxonomy}
    scope_kwargs = {field_name: available_values[field_name] for field_name in scoped_fields}

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CompetencyRuleProfile.objects.create(rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD, **scope_kwargs)


# ---------------------------------------------------------------------------------------------
# scope_code: how a scope is encoded, and what archiving does to it
# ADR-0002 Decision 3. scope_code is a plain column recomputed in save(), and it goes null
# while a profile is archived. SQL never treats two NULLs as equal, so any number of archived
# rows may share a scope while exactly one live row holds it, which is what lets an archived
# profile be replaced.


# ---------------------------------------------------------------------------------------------


def test_scope_code_matches_org_course_taxonomy_format_for_each_scope_shape(
    organization: Organization, course_run: CourseRun, competency_taxonomy: CompetencyTaxonomy
) -> None:
    """
    A live (non-archived) profile's scope_code is "org:X,course:Y,taxonomy:Z", with each segment
    left blank when the corresponding scope column is null. See ADR-0002 Decision 3.
    """
    CompetencyRuleProfile.objects.filter(
        organization__isnull=True, course__isnull=True, competency_taxonomy__isnull=True
    ).delete()

    all_null = CompetencyRuleProfile.objects.create(rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD)
    org_only = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    course_only = CompetencyRuleProfile.objects.create(
        course=course_run, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    taxonomy_only = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    for profile in (all_null, org_only, course_only, taxonomy_only):
        profile.refresh_from_db()

    assert all_null.scope_code == "org:,course:,taxonomy:"
    assert org_only.scope_code == f"org:{organization.pk},course:,taxonomy:"
    assert course_only.scope_code == f"org:,course:{course_run.pk},taxonomy:"
    assert taxonomy_only.scope_code == f"org:,course:,taxonomy:{competency_taxonomy.pk}"


def test_scope_code_is_null_once_archived_and_non_null_while_live(organization: Organization) -> None:
    """
    scope_code is non-null while a profile is live, and becomes null once it is archived. An
    archived profile no longer holds its scope's unique slot, which is what lets a replacement be
    created for that same scope (see test_archiving_a_profile_frees_its_scope_for_a_replacement
    below); a profile that stayed occupying a non-null scope_code after archiving would block that
    forever. This is a deliberate design point, not an oversight: a plain nullable column, written
    explicitly whenever a profile is saved, rather than a database-computed value that can never
    tell "archived" apart from "live" on its own.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    profile.refresh_from_db()
    assert profile.scope_code == f"org:{organization.pk},course:,taxonomy:"

    profile.archived = True
    profile.save()
    profile.refresh_from_db()
    assert profile.scope_code is None


def test_archiving_a_profile_frees_its_scope_for_a_replacement(organization: Organization) -> None:
    """
    Once a profile scoped to a given organization/course/taxonomy is archived, a brand new profile
    may be created for that exact same scope: the archived row's scope_code goes to null and stops
    occupying the unique slot, so it no longer collides with the replacement's non-null scope_code.
    """
    original = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    original.archived = True
    original.save()

    replacement = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    replacement.refresh_from_db()
    original.refresh_from_db()

    assert original.scope_code is None
    assert replacement.scope_code == f"org:{organization.pk},course:,taxonomy:"


def test_two_live_profiles_cannot_share_the_same_scope(organization: Organization) -> None:
    """
    Two live CompetencyRuleProfile rows cannot share the same scope. In particular, two rows that
    both set only `organization` (leaving course and competency_taxonomy null) collide, which is
    exactly the case a plain UniqueConstraint on the three raw nullable columns would not catch,
    since SQL never treats two NULLs as equal. See ADR-0002 Decision 3.
    """
    CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CompetencyRuleProfile.objects.create(
                organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
            )


# ---------------------------------------------------------------------------------------------
# Payload validation is wired into save()


# ---------------------------------------------------------------------------------------------


def test_saving_a_profile_with_an_invalid_payload_raises_validation_error() -> None:
    """
    A profile whose rule_payload does not match its rule_type is rejected by full_clean(), which
    save() calls, so objects.create() raises rather than writing a rule nothing can evaluate.

    This proves only the wiring. test_rule_payloads.py covers every way a payload can be wrong.
    """
    with pytest.raises(ValidationError):
        CompetencyRuleProfile.objects.create(
            rule_type=RuleType.GRADE, rule_payload={"op": "gte", "value": 80, "scale": "percent"}
        )


def test_rule_profile_full_clean_value_message_names_the_fraction_convention(organization: Organization) -> None:
    """
    full_clean()'s error for a rule_payload 'value' given on a 0-100 scale (e.g. 80) names the
    0.0-1.0 fraction convention. Every other invalid-payload test here only asserts the exception
    type; this one asserts the message content.
    """
    profile = CompetencyRuleProfile(
        organization=organization, rule_type=RuleType.GRADE, rule_payload={"op": "gte", "value": 80, "scale": "percent"}
    )
    with pytest.raises(ValidationError) as exc_info:
        profile.full_clean()

    message = " ".join(exc_info.value.messages)
    assert "fraction between 0.0 and 1.0" in message


def test_rule_profile_full_clean_extra_key_message_names_the_key(organization: Organization) -> None:
    """
    full_clean()'s error for an unrecognized rule_payload key names that key in our own domain
    language (e.g. "unexpected extra").
    """
    profile = CompetencyRuleProfile(
        organization=organization,
        rule_type=RuleType.GRADE,
        rule_payload={**_GRADE_PAYLOAD, "extra": 1},
    )
    with pytest.raises(ValidationError) as exc_info:
        profile.full_clean()

    message = " ".join(exc_info.value.messages)
    assert "extra" in message


# ---------------------------------------------------------------------------------------------
# Scope immutability
# Editing a profile may change rule_type, rule_payload and archived only. Its scope is fixed
# at creation, so criteria already resolved to that scope are never silently re-governed.


# ---------------------------------------------------------------------------------------------


def test_scope_immutability_rejects_organization_change(
    organization: Organization, organization2: Organization
) -> None:
    """
    Changing a CompetencyRuleProfile's `organization` after creation raises ValidationError on
    save(). See ADR-0002 Decision 3.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    profile.organization = organization2
    with pytest.raises(ValidationError):
        profile.save()


def test_scope_immutability_rejects_course_change(organization: Organization, course_run: CourseRun) -> None:
    """
    Changing a CompetencyRuleProfile's `course` after creation raises ValidationError on save().
    See ADR-0002 Decision 3.
    """
    other_catalog_course = CatalogCourse.objects.create(org=organization, course_code="Python200")
    other_course_run = CourseRun.objects.create(catalog_course=other_catalog_course, run_code="Spring2027")

    profile = CompetencyRuleProfile.objects.create(
        course=course_run, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    profile.course = other_course_run
    with pytest.raises(ValidationError):
        profile.save()


def test_scope_immutability_rejects_taxonomy_change(competency_taxonomy: CompetencyTaxonomy) -> None:
    """
    Changing a CompetencyRuleProfile's `competency_taxonomy` after creation raises ValidationError
    on save(). See ADR-0002 Decision 3.
    """
    other_taxonomy = CompetencyTaxonomy.objects.create(name="Welding", export_id="welding-v1")

    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    profile.competency_taxonomy = other_taxonomy
    with pytest.raises(ValidationError):
        profile.save()


def test_scope_immutability_allows_rule_type_rule_payload_and_archived_to_change(organization: Organization) -> None:
    """
    Only rule_type, rule_payload, and archived may change after creation; changing any of them (as
    opposed to a scope field) succeeds. See ADR-0002 Decision 3.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    profile.rule_type = RuleType.GRADE
    profile.rule_payload = {"op": "lte", "value": 0.5, "scale": "percent"}
    profile.archived = True
    profile.save()

    profile.refresh_from_db()
    assert profile.rule_payload == {"op": "lte", "value": 0.5, "scale": "percent"}
    assert profile.archived is True


def test_scope_immutability_enforced_after_deferred_load(
    organization: Organization, organization2: Organization
) -> None:
    """
    Scope immutability is enforced even when the profile was loaded with .only()/.defer() and so
    never loaded the scope columns into this instance in the first place.
    _check_scope_immutable() always queries the persisted scope directly (see its docstring), so a
    partial load is not a way to bypass this check.

    Uses a second organization rather than setting the scope to None: a null scope would collide
    with the seeded system-default row, so the unique constraint would raise IntegrityError and
    the scope guard would never be reached.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    deferred = CompetencyRuleProfile.objects.only("id", "rule_type").get(pk=profile.pk)

    deferred.organization = organization2
    with pytest.raises(ValidationError):
        deferred.save()


# ---------------------------------------------------------------------------------------------
# Index 9, history, and the seeded system default


# ---------------------------------------------------------------------------------------------


def test_the_database_carries_adr_0002_decision_5_index_9_as_unique() -> None:
    """
    The real table carries ADR-0002 Decision 5's index 9 on scope_code, and it is unique. A plain
    index there would not enforce one profile per scope.

    The constraint is unconditional on purpose. A conditional UniqueConstraint compiles to a
    partial index, which MySQL does not support: Django raises only a models.W036 warning and
    silently skips creating it, while SQLite does support partial indexes and would hide the gap
    in a local run. See ADR-0002 Rejected Alternative 6.
    """
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(
            cursor, CompetencyRuleProfile._meta.db_table
        )

    assert any(set(c["columns"]) == {"scope_code"} and c["unique"] for c in constraints.values())


def test_editing_a_profile_writes_a_historical_row(organization: Organization) -> None:
    """
    HistoricalRecords() is applied to CompetencyRuleProfile: creating then editing a profile
    leaves two rows in the Historical model. See ADR-0003 Decision 1.
    """
    historical_profile = apps.get_model("openedx_learning", "HistoricalCompetencyRuleProfile")
    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    profile.rule_payload = {"op": "gte", "value": 0.9, "scale": "percent"}
    profile.save()

    assert historical_profile.objects.filter(id=profile.pk).count() == 2


def test_scope_code_is_excluded_from_history() -> None:
    """
    The Historical model does not track scope_code. It is a derived bookkeeping column, and the
    columns it derives from (the three scope fields and archived) are tracked instead, which is
    what an audit trail actually needs.
    """
    historical_profile = apps.get_model("openedx_learning", "HistoricalCompetencyRuleProfile")

    assert "scope_code" not in {f.name for f in historical_profile._meta.get_fields()}


def test_migration_seeds_exactly_one_system_default_rule_profile() -> None:
    """
    Migration 0005 seeds exactly one system-default CompetencyRuleProfile: all three scope
    columns null, not archived, Grade >= 0.8 (80%). See ADR-0002 Decision 3.
    """
    profile = CompetencyRuleProfile.objects.get(
        organization__isnull=True, course__isnull=True, competency_taxonomy__isnull=True
    )
    assert profile.archived is False
    assert profile.rule_type == RuleType.GRADE
    assert profile.rule_payload == _GRADE_PAYLOAD


def test_the_seeded_rule_payload_satisfies_the_payload_contract() -> None:
    """
    The seeded system-default row's rule_payload passes validate_rule_payload.

    0005_seed_default_rule_profile writes that payload as a literal and cannot check it itself: a
    historical migration must not import rule_payloads, because that module changes while the
    migration must not, and apps.get_model() returns a model reconstructed without the custom
    clean(). This test is therefore the only place the seeded literal and the validator meet.
    Without it, tightening _validate_grade_payload would leave the default row that every
    deployment ships with invalid, and no test would fail.
    """
    profile = CompetencyRuleProfile.objects.get(
        organization__isnull=True, course__isnull=True, competency_taxonomy__isnull=True
    )

    validate_rule_payload(profile.rule_type, profile.rule_payload)
