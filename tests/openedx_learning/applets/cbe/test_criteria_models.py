"""
Tests for CompetencyCriteriaGroup, CompetencyRuleProfile, and CompetencyCriterion.
"""
import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.db.utils import IntegrityError
from organizations.api import ensure_organization
from organizations.models import Organization

from openedx_catalog.models import CatalogCourse, CourseRun
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    LogicOperator,
    RuleType,
)
from openedx_tagging.models import ObjectTag, Tag

pytestmark = pytest.mark.django_db

_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}

# One (rule_type, payload) pair per way ADR-0002 Decision 3 says a rule_payload can be invalid.
_INVALID_GRADE_PAYLOADS = [
    pytest.param(RuleType.GRADE, {"op": "startswith", "value": 0.8, "scale": "percent"}, id="bad_op"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": 80, "scale": "percent"}, id="value_80_not_0_8"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": 1.5, "scale": "percent"}, id="value_out_of_range"),
    pytest.param(RuleType.GRADE, {"op": "gte", "scale": "percent"}, id="missing_key"),
    pytest.param(RuleType.GRADE, {**_GRADE_PAYLOAD, "extra": 1}, id="extra_key"),
    pytest.param(RuleType.GRADE, ["not", "a", "dict"], id="non_dict"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": 0.8, "scale": "raw"}, id="wrong_scale"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": True, "scale": "percent"}, id="boolean_value"),
    pytest.param(RuleType.VIEW, _GRADE_PAYLOAD, id="unsupported_rule_type"),
]


@pytest.fixture(name="organization")
def _organization() -> Organization:
    """An Organization for use as a scope in these tests."""
    ensure_organization("Org1")
    return Organization.objects.get(short_name="Org1")


@pytest.fixture(name="organization2")
def _organization2() -> Organization:
    """A second Organization, distinct from `organization`, for use as a scope in these tests."""
    ensure_organization("Org2")
    return Organization.objects.get(short_name="Org2")


@pytest.fixture(name="course_run")
def _course_run(organization: Organization) -> CourseRun:
    """A CourseRun for use as a scope in these tests."""
    catalog_course = CatalogCourse.objects.create(org=organization, course_code="Python100")
    return CourseRun.objects.create(catalog_course=catalog_course, run_code="Fall2026")


@pytest.fixture(name="competency_taxonomy")
def _competency_taxonomy() -> CompetencyTaxonomy:
    """A CompetencyTaxonomy for use as a scope, and as the home taxonomy for `tag`."""
    return CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")


@pytest.fixture(name="tag")
def _tag(competency_taxonomy: CompetencyTaxonomy) -> Tag:
    """A Tag, from `competency_taxonomy`, for use as the competency a criteria tree evaluates."""
    return Tag.objects.create(taxonomy=competency_taxonomy, value="Writing Poetry")


@pytest.fixture(name="object_tag")
def _object_tag(competency_taxonomy: CompetencyTaxonomy, tag: Tag) -> ObjectTag:
    """An ObjectTag associating `tag` with a made-up content object, for use as a criterion's target."""
    return ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+p1",
        taxonomy=competency_taxonomy,
        tag=tag,
    )


@pytest.fixture(name="group")
def _group(tag: Tag) -> CompetencyCriteriaGroup:
    """A root CompetencyCriteriaGroup for `tag`, for use as a criterion's parent group."""
    return CompetencyCriteriaGroup.objects.create(tag=tag)


@pytest.fixture(name="default_rule_profile")
def _default_rule_profile() -> CompetencyRuleProfile:
    """The system-default CompetencyRuleProfile seeded by migration 0003."""
    return CompetencyRuleProfile.objects.get(
        organization__isnull=True,
        course__isnull=True,
        competency_taxonomy__isnull=True,
    )


