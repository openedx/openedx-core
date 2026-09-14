"""
The CompetencyAchievementCriteria models: CompetencyCriteriaGroup, CompetencyRuleProfile, and
CompetencyCriterion.

See :ref:`openedx-learning-adr-0002` Decisions 2, 3 and 4 for the design and Decision 7 for each
foreign key's delete behavior, and :ref:`openedx-learning-adr-0003` Decisions 1 and 2 for why
these models carry ``django-simple-history`` tracking and CompetencyTaxonomy does not.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from organizations.models import Organization
from simple_history.models import HistoricalRecords

from openedx_catalog.models import CourseRun
from openedx_django_lib.fields import case_insensitive_char_field, immutable_uuid_field
from openedx_tagging.models import ObjectTag, Tag

from ..rule_payloads import RuleType, validate_rule_payload
from .competency_taxonomy import CompetencyTaxonomy

__all__ = [
    "CompetencyCriteriaGroup",
    "CompetencyCriterion",
    "CompetencyRuleProfile",
    "LogicOperator",
]


class LogicOperator(models.TextChoices):
    """How a CompetencyCriteriaGroup combines its child nodes."""

    AND = "AND", _("And")
    OR = "OR", _("Or")


class CompetencyCriteriaGroup(models.Model):
    """
    An internal AND/OR node in a CompetencyAchievementCriteria expression tree.

    A single CompetencyAchievementCriteria is one root CompetencyCriteriaGroup plus all of its
    descendant groups and leaf :class:`CompetencyCriterion` rows. ``logic_operator`` says how
    this group's own children combine. ``ordering`` gives this group's own position among its
    siblings under their shared parent, which read-time evaluation and event-driven recomputation
    rely on for deterministic, short-circuiting evaluation order. A group's children can be a mix
    of child groups and leaf criteria, and only CompetencyCriteriaGroup carries an ``ordering``
    field, so that mix has no total order; #641 accepts this deliberately. See ADR-0002 Decision 2.

    .. no_pii:
    """

    uuid = immutable_uuid_field()
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="child_groups",
        help_text=_("The parent CompetencyCriteriaGroup. Null means this group is a tree root."),
    )
    tag = models.ForeignKey(
        Tag,
        db_column="oel_tagging_tag_id",
        on_delete=models.CASCADE,
        related_name="competency_criteria_groups",
        help_text=_("The competency (tag) that this criteria tree evaluates mastery of."),
    )
    course = models.ForeignKey(
        CourseRun,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="competency_criteria_groups",
        help_text=_("The course run that scopes this criteria tree for evaluation windowing, if any."),
    )
    name = case_insensitive_char_field(
        max_length=255, blank=True, default="", help_text=_("A human-readable label for this group, if any.")
    )
    ordering = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "Deterministic sibling evaluation sequence. Used to short-circuit evaluation and to order "
            "child scans during event-driven recomputation."
        ),
    )
    logic_operator = models.CharField(
        max_length=3,
        choices=LogicOperator,
        null=True,
        blank=True,
        help_text=_(
            "How this group's children combine. Null only for a group with a single child, where combining "
            "logic is moot; the application layer treats null the same as OR."
        ),
    )

    history = HistoricalRecords()

    class Meta:
        indexes = [
            # ADR-0002 Decision 5, index 1: lookups by competency tag and course scope.
            models.Index(fields=["tag", "course"]),
            # ADR-0002 Decision 5 also lists an index on `parent` (index 2), but Django already
            # indexes every ForeignKey column by default, so a second explicit one here would only
            # cost write throughput without adding any read benefit.
        ]


class CompetencyRuleProfile(models.Model):
    """
    A reusable default evaluation rule, optionally scoped to a taxonomy, course, or organization.

    Each row is scoped by at most one of ``organization``, ``course``, and ``competency_taxonomy``,
    enforced by the check constraint below; the row with all three null is the system default,
    seeded once by migration and never created or deleted through the profile API. See ADR-0002
    Decision 3 for how a :class:`CompetencyCriterion` is assigned one of these, and Decision 4 for
    what happens when more than one scope's profile could apply to the same criterion.

    A profile's scope is immutable after creation; only ``rule_type``, ``rule_payload`` and
    ``archived`` may change.

    .. no_pii:
    """

    uuid = immutable_uuid_field()
    organization = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="competency_rule_profiles",
        help_text=_("The organization this profile is scoped to, if any."),
    )
    course = models.ForeignKey(
        CourseRun,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="competency_rule_profiles",
        help_text=_("The course run this profile is scoped to, if any."),
    )
    competency_taxonomy = models.ForeignKey(
        CompetencyTaxonomy,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="rule_profiles",
        help_text=_("The competency taxonomy this profile is scoped to, if any."),
    )
    # Recomputed in save(), never set directly: null while archived, so any number of archived
    # rows may share a scope while exactly one live row holds it, which is what lets an archived
    # profile be replaced. See ADR-0002 Decision 3.
    scope_code = models.CharField(
        max_length=255,
        null=True,
        editable=False,
        help_text=_(
            "Derived from organization/course/competency_taxonomy; null while archived, otherwise "
            "\"org:X,course:Y,taxonomy:Z\" with each segment blank when that scope column is null."
        ),
    )
    rule_type = models.CharField(max_length=32, choices=RuleType)
    rule_payload = models.JSONField(
        help_text=_(
            'Structured payload whose keys are set by rule_type. A "Grade" payload is '
            '{"op": "gte" | "lte" | "eq", "value": a fraction from 0.0 to 1.0, "scale": "percent"}.'
        )
    )
    archived = models.BooleanField(
        default=False,
        help_text=_(
            "Hides a profile from authoring and from new associations while keeping it queryable, so "
            "criteria already assigned to it stay resolvable."
        ),
    )

    # scope_code is excluded from history: it is a derived, non-editable bookkeeping column (see
    # above), not an author-facing fact worth its own historical row -- the columns it derives
    # from (organization, course, competency_taxonomy, archived) are already tracked, and are what
    # an audit trail actually needs.
    history = HistoricalRecords(excluded_fields=["scope_code"])

    class Meta:
        constraints = [
            # Unconditional, over the derived scope_code column rather than the raw nullable
            # scope columns: MySQL has no partial unique indexes and Django silently skips
            # creating one there. See ADR-0002 Rejected Alternative 6.
            models.UniqueConstraint(fields=["scope_code"], name="oel_cbe_ruleprofile_scope_code_uniq"),
            models.CheckConstraint(
                # Expressed as "at least two of the three scope columns are null", i.e. at most one
                # is non-null.
                condition=(
                    Q(organization__isnull=True, course__isnull=True)
                    | Q(organization__isnull=True, competency_taxonomy__isnull=True)
                    | Q(course__isnull=True, competency_taxonomy__isnull=True)
                ),
                name="oel_cbe_ruleprofile_scope_check",
                violation_error_message=_(
                    "A CompetencyRuleProfile may be scoped to at most one of organization, course, and "
                    "competency_taxonomy."
                ),
            ),
            models.CheckConstraint(
                # Keeps scope_code's invariant honest against QuerySet.update(), which bypasses
                # save(): the database refuses the row rather than letting this get out of sync
                # behind save()'s back.
                condition=(
                    Q(archived=True, scope_code__isnull=True) | Q(archived=False, scope_code__isnull=False)
                ),
                name="oel_cbe_ruleprofile_archived_scope_code_check",
                violation_error_message=_(
                    "An archived CompetencyRuleProfile must have a null scope_code; a live one must not."
                ),
            ),
        ]

    def _check_scope_immutable(self) -> None:
        """Raise ValidationError if the scope columns no longer match what is persisted for this row."""
        if self.pk is None:
            # A new, unsaved instance: there's no persisted scope yet to compare against.
            return
        # Queried rather than compared against a value cached at load time, so a deferred load or
        # a refresh_from_db() cannot bypass the check.
        persisted_scope = (
            CompetencyRuleProfile.objects.filter(pk=self.pk)
            .values_list("organization_id", "course_id", "competency_taxonomy_id")
            .first()
        )
        if persisted_scope is None:
            return
        current_scope = (self.organization_id, self.course_id, self.competency_taxonomy_id)
        if current_scope != persisted_scope:
            raise ValidationError(
                _(
                    "A CompetencyRuleProfile's scope (organization, course, competency_taxonomy) cannot be "
                    "changed after creation."
                )
            )

    def clean(self):
        """Validate scope immutability and the rule_payload shape for rule_type."""
        super().clean()
        self._check_scope_immutable()
        validate_rule_payload(self.rule_type, self.rule_payload)

    def _compute_scope_code(self) -> str | None:
        """Return this profile's scope_code, or None while it is archived."""
        if self.archived:
            return None
        # A blank segment, not "None", for an unset scope: ADR-0002 Decision 3 fixes this format.
        org, course, taxonomy = self.organization_id, self.course_id, self.competency_taxonomy_id
        return f"org:{org or ''},course:{course or ''},taxonomy:{taxonomy or ''}"

    def save(self, *args, **kwargs):
        """On save: recompute and validate scope_code."""
        self.scope_code = self._compute_scope_code()
        # validate_unique() is already enforced by the database.
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)


