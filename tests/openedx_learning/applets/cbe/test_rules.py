"""
Tests for the CBE permission predicates.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.contrib.auth.models import User as UserType  # pylint: disable=imported-auth-user

from openedx_learning.applets.cbe.rules import can_view_competency_rule_profile
from openedx_learning.models import CompetencyRuleProfile, CompetencyTaxonomy, RuleType

pytestmark = pytest.mark.django_db

# A valid payload for the rows these tests create themselves.
GRADE_PAYLOAD = {"op": "gte", "value": 0.6, "scale": "percent"}


@pytest.mark.parametrize("user_fixture", ["staff_user", "user"])
def test_staff_and_non_staff_may_view_rule_profiles_without_a_taxonomy(
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
