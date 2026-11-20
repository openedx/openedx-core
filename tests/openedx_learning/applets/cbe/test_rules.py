"""
Tests for the CBE permission predicates.

Fixtures live in this directory's conftest.py.
"""
import importlib.util
from unittest import mock

import pytest
import rules
from django.contrib.auth.models import AnonymousUser
from opaque_keys.edx.keys import CourseKey

from openedx_learning.applets.cbe import rules as cbe_rules
from openedx_learning.applets.cbe.rules import can_view_competency_rule_profile
from openedx_learning.models import CompetencyRuleProfile, CompetencyTaxonomy, RuleType
from openedx_tagging.rules import UserType

pytestmark = pytest.mark.django_db

VIEW_RULE_PROFILE = "openedx_learning.view_competencyruleprofile"
VIEW_CRITERIA = "openedx_learning.view_course_competency_criteria"
COURSE_KEY = CourseKey.from_string("course-v1:Org1+Python100+Fall2026")

# A valid payload for the rows these tests create themselves.
GRADE_PAYLOAD = {"op": "gte", "value": 0.6, "scale": "percent"}


@pytest.mark.parametrize("user_fixture", ["staff_user", "user"])
def test_staff_and_non_staff_may_view_the_collection_and_the_system_default(
    request: pytest.FixtureRequest,
    default_rule_profile: CompetencyRuleProfile,
    user_fixture: str,
) -> None:
    caller = request.getfixturevalue(user_fixture)

    assert can_view_competency_rule_profile(caller) is True
    assert can_view_competency_rule_profile(caller, default_rule_profile) is True


def test_a_profile_on_a_disabled_taxonomy_stays_administrator_only(
    staff_user: UserType,
    user: UserType,
) -> None:
    disabled_taxonomy = CompetencyTaxonomy.objects.create(name="Retired", export_id="retired-v1", enabled=False)
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=disabled_taxonomy,
        rule_type=RuleType.GRADE,
        rule_payload=dict(GRADE_PAYLOAD),
    )

    assert can_view_competency_rule_profile(user, profile) is False
    assert can_view_competency_rule_profile(staff_user, profile) is True


@pytest.mark.parametrize("scope_field, scope_fixture", [("course", "course_run"), ("organization", "organization")])
def test_course_and_organization_scoped_profiles_are_refused_to_everyone(
    request: pytest.FixtureRequest,
    staff_user: UserType,
    user: UserType,
    scope_field: str,
    scope_fixture: str,
) -> None:
    profile = CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE,
        rule_payload=dict(GRADE_PAYLOAD),
        **{scope_field: request.getfixturevalue(scope_fixture)},
    )

    assert can_view_competency_rule_profile(user, profile) is False
    assert can_view_competency_rule_profile(staff_user, profile) is False


def test_anonymous_callers_are_refused_by_the_registered_permission(
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    anonymous = AnonymousUser()

    assert anonymous.has_perm(VIEW_RULE_PROFILE) is False
    assert anonymous.has_perm(VIEW_RULE_PROFILE, default_rule_profile) is False


def test_the_default_criteria_permission_refuses_anonymous_callers() -> None:
    assert rules.has_perm(VIEW_CRITERIA, AnonymousUser(), COURSE_KEY) is False


def test_the_default_criteria_permission_admits_a_signed_in_user_with_no_course_role(user: UserType) -> None:
    assert rules.has_perm(VIEW_CRITERIA, user, COURSE_KEY) is True


def load_rules_module_into(registry: rules.RuleSet) -> None:
    """Run the CBE rules module again against `registry`, leaving the real registry and module alone."""
    spec = importlib.util.spec_from_file_location("openedx_learning.applets.cbe.rules_reloaded", cbe_rules.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(rules.permissions, "permissions", registry):
        spec.loader.exec_module(module)


def test_loading_the_rules_registers_the_default_criteria_permission_when_none_exists() -> None:
    registry = rules.RuleSet()

    load_rules_module_into(registry)

    assert registry[VIEW_CRITERIA] is rules.is_authenticated


def test_loading_the_rules_keeps_a_criteria_permission_a_host_registered_first() -> None:
    host_rule = rules.predicate(lambda user, course_key: True)
    registry = rules.RuleSet()
    registry.set_rule(VIEW_CRITERIA, host_rule)

    load_rules_module_into(registry)

    assert registry[VIEW_CRITERIA] is host_rule


def test_a_host_criteria_permission_set_after_the_rules_load_replaces_the_default() -> None:
    host_rule = rules.predicate(lambda user, course_key: True)
    registry = rules.RuleSet()
    load_rules_module_into(registry)

    registry.set_rule(VIEW_CRITERIA, host_rule)

    assert registry[VIEW_CRITERIA] is host_rule
