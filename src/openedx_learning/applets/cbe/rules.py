"""
Django rules-based permissions for Competency-Based Education (CBE).

Registered with the ``rules`` permission registry on import. ``src/openedx_learning/rules.py``
is what makes that import happen; see the note there.
"""
from __future__ import annotations

import rules

from openedx_tagging.rules import UserType, is_taxonomy_admin

from .models import CompetencyRuleProfile

__all__ = [
    "can_view_competency_rule_profile",
]


@rules.predicate
def can_view_competency_rule_profile(
    user: UserType,
    profile: CompetencyRuleProfile | None = None,  # pylint: disable=unused-argument
) -> bool:
    """
    Taxonomy admins can read any competency rule profile.

    The system default is instance-wide competency configuration, administered by the same
    people who administer taxonomies, so this reuses the tagging app's notion of an
    administrator rather than defining a second one inside CBE that could drift from it.

    ``profile`` is accepted but not consulted yet: the scoped profiles still to come will branch
    on it and filter rows, rather than refuse a whole request in order to hide some of its rows.
    """
    return is_taxonomy_admin(user)


rules.add_perm("openedx_learning.view_competencyruleprofile", can_view_competency_rule_profile)
