"""
Tests for CompetencyCriteriaGroup, CompetencyRuleProfile, and CompetencyCriterion.

Fixtures shared with test_criteria_deletion.py and test_criteria_trees.py live in this directory's
conftest.py.
"""
import uuid as uuid_module

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.db.utils import IntegrityError
from organizations.models import Organization

from openedx_catalog.models import CatalogCourse, CourseRun
# _RULE_PAYLOAD_SPECS is private: it's the payload-spec registry itself, the one place that
# defines which rule types can actually be saved, which is exactly what
# test_rule_type_choices_match_rule_types_with_a_defined_payload_spec below needs to compare
# RuleType's declared choices against.
from openedx_learning.applets.cbe.models.criteria import _RULE_PAYLOAD_SPECS
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    LogicOperator,
    RuleType,
)
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

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
    # "View" is a plain string, not RuleType.VIEW: RuleType declares only rule types that have a
    # defined payload spec (see test_rule_type_choices_match_rule_types_with_a_defined_payload_spec
    # below), so an unsupported rule type is, by construction, one that isn't a RuleType member at
    # all. Behaviorally identical either way, since a TextChoices member IS its string value.
    pytest.param("View", _GRADE_PAYLOAD, id="unsupported_rule_type"),
]


# ==============================================================================================
# Schema and columns (AC1, AC2, AC6, AC12, AC17, AC22, AC33, AC34, AC23). CompetencyTaxonomy's own
# taxonomy_overrides_org default (AC1) is covered in test_models.py, not duplicated here.
# ==============================================================================================


def test_group_columns_match_adr_decision_2(course_run: CourseRun, tag: Tag) -> None:
    """
    CompetencyCriteriaGroup has exactly the columns ADR-0002 Decision 2 lists: a nullable self-FK
    `parent`, a required `tag` (db_column oel_tagging_tag_id), a nullable `course` targeting
    openedx_catalog.CourseRun, `name`, `ordering`, and `logic_operator`, plus `id`.
    """
    group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run)

    parent_field = CompetencyCriteriaGroup._meta.get_field("parent")
    assert parent_field.null is True
    assert parent_field.remote_field.model is CompetencyCriteriaGroup

    tag_field = CompetencyCriteriaGroup._meta.get_field("tag")
    assert tag_field.null is False
    assert tag_field.remote_field.model is Tag
    assert tag_field.db_column == "oel_tagging_tag_id"

    course_field = CompetencyCriteriaGroup._meta.get_field("course")
    assert course_field.null is True
    assert course_field.remote_field.model is CourseRun

    assert CompetencyCriteriaGroup._meta.get_field("name").null is False
    assert CompetencyCriteriaGroup._meta.get_field("ordering").null is False
    assert CompetencyCriteriaGroup._meta.get_field("logic_operator").null is True

    assert group.course_id == course_run.pk


def test_rule_profile_columns_match_adr_decision_3() -> None:
    """
    CompetencyRuleProfile has exactly the columns ADR-0002 Decision 3 lists: nullable
    `organization`, `course`, and `competency_taxonomy` scope fields, `scope_code`, `rule_type`,
    `rule_payload`, and `archived` (defaulting to False), plus `id`.

    `scope_code` is nullable, not "never null" as an earlier reading of AC7 (issue #641) required:
    see DECISION-on-delete.md deviation 3. It is null exactly while a profile is archived (see
    test_scope_code_is_null_once_archived_and_non_null_while_live below); this is what lets an
    archived profile stop occupying its scope's unique slot.
    """
    organization_field = CompetencyRuleProfile._meta.get_field("organization")
    assert organization_field.null is True
    assert organization_field.remote_field.model is Organization

    course_field = CompetencyRuleProfile._meta.get_field("course")
    assert course_field.null is True
    assert course_field.remote_field.model is CourseRun

    taxonomy_field = CompetencyRuleProfile._meta.get_field("competency_taxonomy")
    assert taxonomy_field.null is True
    assert taxonomy_field.remote_field.model is CompetencyTaxonomy

    assert CompetencyRuleProfile._meta.get_field("scope_code").null is True
    assert CompetencyRuleProfile._meta.get_field("rule_type").null is False
    assert CompetencyRuleProfile._meta.get_field("rule_payload").null is False
    assert CompetencyRuleProfile._meta.get_field("archived").default is False


