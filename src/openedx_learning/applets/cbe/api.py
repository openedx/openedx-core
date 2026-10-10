"""
Public API for Competency-Based Education (CBE).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Sequence

from django.core.exceptions import ValidationError
from django.db.models import QuerySet

from openedx_tagging.api import create_taxonomy
from openedx_tagging.models import Taxonomy

from .data import GradedObjectScore
from .models import CompetencyCriterion, CompetencyTaxonomy, MasteryStatus, StudentCompetencyCriterionStatus
from .rule_payloads import evaluate_rule

__all__ = [
    "GradedObjectScore",
    "create_competency_taxonomy",
    "is_competency_taxonomy",
    "record_graded_object_statuses",
    "select_competency_taxonomies",
]

log = logging.getLogger(__name__)


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


def _effective_rule(criterion: CompetencyCriterion) -> tuple[str, Any]:
    """Return (rule_type, payload) for whichever of criterion's profile or override is set."""
    if criterion.rule_profile_id is not None:
        # The oel_cbe_criterion_profile_xor_override_check constraint guarantees rule_profile is
        # set whenever rule_profile_id is; this assertion only narrows that fact for mypy.
        assert criterion.rule_profile is not None
        return criterion.rule_profile.rule_type, criterion.rule_profile.rule_payload
    # Same constraint, the other way around: rule_type_override is set whenever rule_profile_id
    # is not.
    assert criterion.rule_type_override is not None
    return criterion.rule_type_override, criterion.rule_payload_override


def _raise_leaf_status(user_id: int, criterion_id: int, status: MasteryStatus, now: datetime) -> int:
    """
    Create or raise a learner's leaf status row for one criterion; never lowers a stored status.

    Returns the count of rows created or raised (0 or 1), matching the semantics of
    ``QuerySet.update()``'s own return value.
    """
    row, created = StudentCompetencyCriterionStatus.objects.get_or_create(
        user_id=user_id, criterion_id=criterion_id,
        defaults={"status_id": status, "created": now, "modified": now},
    )
    if created:
        return 1
    return StudentCompetencyCriterionStatus.objects.filter(
        pk=row.pk, status_id__lt=status,
    ).update(status_id=status, modified=now)


def record_graded_object_statuses(*, user_id: int, scores: Sequence[GradedObjectScore]) -> int:
    """
    Record a learner's leaf-level competency status for each graded object in ``scores``.

    Call this only from inside the same transaction that also writes the grade: this function
    opens no transaction of its own and lets any storage error propagate rather than swallowing
    it, so the grade and its leaf statuses always commit or roll back together. Pass each
    ``object_id`` at most once, using the exact string already stored in ``ObjectTag.object_id``;
    the match performed against that value is case-sensitive. Leave out of ``scores`` any object
    worth zero possible points and any object the learner has not attempted. Only criteria whose
    tag belongs to a competency taxonomy are evaluated. An evaluated criterion's status is only
    ever raised, never lowered. The return value is the count of leaf rows created or raised,
    where ``0`` means nothing changed. This function performs no authorization check of its own;
    as with ``openedx_tagging.api``, the caller is responsible for enforcing permissions.
    """
    if not scores:
        return 0

    fraction_by_object_id: dict[str, Decimal] = {}
    for score in scores:
        if score.object_id in fraction_by_object_id:
            raise ValueError(f"duplicate object_id in scores: {score.object_id!r}")
        fraction_by_object_id[score.object_id] = score.fraction

    criteria = (
        CompetencyCriterion.objects.filter(
            object_tag__object_id__in=list(fraction_by_object_id),
            object_tag__tag__taxonomy__competencytaxonomy__isnull=False,
        )
        .select_related("object_tag", "rule_profile")
        .order_by("pk")
    )

    now = datetime.now(tz=timezone.utc)
    changed = 0
    for criterion in criteria:
        fraction = fraction_by_object_id[criterion.object_tag.object_id]
        rule_type, payload = _effective_rule(criterion)
        try:
            demonstrated = evaluate_rule(rule_type, payload, fraction)
        except ValidationError as exc:
            log.warning(
                "Skipping CompetencyCriterion %s: its rule cannot be applied: %s", criterion.pk, exc.messages,
            )
            continue
        status = MasteryStatus.DEMONSTRATED if demonstrated else MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED
        changed += _raise_leaf_status(user_id, criterion.pk, status, now)

    return changed
