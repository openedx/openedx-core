"""
Public API for Competency-Based Education (CBE).
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import QuerySet

from openedx_tagging.api import create_taxonomy
from openedx_tagging.models import Taxonomy

from .models import CompetencyTaxonomy

__all__ = [
    "create_competency_taxonomy",
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
    with transaction.atomic():
        taxonomy = create_taxonomy(
            name=name,
            description=description,
            enabled=enabled,
            allow_multiple=allow_multiple,
            allow_free_text=allow_free_text,
            read_only=read_only,
            export_id=export_id,
        )
        competency_taxonomy = CompetencyTaxonomy(taxonomy_ptr=taxonomy)
        # Copy the parent's fields onto the child instance: save_base(raw=True) below writes
        # only the child's own row and skips Taxonomy entirely, so it never reads these back
        # off the DB itself the way a normal save() of the MTI chain would.
        for field in Taxonomy._meta.fields:
            setattr(competency_taxonomy, field.attname, getattr(taxonomy, field.attname))
        competency_taxonomy.save_base(raw=True)
    # competency_taxonomy carries every Taxonomy field too (copied above), so it's usable
    # anywhere a Taxonomy is expected, without a second query to re-fetch the parent row.
    return competency_taxonomy


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
