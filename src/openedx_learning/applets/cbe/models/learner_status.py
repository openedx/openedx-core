"""
Models tracking a learner's mastery status at each level of a competency criteria tree.

:class:`StudentCompetencyCriteriaStatus`, :class:`StudentCompetencyCriteriaGroupStatus`, and
:class:`StudentCompetencyStatus` track the same fact -- a learner's current mastery rank -- at
the leaf (:class:`~openedx_learning.applets.cbe.models.criteria.CompetencyCriterion`), group
(:class:`~openedx_learning.applets.cbe.models.criteria.CompetencyCriteriaGroup`), and top
(:class:`~openedx_tagging.models.Tag`) levels of a criteria tree, respectively. All three share
one shape: a user, a foreign key to the node they track, a status, and caller-supplied
``created``/``modified`` timestamps.

There is one row per learner per node, updated in place: finding a learner's current status at
any level is a lookup of that single row, not a query for the most recent of several (ADR-0003
Decision 5). Each model accepts any status value its constraints permit;
the rules that decide *which* writes are allowed, that an automatic write may raise a status but
never lower it (ADR-0004 Decision 4), and that a staff correction may lower one (ADR-0004
Decision 6), are enforced in the API layer, not here: by the time a write reaches these models
there is no caller context left to tell those cases apart.

``created`` and ``modified`` are caller-supplied UTC datetimes, not automatic. A caller
performing a conditional raise must pass ``modified`` in the same ``update()`` call; there is no
``auto_now`` to do it for them, deliberately, because ``auto_now`` does not fire on
``QuerySet.update()`` and would silently leave the column stale on exactly that path.

Each model's ``user`` foreign key is ``on_delete=models.CASCADE``, not ``PROTECT``: ``PROTECT``
would let this library veto ``User.delete()`` platform-wide, from openedx-platform code that has
no reason to know CBE rows exist. A learner's status is a derived fact about that learner, so it
goes when they do. ``SET_NULL`` is not an option, because a null ``user_id`` would break the
``(user_id, node_id)`` uniqueness each model's in-place updates rest on.

Each model's foreign key to the node it tracks (``criterion``, ``group``, or ``tag``) is
``on_delete=models.PROTECT``, and it is load-bearing, not defensive. #641's four foreign keys
that carry Django's collector down the criteria tree -- ``CompetencyCriteriaGroup.parent``,
``CompetencyCriteriaGroup.tag``, ``CompetencyCriterion.group``, and
``CompetencyCriterion.object_tag`` -- are ``CASCADE``, so deleting a ``Tag`` walks into its
groups and then their criteria. These ``PROTECT`` values are the only thing that stops that
walk, and they are what turns ADR-0002 Decision 7's guarantee into behavior: the delete succeeds
when no learner holds status beneath the row and raises ``ProtectedError`` when one does. Django
evaluates ``PROTECT`` on every row the collector reaches, not only the row passed to
``delete()``, which is why transitive cases work too. #675 re-implements the same predicate at
the API layer for a clean status code; this is the backstop for paths that never reach it.

These three ``PROTECT`` values are deliberately stricter than the application-layer predicate.
ADR-0002 Decision 7, as amended for #655, names only the leaf table
``StudentCompetencyCriteriaStatus`` as determining whether a record is protected, and treats the
two roll-up tables as derived and not independently checked. The database makes no such
distinction, so a roll-up row with no leaf beneath it would also block a delete. That should not
occur, and failing closed is the right default for a backstop.

Each model's ``status`` foreign key is also ``on_delete=models.PROTECT``, because the lookup
table it points to (:class:`CompetencyMasteryStatus`) is system-owned immutable data, seeded by
migration and never deleted.
"""
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx_django_lib.fields import manual_date_time_field
from openedx_tagging.models import Tag

from .criteria import CompetencyCriteriaGroup, CompetencyCriterion

__all__ = [
    "MasteryStatus",
    "CompetencyMasteryStatus",
    "StudentCompetencyCriteriaStatus",
    "StudentCompetencyCriteriaGroupStatus",
    "StudentCompetencyStatus",
]