def test_criterion_columns_match_adr_decision_4(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    CompetencyCriterion has exactly the columns ADR-0002 Decision 4 lists: required `group` and
    `object_tag`, a nullable `rule_profile`, and nullable `rule_type_override` /
    `rule_payload_override`, plus `id`. The model is named CompetencyCriterion (singular; the
    table holds many, individually a criterion), and carries no Meta.db_table override, so the
    table is Django's default name for that class.
    """
    assert CompetencyCriterion.__name__ == "CompetencyCriterion"
    assert CompetencyCriterion._meta.db_table == "openedx_learning_competencycriterion"

    group_field = CompetencyCriterion._meta.get_field("group")
    assert group_field.null is False
    assert group_field.db_column == "competency_criteria_group_id"

    object_tag_field = CompetencyCriterion._meta.get_field("object_tag")
    assert object_tag_field.null is False
    assert object_tag_field.db_column == "oel_tagging_objecttag_id"

    rule_profile_field = CompetencyCriterion._meta.get_field("rule_profile")
    assert rule_profile_field.null is True
    assert rule_profile_field.db_column == "competency_rule_profile_id"

    assert CompetencyCriterion._meta.get_field("rule_type_override").null is True
    assert CompetencyCriterion._meta.get_field("rule_payload_override").null is True

    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert criterion.rule_profile_id == default_rule_profile.pk


@pytest.mark.parametrize(
    "model",
    [CompetencyCriteriaGroup, CompetencyRuleProfile, CompetencyCriterion],
    ids=["group", "rule_profile", "criterion"],
)
def test_uuid_is_a_stable_unique_non_editable_external_identifier(model: type[models.Model]) -> None:
    """
    All three models carry a `uuid` external identifier: unique, not editable (so it can never be
    set through a form), and defaulting to a freshly generated uuid4 for every new row.
    """
    uuid_field = model._meta.get_field("uuid")
    assert isinstance(uuid_field, models.UUIDField)
    assert uuid_field.unique is True
    assert uuid_field.editable is False
    assert uuid_field.null is False
    assert uuid_field.default is uuid_module.uuid4


def test_group_has_no_columns_beyond_adr_decision_2() -> None:
    """
    CompetencyCriteriaGroup's concrete field set is exactly {id, uuid, parent, tag, course, name,
    ordering, logic_operator}: no more, no less. In particular, no `archived` column exists on
    this model (that responsibility belongs to a later change; see the module's own history of
    which ticket owns which model's archive column).
    """
    concrete_field_names = {f.name for f in CompetencyCriteriaGroup._meta.get_fields() if f.concrete}
    assert concrete_field_names == {"id", "uuid", "parent", "tag", "course", "name", "ordering", "logic_operator"}


def test_rule_profile_has_no_columns_beyond_adr_decision_3() -> None:
    """
    CompetencyRuleProfile's concrete field set is exactly {id, organization, course,
    competency_taxonomy, scope_code, rule_type, rule_payload, archived, uuid}: no more, no less.
    """
    concrete_field_names = {f.name for f in CompetencyRuleProfile._meta.get_fields() if f.concrete}
    assert concrete_field_names == {
        "id", "organization", "course", "competency_taxonomy", "scope_code", "rule_type", "rule_payload",
        "archived", "uuid",
    }


def test_criterion_has_no_columns_beyond_adr_decision_4() -> None:
    """
    CompetencyCriterion's concrete field set is exactly {id, uuid, group, object_tag, rule_profile,
    rule_type_override, rule_payload_override}: no more, no less. In particular, no `archived`
    column exists on this model.
    """
    concrete_field_names = {f.name for f in CompetencyCriterion._meta.get_fields() if f.concrete}
    assert concrete_field_names == {
        "id", "uuid", "group", "object_tag", "rule_profile", "rule_type_override", "rule_payload_override",
    }


# ==============================================================================================
# Constraints and validation (AC4, AC5, AC7, AC9, AC11, AC13, AC14, AC15).
# ==============================================================================================


def test_group_logic_operator_accepts_and_or_and_null_regardless_of_child_count(tag: Tag) -> None:
    """
    logic_operator accepts AND, OR, or null. Nothing at the data layer constrains it by how many
    children the group actually has: a group with zero children and a group with two children both
    save successfully with any of the three values. See ADR-0002 Decision 2; the database cannot
    see a group's future children at save time (a child's parent FK cannot point at a row that
    doesn't have a primary key yet), so this is enforced nowhere at this layer, deliberately.
    """
    for logic_operator in (LogicOperator.AND, LogicOperator.OR, None):
        childless = CompetencyCriteriaGroup.objects.create(tag=tag, logic_operator=logic_operator)
        assert childless.pk is not None

        parent = CompetencyCriteriaGroup.objects.create(tag=tag, logic_operator=logic_operator)
        CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent)
        CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent)
        assert CompetencyCriteriaGroup.objects.filter(parent=parent).count() == 2


def test_group_parent_and_child_relationship(tag: Tag) -> None:
    """
    A CompetencyCriteriaGroup's parent is null for a root and points at its parent for a child.
    See ADR-0002 Decision 2.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag, logic_operator=None)
    assert root.parent is None

    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, logic_operator=LogicOperator.AND)
    assert child.parent == root