def test_group_tree_and_logic_operator(tag: Tag) -> None:
    """
    A CompetencyCriteriaGroup's parent is null for a root and points at its parent for a child,
    and logic_operator accepts AND, OR, or null (the "no children yet" state). See ADR-0002
    Decision 2.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag, logic_operator=None)
    assert root.parent is None

    child_and = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, logic_operator=LogicOperator.AND)
    assert child_and.parent == root

    child_or = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, logic_operator=LogicOperator.OR)
    assert child_or.parent == root


def test_rule_profile_scope_check_constraint(
    organization: Organization, course_run: CourseRun, competency_taxonomy: CompetencyTaxonomy
) -> None:
    """
    The scope check constraint accepts a CompetencyRuleProfile scoped to at most one of
    organization, course, or competency_taxonomy (including none of them), and rejects one scoped
    to any two, or to all three. See ADR-0002 Decision 3.
    """
    # Free the all-null slot the seed migration (0003) occupies, so the "all null" case below can
    # be tested in isolation from the uniqueness constraint on scope_code, which is a separate
    # constraint covered by its own tests.
    CompetencyRuleProfile.objects.filter(
        organization__isnull=True, course__isnull=True, competency_taxonomy__isnull=True
    ).delete()

    accepted_scopes: list[dict] = [
        {"organization": organization},
        {"course": course_run},
        {"competency_taxonomy": competency_taxonomy},
        {},
    ]
    for scope_kwargs in accepted_scopes:
        with transaction.atomic():
            CompetencyRuleProfile.objects.create(rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD, **scope_kwargs)

    rejected_scopes: list[dict] = [
        {"organization": organization, "course": course_run},
        {"organization": organization, "competency_taxonomy": competency_taxonomy},
        {"course": course_run, "competency_taxonomy": competency_taxonomy},
        {"organization": organization, "course": course_run, "competency_taxonomy": competency_taxonomy},
    ]
    for scope_kwargs in rejected_scopes:
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CompetencyRuleProfile.objects.create(
                    rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD, **scope_kwargs
                )


def test_scope_code_generated_value(
    organization: Organization, course_run: CourseRun, competency_taxonomy: CompetencyTaxonomy
) -> None:
    """
    scope_code is derived from the three scope columns as "org:X,course:Y,taxonomy:Z", with each
    segment blank when the corresponding column is null. See ADR-0002 Decision 3.
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


def test_scope_code_uniqueness(organization: Organization) -> None:
    """
    Two CompetencyRuleProfile rows cannot share the same scope. In particular, two rows that both
    set only `organization` (leaving course and competency_taxonomy null) collide, which is
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


def test_scope_code_unique_constraint_has_no_condition() -> None:
    """
    No UniqueConstraint on CompetencyRuleProfile carries a `condition`. A conditional
    UniqueConstraint compiles to a partial index, which this project's MySQL backend does not
    support: Django would only raise a non-fatal system-check warning (models.W036) and silently
    skip creating the constraint, leaving uniqueness unenforced in production, while SQLite (used
    for local test runs) supports partial indexes and would mask the gap. See ADR-0002 Rejected
    Alternative 6.
    """
    unique_constraints = [c for c in CompetencyRuleProfile._meta.constraints if isinstance(c, models.UniqueConstraint)]
    assert unique_constraints
    for constraint in unique_constraints:
        assert constraint.condition is None


def test_criterion_profile_xor_override_constraint(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    A CompetencyCriterion must have either a rule_profile with no overrides, or both override
    fields set with no rule_profile, never both and never neither. See ADR-0002 Decision 4.
    """
    CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=default_rule_profile)
    CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_type_override=RuleType.GRADE, rule_payload_override=_GRADE_PAYLOAD
    )

    invalid_kwargs_list: list[dict] = [
        {  # both set
            "rule_profile": default_rule_profile,
            "rule_type_override": RuleType.GRADE,
            "rule_payload_override": _GRADE_PAYLOAD,
        },
        {},  # neither set
        {"rule_type_override": RuleType.GRADE},  # only the type override set
        {"rule_payload_override": _GRADE_PAYLOAD},  # only the payload override set
    ]
    for kwargs in invalid_kwargs_list:
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CompetencyCriterion.objects.create(group=group, object_tag=object_tag, **kwargs)


@pytest.mark.parametrize("rule_type, payload", _INVALID_GRADE_PAYLOADS)
def test_rule_profile_full_clean_rejects_invalid_payload(rule_type: str, payload: object) -> None:
    """
    full_clean() raises ValidationError for a CompetencyRuleProfile on every documented way a
    rule_payload can be invalid: a bad op, a value given on a 0-100 scale instead of 0.0-1.0, a
    value outside that range, a missing or extra key, a non-dict payload, a wrong scale, a
    boolean value, and a rule_type with no defined payload shape yet. See ADR-0002 Decision 3.
    """
    profile = CompetencyRuleProfile(rule_type=rule_type, rule_payload=payload)
    with pytest.raises(ValidationError):
        profile.full_clean()


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


