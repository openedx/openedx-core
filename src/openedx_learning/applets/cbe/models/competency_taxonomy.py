"""
The CompetencyTaxonomy model.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx_tagging.models import Taxonomy

__all__ = [
    "CompetencyTaxonomy",
]


class CompetencyTaxonomy(Taxonomy):
    """
    Marks a Taxonomy as competency-enabled, so CBE features apply to its tags.

    A taxonomy listed in this table:

    - can be displayed in the competency criteria association view.
    - can be displayed in the competency progress tracking views.
    - can also be displayed in the existing generic taxonomy views.
    - constrains its associated content objects to those supported for progress
      tracking, and to ones that could logically be used to demonstrate mastery of
      the competency (for example, associating both a course and one assignment
      within that same course would be ambiguous).

    A taxonomy *not* listed here:

    - is only displayed in the existing generic taxonomy views.
    - is not displayed in competency criteria association views.
    - is not displayed in competency progress tracking views.
    - has no competency-specific constraints on its associated content objects.

    Creating a competency taxonomy creates both the parent ``Taxonomy`` row and this
    row in one transaction; deleting either row removes both.

    .. no_pii:
    """

    taxonomy_overrides_org = models.BooleanField(
        default=False,
        help_text=_(
            "Resolves a tie when assigning a CompetencyRuleProfile to a CompetencyCriterion (ADR-0002 "
            "Decision 4): if both an organization-scoped profile and a taxonomy-scoped profile from this "
            "taxonomy apply to the same criterion, False (the default) assigns the organization-scoped "
            "profile, and True assigns this taxonomy's own profile instead, so it cannot be locally "
            "weakened by an organization. Nothing reads this field yet: organization-scoped "
            "CompetencyRuleProfile rows do not exist in this phase, so the tie it resolves cannot arise "
            "until they do."
        ),
    )

    class Meta:
        verbose_name = "Competency Taxonomy"
        verbose_name_plural = "Competency Taxonomies"