class CompetencyCriterion(models.Model):
    """
    A leaf node in a CompetencyAchievementCriteria tree: one tag/object association plus its rule.

    A null ``rule_profile`` does NOT mean "resolve the applicable profile at read time." ADR-0002
    Decision 4 resolves which profile (or override) applies at four specific write events
    (creation, a more specific profile appearing later, an author setting a per-criterion
    override, and an override being cleared back to matching the computed profile), and stores
    the result. ``rule_profile`` is null only when an author has set a per-criterion override; in
    every other case it holds the id of the profile that was resolved at the relevant write event
    and is never re-resolved dynamically. Do not add a property, manager method, or other helper
    that recomputes it; that would contradict the ADR.

    When ``rule_type_override`` is set, its ``rule_payload_override``'s shape (see
    :func:`~openedx_learning.applets.cbe.rule_payloads.validate_rule_payload`) is validated from
    ``clean()``, reached from both ``objects.create()`` and a plain ``instance.save()`` via
    ``full_clean()``. A bulk ``QuerySet.update()``, ``bulk_create()``, and a DRF serializer that
    writes straight to the database are NOT covered: none of them build or save a model instance,
    so ``clean()`` never runs.

    .. no_pii:
    """

    uuid = immutable_uuid_field()
    group = models.ForeignKey(
        CompetencyCriteriaGroup,
        db_column="competency_criteria_group_id",
        on_delete=models.CASCADE,
        related_name="criteria",
        help_text=_("The CompetencyCriteriaGroup this leaf criterion belongs to."),
    )
    object_tag = models.ForeignKey(
        ObjectTag,
        db_column="oel_tagging_objecttag_id",
        on_delete=models.CASCADE,
        related_name="competency_criteria",
        help_text=_("The tag/object association that this criterion evaluates."),
    )
    rule_profile = models.ForeignKey(
        CompetencyRuleProfile,
        null=True,
        blank=True,
        db_column="competency_rule_profile_id",
        on_delete=models.RESTRICT,
        related_name="criteria",
        help_text=_("The profile this criterion uses by default. Null only when overrides are set instead."),
    )
    rule_type_override = models.CharField(max_length=32, choices=RuleType, null=True, blank=True)
    rule_payload_override = models.JSONField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        # No db_table override: the table is Django's default, openedx_learning_competencycriterion.
        # verbose_name/verbose_name_plural are set explicitly because Django's default pluralization
        # of "CompetencyCriterion" is "competency criterions". See ADR-0002 Decision 4.
        verbose_name = _("Competency Criterion")
        verbose_name_plural = _("Competency Criteria")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        rule_profile__isnull=False,
                        rule_type_override__isnull=True,
                        rule_payload_override__isnull=True,
                    )
                    | Q(
                        rule_profile__isnull=True,
                        rule_type_override__isnull=False,
                        rule_payload_override__isnull=False,
                    )
                ),
                name="oel_cbe_criterion_profile_xor_override_check",
                violation_error_message=_(
                    "A CompetencyCriterion must have either a rule_profile with no overrides, or both override "
                    "fields set with no rule_profile. Never both, never neither."
                ),
            ),
        ]

    def clean(self):
        """Validate the override rule_payload's shape, when a per-criterion override is set."""
        super().clean()
        if self.rule_type_override is not None:
            validate_rule_payload(self.rule_type_override, self.rule_payload_override)

    def save(self, *args, **kwargs):
        """Persist this criterion, after full_clean() re-validates the override payload, if set."""
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)