def test_history_recorded_for_new_models_but_not_taxonomy(
    organization: Organization,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """
    HistoricalRecords() is applied to CompetencyCriteriaGroup, CompetencyRuleProfile, and
    CompetencyCriterion: each is registered in the app registry under its expected
    Historical* name, and editing an instance writes a row there. CompetencyTaxonomy has no
    history at all. See ADR-0003 Decisions 1 and 2.

    Historical* models are looked up via the app registry rather than the `.history` attribute
    because simple_history installs `.history` as a runtime descriptor with no type stubs, which
    mypy cannot type; apps.get_model() returns something mypy can call `.objects` on.
    """
    historical_group = apps.get_model("openedx_learning", "HistoricalCompetencyCriteriaGroup")
    historical_profile = apps.get_model("openedx_learning", "HistoricalCompetencyRuleProfile")
    historical_criterion = apps.get_model("openedx_learning", "HistoricalCompetencyCriterion")

    group.name = "Poetry Mastery"
    group.save()
    assert historical_group.objects.filter(id=group.pk).count() == 2

    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    profile.rule_payload = {"op": "gte", "value": 0.9, "scale": "percent"}
    profile.save()
    assert historical_profile.objects.filter(id=profile.pk).count() == 2

    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    criterion.rule_profile = None
    criterion.rule_type_override = RuleType.GRADE
    criterion.rule_payload_override = _GRADE_PAYLOAD
    criterion.save()
    assert historical_criterion.objects.filter(id=criterion.pk).count() == 2

    assert not hasattr(competency_taxonomy, "history")


def test_scope_immutability(organization: Organization, course_run: CourseRun) -> None:
    """
    Changing a CompetencyRuleProfile's scope (organization, course, or competency_taxonomy) after
    creation raises ValidationError on save(). Criteria store the profile id they were assigned
    and never re-resolve it, so letting the scope change would silently re-govern every criterion
    already pointing at this profile. This guard catches instance.save() but not a bulk
    QuerySet.update(). See ADR-0002 Decision 3.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    profile.organization = None
    profile.course = course_run
    with pytest.raises(ValidationError):
        profile.save()


def test_scope_immutability_with_deferred_load(organization: Organization, organization2: Organization) -> None:
    """
    Scope immutability is enforced even when the profile was loaded with .only()/.defer() and so
    never had a complete `loaded_scope` captured by from_db(). Without falling back to read the
    persisted scope back from the database, this edit would go through unchecked, because
    _check_scope_immutable() would find `loaded_scope` still None and skip the comparison
    entirely.

    Uses a second organization rather than setting the scope to None: setting it to None would
    make scope_code collide with the seeded system-default row, so the unique constraint would
    raise IntegrityError instead of the scope guard, and the test would pass for the wrong
    reason. Do not "simplify" this back to None.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    deferred = CompetencyRuleProfile.objects.only("id", "rule_type").get(pk=profile.pk)
    assert deferred.loaded_scope is None

    deferred.organization = organization2
    with pytest.raises(ValidationError):
        deferred.save()


def test_adr_indexes_present() -> None:
    """
    The real database tables carry the ADR-0002 Decision 5 indexes this migration is responsible
    for: positions 1, 2, 4, 5 (all covering indexes), and 9 (unique). Positions 2, 4, and 5 come
    from Django's automatic per-ForeignKey index rather than an explicit models.Index; this test
    introspects the database, not the model, so it holds regardless of which mechanism produced
    the index. Positions 3, 6, 7, 8, and 10 belong to tables this migration doesn't create.
    """
    with connection.cursor() as cursor:
        group_constraints = connection.introspection.get_constraints(cursor, CompetencyCriteriaGroup._meta.db_table)
        criterion_constraints = connection.introspection.get_constraints(cursor, CompetencyCriterion._meta.db_table)
        profile_constraints = connection.introspection.get_constraints(cursor, CompetencyRuleProfile._meta.db_table)

    def is_indexed(constraints: dict, columns: list[str]) -> bool:
        # Compare the ordered column list, not a set: column order is the whole point of a
        # composite index. An index on (course_id, oel_tagging_tag_id) would satisfy a set
        # comparison against ADR index 1 just as well as (oel_tagging_tag_id, course_id), but
        # only the tag-first ordering also serves tag-only lookups.
        return any(c["columns"] == columns and c["index"] for c in constraints.values())

    # 1: CompetencyCriteriaGroup(tag, course), the one explicit composite index.
    assert is_indexed(group_constraints, ["oel_tagging_tag_id", "course_id"])
    # 2: CompetencyCriteriaGroup(parent).
    assert is_indexed(group_constraints, ["parent_id"])
    # 4: CompetencyCriteria(object_tag).
    assert is_indexed(criterion_constraints, ["oel_tagging_objecttag_id"])
    # 5: CompetencyCriteria(group).
    assert is_indexed(criterion_constraints, ["competency_criteria_group_id"])
    # 9: CompetencyRuleProfile(scope_code), unique.
    assert any(
        set(c["columns"]) == {"scope_code"} and c["unique"] for c in profile_constraints.values()
    )


def test_seeded_default_rule_profile_exists() -> None:
    """
    Migration 0003 seeds exactly one system-default CompetencyRuleProfile: all three scope
    columns null, not archived, Grade >= 0.8 (80%). See ADR-0002 Decision 3.
    """
    profile = CompetencyRuleProfile.objects.get(
        organization__isnull=True, course__isnull=True, competency_taxonomy__isnull=True
    )
    assert profile.archived is False
    assert profile.rule_type == RuleType.GRADE
    assert profile.rule_payload == _GRADE_PAYLOAD
