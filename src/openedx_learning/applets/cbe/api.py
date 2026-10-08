"""
Public API for Competency-Based Education (CBE).
"""
from __future__ import annotations

from django.db.models import QuerySet

from openedx_tagging.api import create_taxonomy
from openedx_tagging.models import Taxonomy

from .models import CompetencyRuleProfile, CompetencyTaxonomy

__all__ = [
    "create_competency_taxonomy",
    "get_competency_rule_profiles",
    "is_competency_taxonomy",
    "select_competency_taxonomies",
]


def create_competency_taxonomy(  # pylint: disable=too-many-positional-arguments
    name: str,
    description: str | None = None,
    enabled=True,
    allow_multiple=True,
    allow_free_text=False,
    read_only=False,
    export_id: str | None = None,
) -> CompetencyTaxonomy:
    """
    Create, save, and return a new CompetencyTaxonomy with the given attributes.
    """
    taxonomy = create_taxonomy(
        name=name,
        description=description,
        enabled=enabled,
        allow_multiple=allow_multiple,
        allow_free_text=allow_free_text,
        read_only=read_only,
        export_id=export_id,
        taxonomy_cls=CompetencyTaxonomy,
    )
    assert isinstance(taxonomy, CompetencyTaxonomy)
    return taxonomy


def get_competency_rule_profiles() -> QuerySet[CompetencyRuleProfile]:
    """
    Return every live CompetencyRuleProfile, in ascending ``id`` order.

    UNSTABLE: the rule profile family is incomplete, so the create, update, and archive entry
    points still to come may change this function's shape without a deprecation cycle.

    Archived profiles are left out: retirement is archive-only.

    The ordering is part of the contract rather than a cosmetic detail: an unordered queryset
    gives a paginating caller overlapping and skipped pages.
    """
    return CompetencyRuleProfile.objects.filter(archived=False).order_by("id")


def is_competency_taxonomy(taxonomy: Taxonomy) -> bool:
    """
    Return True if ``taxonomy`` is competency-enabled, i.e. has a CompetencyTaxonomy row.

    Costs one query per call unless ``taxonomy`` came from a queryset passed through
    :func:`select_competency_taxonomies`. Returns False for an unsaved ``taxonomy``.
    """
    # "competencytaxonomy" is the accessor Django generates for the multi-table-inheritance
    # link from Taxonomy to CompetencyTaxonomy.
    return hasattr(taxonomy, "competencytaxonomy")


def select_competency_taxonomies(taxonomies: QuerySet[Taxonomy]) -> QuerySet[Taxonomy]:
    """
    Return ``taxonomies`` with each CompetencyTaxonomy row joined in.

    Pair this with :func:`is_competency_taxonomy` when checking more than one taxonomy,
    so the check costs no additional query per row.
    """
    return taxonomies.select_related("competencytaxonomy")
