"""Tests for CompetencyRuleProfile, the reusable evaluation rule a CompetencyCriterion draws from."""
import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from organizations.models import Organization

from openedx_catalog.models import CatalogCourse, CourseRun
from openedx_learning.applets.cbe.rule_payloads import validate_rule_payload
from openedx_learning.models import CompetencyRuleProfile, CompetencyTaxonomy, RuleType

pytestmark = pytest.mark.django_db

_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}


def test_creating_a_rule_profile_persists_its_columns(organization: Organization) -> None:
    """
    Creating a CompetencyRuleProfile with values for `organization`, `rule_type`, and
    `rule_payload`, then reading the row back from the database, returns those same values, plus
    `course` and `competency_taxonomy` left null and `archived` defaulting to False. See ADR-0002
    Decision 3.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    persisted = CompetencyRuleProfile.objects.get(pk=profile.pk)
    assert persisted.organization == organization
    assert persisted.course is None
    assert persisted.competency_taxonomy is None
    assert persisted.rule_type == RuleType.GRADE
    assert persisted.rule_payload == _GRADE_PAYLOAD
    assert persisted.archived is False


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
def test_rule_profile_can_be_created_with_any_single_scope_or_no_scope(
    scope_kwargs: dict,
    organization: Organization,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """
    A CompetencyRuleProfile can be created scoped to organization, course, or competency_taxonomy
    alone, or to none of them (the system default). The check constraint's rejection of more than
    one scope field at once is covered separately, by
    test_rule_profile_scope_check_constraint_rejects_more_than_one_scope_field below.
    """
    # Free the all-null slot the seed migration (0005) occupies, so the "no scope" case can be
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


def test_archiving_a_profile_nulls_scope_code_and_frees_its_scope_for_a_replacement(
    organization: Organization,
) -> None:
    """
    Archiving a profile nulls its scope_code, which frees that scope for a brand new profile: a
    replacement may now be created for the exact same organization/course/taxonomy, since the
    archived row no longer occupies the unique scope_code slot.
    """
    original = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    original.refresh_from_db()
    assert original.scope_code == f"org:{organization.pk},course:,taxonomy:"

    original.archived = True
    original.save()
    original.refresh_from_db()
    assert original.scope_code is None

    replacement = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    replacement.refresh_from_db()
    assert replacement.scope_code == f"org:{organization.pk},course:,taxonomy:"


def test_archiving_the_seeded_system_default_frees_its_scope_for_a_replacement(
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Archiving the seeded system-default profile (all three scope fields null) nulls its
    scope_code, same as for a scoped profile, which frees the all-null scope for a brand new
    system-default row.
    """
    assert default_rule_profile.scope_code == "org:,course:,taxonomy:"

    default_rule_profile.archived = True
    default_rule_profile.save()
    default_rule_profile.refresh_from_db()
    assert default_rule_profile.scope_code is None

    replacement = CompetencyRuleProfile.objects.create(rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD)
    replacement.refresh_from_db()
    assert replacement.scope_code == "org:,course:,taxonomy:"


def test_two_live_profiles_cannot_share_the_same_scope(organization: Organization) -> None:
    """Two live CompetencyRuleProfile rows cannot share the same scope: two rows that both set
    only `organization` collide."""
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
    A profile whose rule_payload does not match its rule_type (here, `value: 80` instead of a
    0.0-1.0 fraction) is rejected by full_clean(), which save() calls, so objects.create() raises
    rather than writing a rule nothing can evaluate.

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


def test_scope_immutability_allows_rule_payload_and_archived_to_change(organization: Organization) -> None:
    """
    Only rule_type, rule_payload, and archived may change after creation; changing rule_payload and
    archived (as opposed to a scope field) succeeds. rule_type isn't exercised here: RuleType has
    only one member right now, so there's no other value to change it to.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
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
# History and the seeded system default


# ---------------------------------------------------------------------------------------------


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
