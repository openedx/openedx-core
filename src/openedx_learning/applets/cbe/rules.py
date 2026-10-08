"""
Django rules-based permissions for Competency-Based Education (CBE).

Registered with the ``rules`` permission registry on import. ``src/openedx_learning/rules.py``
is what makes that import happen; see the note there.
"""
from __future__ import annotations

import rules

from openedx_tagging.rules import UserType

from .models import CompetencyRuleProfile

__all__ = [
    "can_view_competency_rule_profile",
]


@rules.predicate
def can_view_competency_rule_profile(
    user: UserType,
    profile: CompetencyRuleProfile | None = None,
) -> bool:
    """
    Whoever may view a profile's taxonomy may read that profile.

    Course authors, not only platform staff, need to read this. This is the gate the criteria
    tree endpoint uses too, since ``oel_tagging.view_tag`` delegates to ``view_taxonomy`` as well.

    An unscoped profile passes no taxonomy, which ``view_taxonomy`` grants to everyone; a
    taxonomy-scoped one is as visible as its taxonomy, so a disabled taxonomy stays admin-only.
    The endpoint refuses an anonymous caller before this predicate is asked.

    Asked through ``has_perm`` rather than by calling ``can_view_taxonomy``: openedx-platform
    replaces that rule with an org-aware one via ``rules.set_perm``, which a direct call skips.
    """
    return user.has_perm("oel_tagging.view_taxonomy", profile.competency_taxonomy if profile else None)


rules.add_perm("openedx_learning.view_competencyruleprofile", can_view_competency_rule_profile)