class MasteryStatus(models.IntegerChoices):
    """
    Ranks of competency mastery.

    Each member's value is the pinned primary key of its ``CompetencyMasteryStatus``
    row, seeded by the ``seed_competency_mastery_statuses`` data migration.

    The integer value of each member is also its rank, lowest to highest mastery.
    Pinning the rank order into the stored id is what lets raising a learner's
    status be written as one conditional ``UPDATE``
    (``... WHERE status_id < new_status_id``) instead of a read, a comparison in
    Python, and a write: ADR-0004 Decision 4 requires that, because two concurrent
    writers doing read-compare-write can each read the same old value, and the
    later of the two writes then lowers what the earlier one had already raised.

    Because the ids are pinned, a new status can be added above or below the
    existing three, but never between them.

    This subclasses ``IntegerChoices`` rather than ``enum.IntEnum`` so that
    Django's migration serializer writes a member as a bare integer instead of an
    import of this module. That keeps an already-applied migration's meaning
    independent of later edits to this enum.
    """

    ATTEMPTED_NOT_DEMONSTRATED = 1, "AttemptedNotDemonstrated"
    PARTIALLY_ATTEMPTED = 2, "PartiallyAttempted"
    DEMONSTRATED = 3, "Demonstrated"


class CompetencyMasteryStatus(models.Model):
    """
    Lookup table of the mastery statuses a competency can be assigned.

    This table is system-owned lookup data, seeded by the
    ``seed_competency_mastery_statuses`` data migration, and is
    treated as immutable configuration rather than user-authored rows
    (ADR-0002 Decision 6.1). See :class:`MasteryStatus` for the pinned ids and
    names of its rows.

    .. no_pii:
    """

    # ADR-0002 Decision 5 index 10.
    status = models.CharField(max_length=64, unique=True)

    def __str__(self) -> str:
        """User-facing string representation of a CompetencyMasteryStatus."""
        return self.status

    class Meta:
        verbose_name = "Competency Mastery Status"
        verbose_name_plural = "Competency Mastery Statuses"
        # id order is rank order (see MasteryStatus), so a default listing of
        # this table already reads lowest to highest mastery.
        ordering = ("id",)


class StudentCompetencyCriteriaStatus(models.Model):
    """
    A learner's current mastery status for one leaf ``CompetencyCriterion``.

    There is one row per learner per criterion, updated in place: finding a
    learner's current status is a lookup of that single row, not a query for the
    most recent of several (ADR-0003 Decision 5).

    This model accepts any status value the constraints below permit. The rules
    that decide *which* writes are allowed, that an automatic write may raise a
    status but never lower it, and that a staff correction may lower one, are
    enforced in the API layer, not here: by the time a write reaches this model
    there is no caller context left to tell those two cases apart.

    ``created`` and ``modified`` are caller-supplied UTC datetimes, not automatic.
    A caller performing a conditional raise must pass ``modified`` in the same
    ``update()`` call; there is no ``auto_now`` to do it for them, deliberately,
    because ``auto_now`` does not fire on ``QuerySet.update()`` and would silently
    leave the column stale on exactly that path.

    This table stores a user foreign key and a status value, and no personal
    data of its own.

    .. no_pii:
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="competency_criteria_statuses",
    )
    criterion = models.ForeignKey(
        CompetencyCriterion,
        db_column="competency_criteria_id",
        on_delete=models.PROTECT,
        related_name="student_statuses",
    )
    status = models.ForeignKey(
        CompetencyMasteryStatus,
        on_delete=models.PROTECT,
        related_name="student_criteria_statuses",
    )
    created = manual_date_time_field()
    modified = manual_date_time_field()

    def __str__(self) -> str:
        """User-facing string representation of a StudentCompetencyCriteriaStatus."""
        return f"{self.user}: {self.criterion} = {self.status}"

    class Meta:
        verbose_name = "Student Competency Criteria Status"
        verbose_name_plural = "Student Competency Criteria Statuses"
        constraints = [
            # ADR-0002 Decision 5 index 6. This is what makes "one row per
            # learner and criterion" true, which is the precondition for the
            # in-place conditional update described above: it is load-bearing,
            # not a lookup optimisation.
            models.UniqueConstraint(
                fields=("user", "criterion"),
                name="oex_learning_studentcriteriastatus_user_criterion_uniq",
            ),
        ]


class StudentCompetencyCriteriaGroupStatus(models.Model):
    """
    A learner's current mastery status for one ``CompetencyCriteriaGroup`` node.

    There is one row per learner per group, updated in place: finding a
    learner's current status is a lookup of that single row, not a query for the
    most recent of several (ADR-0003 Decision 5).

    This model accepts any status value the constraints below permit. The rules
    that decide *which* writes are allowed, that an automatic write may raise a
    status but never lower it, and that a staff correction may lower one, are
    enforced in the API layer, not here: by the time a write reaches this model
    there is no caller context left to tell those two cases apart.

    ``created`` and ``modified`` are caller-supplied UTC datetimes, not automatic.
    A caller performing a conditional raise must pass ``modified`` in the same
    ``update()`` call; there is no ``auto_now`` to do it for them, deliberately,
    because ``auto_now`` does not fire on ``QuerySet.update()`` and would silently
    leave the column stale on exactly that path.

    This table stores a user foreign key and a status value, and no personal
    data of its own.

    .. no_pii:
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="competency_criteria_group_statuses",
    )
    group = models.ForeignKey(
        CompetencyCriteriaGroup,
        db_column="competency_criteria_group_id",
        on_delete=models.PROTECT,
        related_name="student_statuses",
    )
    status = models.ForeignKey(
        CompetencyMasteryStatus,
        on_delete=models.PROTECT,
        related_name="student_criteria_group_statuses",
    )
    created = manual_date_time_field()
    modified = manual_date_time_field()

    def __str__(self) -> str:
        """User-facing string representation of a StudentCompetencyCriteriaGroupStatus."""
        return f"{self.user}: {self.group} = {self.status}"

    class Meta:
        verbose_name = "Student Competency Criteria Group Status"
        verbose_name_plural = "Student Competency Criteria Group Statuses"
        constraints = [
            # ADR-0002 Decision 5 index 7. This is what makes "one row per
            # learner and group" true, which is the precondition for the
            # in-place conditional update described above: it is load-bearing,
            # not a lookup optimisation.
            models.UniqueConstraint(
                fields=("user", "group"),
                name="oex_learning_studentcriteriagroupstatus_user_group_uniq",
            ),
        ]


