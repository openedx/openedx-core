"""
Tests for the CBE permission predicates.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.contrib.auth.models import AnonymousUser

from openedx_learning.applets.cbe.rules import can_view_competency_rule_profile
from openedx_learning.models import CompetencyRuleProfile, CompetencyTaxonomy, RuleType
from openedx_tagging.rules import UserType

pytestmark = pytest.mark.django_db

VIEW_RULE_PROFILE = "openedx_learning.view_competencyruleprofile"

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
