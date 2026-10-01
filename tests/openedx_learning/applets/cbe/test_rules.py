"""
Tests for the CBE permission predicates and their registration.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import User as UserType  # pylint: disable=imported-auth-user

from openedx_learning.applets.cbe.rules import can_view_competency_rule_profile
from openedx_learning.models import CompetencyRuleProfile

pytestmark = pytest.mark.django_db

VIEW_RULE_PROFILE = "openedx_learning.view_competencyruleprofile"


def test_staff_may_view_rule_profiles(staff_user: UserType) -> None:
    """A user who may administer taxonomies may read rule profiles."""
    assert can_view_competency_rule_profile(staff_user) is True


def test_non_staff_may_not_view_rule_profiles(user: UserType) -> None:
    """A user who may not administer taxonomies may not read rule profiles."""
    assert can_view_competency_rule_profile(user) is False


def test_anonymous_may_not_view_rule_profiles() -> None:
    """A caller the system cannot identify may not read rule profiles."""
    assert can_view_competency_rule_profile(AnonymousUser()) is False


def test_staff_may_view_a_specific_profile_and_non_staff_may_not(
    staff_user: UserType,
    user: UserType,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A staff user may view a specific rule profile, and a non-staff user may not, matching each
    one's permission on the collection. The profile argument is accepted for the scoped profiles
    still to come; nothing depends on it yet.
    """
    assert can_view_competency_rule_profile(staff_user, default_rule_profile) is True
    assert can_view_competency_rule_profile(user, default_rule_profile) is False


def test_permission_is_registered_for_the_django_permission_check(staff_user: UserType, user: UserType) -> None:
    """
    The predicate answers has_perm() under the name DRF's perms_map builds.

    This is what proves src/openedx_learning/rules.py reached the rules registry: without it
    the applet's module is never imported and every caller is refused.
    """
    assert staff_user.has_perm(VIEW_RULE_PROFILE) is True
    assert user.has_perm(VIEW_RULE_PROFILE) is False
    assert AnonymousUser().has_perm(VIEW_RULE_PROFILE) is False
