"""
Models tracking a learner's mastery status at each level of a criteria tree.

:class:`StudentCompetencyCriterionStatus` covers one leaf :class:`~openedx_learning.models.CompetencyCriterion`,
:class:`StudentCompetencyCriteriaGroupStatus` covers one
:class:`~openedx_learning.models.CompetencyCriteriaGroup`, and :class:`StudentCompetencyStatus` covers one
:class:`~openedx_tagging.models.Tag`. Each holds one row per learner per node, updated in place (ADR-0003
Decision 5). Which writes may raise or lower a status is decided in the API layer, not here (ADR-0004
Decisions 4 and 6). ``created`` and ``modified`` are caller-supplied because ``auto_now`` does not fire on
``QuerySet.update()``, which is how conditional raises are written. ``user`` is ``CASCADE`` so that this
library never vetoes ``User.delete()`` platform-wide. The node foreign keys are ``PROTECT`` because every
foreign key above them in the criteria tree is ``CASCADE``: without ``PROTECT``, deleting a tag, taxonomy,
group or ``ObjectTag`` would silently delete the learner status beneath it (ADR-0002 Decision 7).
"""
from django.conf import settings
from django.db import models

from openedx_django_lib.fields import manual_date_time_field
from openedx_tagging.models import Tag

from .criteria import CompetencyCriteriaGroup, CompetencyCriterion

__all__ = [
    "MasteryStatus",
    "CompetencyMasteryStatus",
    "StudentCompetencyStatus",
    "StudentCompetencyCriterionStatus",
    "StudentCompetencyCriteriaGroupStatus",
]


class MasteryStatus(models.IntegerChoices):
    """
    Ranks of competency mastery, lowest to highest.

    The values are the ``CompetencyMasteryStatus`` primary keys, and the database compares them
    directly to decide whether a write raises a status, so their order is a contract. They are
    spaced by 10 so that a new rank can go between two existing ones without renumbering
    stored rows.
    """

    ATTEMPTED_NOT_DEMONSTRATED = 10, "AttemptedNotDemonstrated"
    PARTIALLY_ATTEMPTED = 20, "PartiallyAttempted"
    DEMONSTRATED = 30, "Demonstrated"


class CompetencyMasteryStatus(models.Model):
    """
    Lookup table of the mastery statuses a competency can be assigned.

    System-owned lookup data, seeded by the ``seed_competency_mastery_statuses`` data
    migration and treated as immutable configuration, not user-authored rows (ADR-0002
    Decision 6.1). See :class:`MasteryStatus` for the pinned ids and names of its rows.

    .. no_pii:
    """

    id = models.SmallAutoField(primary_key=True)

    # ADR-0002 Decision 5 index 10.
    status = models.CharField(max_length=64, unique=True)

    def __str__(self) -> str:
        """User-facing string representation of a CompetencyMasteryStatus."""
        return self.status


class StudentCompetencyStatus(models.Model):
    """
    A learner's current mastery status for one competency (``Tag``).

    One row per learner per tag, updated in place (ADR-0003 Decision 5).

    .. no_pii:
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    tag = models.ForeignKey(
        Tag,
        db_column="oel_tagging_tag_id",
        on_delete=models.PROTECT,
        related_name="+",
    )
    status = models.ForeignKey(
        CompetencyMasteryStatus,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created = manual_date_time_field()
    modified = manual_date_time_field()

    class Meta:
        constraints = [
            # ADR-0002 Decision 5 index 8. This is what makes "one row per learner and
            # competency" true, which is the precondition for updating a status in place
            # with a conditional UPDATE.
            models.UniqueConstraint(
                fields=("user", "tag"),
                name="oex_learning_studentcompetencystatus_user_tag_uniq",
            ),
            # A learner can still demonstrate a competency later, in another course, and
            # nothing here can tell when that stops being possible, so a top-level row never
            # records AttemptedNotDemonstrated. This is an allow list rather than a negation
            # of the excluded value, so a future status is rejected here by default rather
            # than silently permitted.
            models.CheckConstraint(
                condition=models.Q(status__in=(MasteryStatus.PARTIALLY_ATTEMPTED, MasteryStatus.DEMONSTRATED)),
                name="oex_learning_studentcompetencystatus_status_allowed",
            ),
        ]


class StudentCompetencyCriterionStatus(models.Model):
    """
    A learner's current mastery status for one leaf ``CompetencyCriterion``.

    One row per learner per criterion, updated in place (ADR-0003 Decision 5).

    .. no_pii:
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    criterion = models.ForeignKey(
        CompetencyCriterion,
        db_column="competency_criteria_id",
        on_delete=models.PROTECT,
        related_name="+",
    )
    status = models.ForeignKey(
        CompetencyMasteryStatus,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created = manual_date_time_field()
    modified = manual_date_time_field()

    class Meta:
        constraints = [
            # ADR-0002 Decision 5 index 6.
            models.UniqueConstraint(
                fields=("user", "criterion"),
                name="oex_learning_studentcriterionstatus_user_criterion_uniq",
            ),
        ]


class StudentCompetencyCriteriaGroupStatus(models.Model):
    """
    A learner's current mastery status for one ``CompetencyCriteriaGroup``.

    One row per learner per group, updated in place (ADR-0003 Decision 5).

    .. no_pii:
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    group = models.ForeignKey(
        CompetencyCriteriaGroup,
        db_column="competency_criteria_group_id",
        on_delete=models.PROTECT,
        related_name="+",
    )
    status = models.ForeignKey(
        CompetencyMasteryStatus,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created = manual_date_time_field()
    modified = manual_date_time_field()

    class Meta:
        constraints = [
            # ADR-0002 Decision 5 index 7.
            models.UniqueConstraint(
                fields=("user", "group"),
                name="oex_learning_studentcriteriagroupstatus_user_group_uniq",
            ),
        ]