def test_group_has_no_unique_constraint_on_parent_and_ordering(tag: Tag) -> None:
    """
    No UniqueConstraint on (parent, ordering) exists: two sibling groups may share the same
    `ordering` value. A parent's clean() cannot see its own future children at save time (a
    child's FK can't point at a not-yet-existing parent row), so there is no single-row state to
    check a per-parent uniqueness rule against, and none is declared. See ADR-0002 Decision 2.
    """
    unique_constraints = [
        c for c in CompetencyCriteriaGroup._meta.constraints if isinstance(c, models.UniqueConstraint)
    ]
    assert not any({"parent", "ordering"} <= set(c.fields) for c in unique_constraints)

    parent = CompetencyCriteriaGroup.objects.create(tag=tag)
    sibling_a = CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent, ordering=1)
    sibling_b = CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent, ordering=1)
    assert sibling_a.ordering == sibling_b.ordering == 1


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
    Before this, archiving a profile meant that scope could never be used again, since the archived
    row's scope_code stayed non-null and permanently held the unique slot.
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


def test_scope_code_unique_constraint_is_unconditional() -> None:
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
    database is ever touched; see test_criterion_save_validates_override_payload_before_constraint
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


def test_criterion_save_validates_override_payload_before_constraint(
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


def test_rule_profile_full_clean_value_message_names_the_fraction_convention(organization: Organization) -> None:
    """
    full_clean()'s error for a rule_payload 'value' given on a 0-100 scale (e.g. 80) names the
    0.0-1.0 fraction convention, not attrs' generic default message for a failed validator (which
    would say nothing about fractions or percentages) and not a Python traceback fragment.
    Guards against exactly the message-quality regression a naive attrs implementation of
    validate_rule_payload could introduce silently, since every other invalid-payload test here
    only asserts the exception type.
    """
    profile = CompetencyRuleProfile(
        organization=organization, rule_type=RuleType.GRADE, rule_payload={"op": "gte", "value": 80, "scale": "percent"}
    )
    with pytest.raises(ValidationError) as exc_info:
        profile.full_clean()

    message = " ".join(exc_info.value.messages)
    assert "fraction between 0.0 and 1.0" in message
    # Must not leak the attrs spec class's name or any Python call-mechanics fragment: a course
    # author editing this payload in the admin should never see "GradeRule.__init__()".
    assert "__init__" not in message
    assert "GradeRule" not in message


def test_rule_profile_full_clean_extra_key_message_names_the_key(organization: Organization) -> None:
    """
    full_clean()'s error for an unrecognized rule_payload key names that key in our own domain
    language (e.g. "unexpected extra"), not attrs' generic default message for a failed validator
    and not Python's own kw_only TypeError text ("GradeRule.__init__() got an unexpected keyword
    argument 'extra'"), which leaks the internal spec class's name to a course author editing
    this payload in the admin. Guards against exactly that regression, which a test asserting
    only that the key name appears in the message would not catch, since the leaky Python message
    also contains the key name.
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
    assert "__init__" not in message
    assert "GradeRule" not in message


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


def test_rule_type_choices_match_rule_types_with_a_defined_payload_spec() -> None:
    """
    RuleType's declared choices (what a serializer or an admin form offers an author) must contain
    exactly the rule types that can actually be saved. ADR-0002 Decision 3 defines a rule_payload
    shape per rule_type, and a rule_type with no defined shape is always rejected by
    validate_rule_payload's "not supported yet" branch, regardless of payload content. RuleType
    therefore declares only the rule types with a payload-spec entry (currently just Grade); a
    future rule type not yet built (a "View" or "MasteryLevel") is neither a RuleType member nor a
    declared choice until both its spec class and its RuleType member land together. This pins
    that invariant so declaring a new RuleType member and forgetting its payload spec (or vice
    versa) fails a test instead of shipping a dead-end choice.
    """
    declared_rule_types = {choice_value for choice_value, _label in RuleType.choices}
    enforced_rule_types = set(_RULE_PAYLOAD_SPECS.keys())
    assert declared_rule_types == enforced_rule_types


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


# ==============================================================================================
# Scope immutability (AC11). Each scope field gets its own rejection test; rule_type, rule_payload,
# and archived changing on the same row is asserted separately as the case that must still work.
# ==============================================================================================


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

    Uses a second organization rather than setting the scope to None: setting it to None would
    make scope_code collide with the seeded system-default row, so the unique constraint would
    raise IntegrityError instead of the scope guard, and the test would pass for the wrong
    reason. Do not "simplify" this back to None.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    deferred = CompetencyRuleProfile.objects.only("id", "rule_type").get(pk=profile.pk)

    deferred.organization = organization2
    with pytest.raises(ValidationError):
        deferred.save()


# NOTE: there is no test that `_check_scope_immutable()`'s fallback query targets
# `self._state.db` rather than the default alias. Proving that needs an instance loaded from a
# second database alias, and this suite configures only "default". Adding a second alias makes
# pytest-django run the whole migration history against it, which fails in
# openedx_content/backcompat/collections/migrations/0004_collection_key.py: its `generate_keys`
# RunPython step queries Collection.objects without `.using(schema_editor.connection.alias)`, so
# it always hits "default". That is a pre-existing bug in an unrelated app, but it breaks
# database setup for the entire session, not just this test. The alternative, assigning
# `instance._state.db` directly, is idiomatic in Django's own tests but trips this repo's
# enabled pylint `protected-access` check, and silencing that is not allowed. The one-line
# `.using(self._state.db)` in the model is correct by inspection; this is a known test gap.


# ==============================================================================================
# Indexes, history (AC16, AC19, AC20).
# ==============================================================================================


def test_database_indexes_from_adr_decision_5_are_present() -> None:
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


def test_history_recorded_for_group_profile_and_criterion(
    organization: Organization,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    HistoricalRecords() is applied to CompetencyCriteriaGroup, CompetencyRuleProfile, and
    CompetencyCriterion: each is registered in the app registry under its expected Historical*
    name, and editing an instance writes a row there. See ADR-0003 Decisions 1 and 2.

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


def test_history_not_recorded_for_tag_taxonomy_or_competencytaxonomy(competency_taxonomy: CompetencyTaxonomy) -> None:
    """
    django-simple-history is NOT applied to oel_tagging_tag, oel_tagging_taxonomy, or
    CompetencyTaxonomy: none of the three has a `.history` attribute, and no Historical* model is
    registered for any of them. See ADR-0003 Decisions 1 and 2 for why history tracking stops at
    the CBE-specific models and does not reach back into the generic tagging models they build on.
    """
    assert not hasattr(Tag, "history")
    assert not hasattr(Taxonomy, "history")
    assert not hasattr(competency_taxonomy, "history")

    for app_label, model_name in [
        ("oel_tagging", "HistoricalTag"),
        ("oel_tagging", "HistoricalTaxonomy"),
        ("openedx_learning", "HistoricalCompetencyTaxonomy"),
    ]:
        with pytest.raises(LookupError):
            apps.get_model(app_label, model_name)


# ==============================================================================================
# Migrations (AC10). AC21 (no makemigrations drift) and AC8 (this suite also runs against MySQL)
# are verified by running manage.py / the MySQL settings module, not by a unit test.
# ==============================================================================================


def test_migration_seeds_exactly_one_system_default_rule_profile() -> None:
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