class StudentCompetencyStatus(models.Model):
    """
    A learner's current mastery status for one competency (``Tag``).

    There is one row per learner per competency, updated in place: finding a
    learner's current status is a lookup of that single row, not a query for the
    most recent of several (ADR-0003 Decision 5).

    This model accepts any status value the allow-list constraint below permits.
    The rules that decide *which* writes are allowed, that an automatic write may
    raise a status but never lower it, and that a staff correction may lower one,
    are enforced in the API layer, not here: by the time a write reaches this
    model there is no caller context left to tell those two cases apart.

    ``created`` and ``modified`` are caller-supplied UTC datetimes, not automatic.
    A caller performing a conditional raise must pass ``modified`` in the same
    ``update()`` call; there is no ``auto_now`` to do it for them, deliberately,
    because ``auto_now`` does not fire on ``QuerySet.update()`` and would silently
    leave the column stale on exactly that path.

    This table stores a user foreign key and a status value, and no personal
    data of its own.

    .. no_pii:
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="competency_mastery_statuses",
    )
    tag = models.ForeignKey(
        Tag,
        db_column="oel_tagging_tag_id",
        on_delete=models.PROTECT,
        related_name="student_competency_statuses",
    )
    status = models.ForeignKey(
        CompetencyMasteryStatus,
        on_delete=models.PROTECT,
        related_name="student_competency_statuses",
    )
    created = manual_date_time_field()
    modified = manual_date_time_field()

    def __str__(self) -> str:
        """User-facing string representation of a StudentCompetencyStatus."""
        return f"{self.user}: {self.tag} = {self.status}"

    class Meta:
        verbose_name = "Student Competency Status"
        verbose_name_plural = "Student Competency Statuses"
        constraints = [
            # ADR-0002 Decision 5 index 8. This is what makes "one row per
            # learner and competency" true, which is the precondition for the
            # in-place conditional update above: it is load-bearing, not a
            # lookup optimisation.
            models.UniqueConstraint(
                fields=("user", "tag"),
                name="oex_learning_studentcompetencystatus_user_tag_uniq",
            ),
            # Allow list, not a negation of the excluded value: a future fourth
            # status should be rejected here by default rather than silently
            # permitted.
            models.CheckConstraint(
                condition=models.Q(status__in=(MasteryStatus.PARTIALLY_ATTEMPTED, MasteryStatus.DEMONSTRATED)),
                name="oex_learning_studentcompetencystatus_status_allowed",
                violation_error_message=_(
                    "A competency-level status may only be PartiallyAttempted or Demonstrated, "
                    "since it represents overall demonstration state, not an in-progress state."
                ),
            ),
        ]
