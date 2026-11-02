"""
The Competency Criteria tree models: CompetencyCriteriaGroup, CompetencyRuleProfile, and
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

from openedx_catalog.models_api import CourseRun
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
    An internal AND/OR node in a Competency Criteria tree.

    A single Competency Criteria tree is one root CompetencyCriteriaGroup plus all of its
    descendant groups and leaf :class:`CompetencyCriterion` rows. ``logic_operator`` says how
    this group's own children combine. ``ordering`` gives this group's own position among its
    siblings under their shared parent: the read API returns a parent's child groups ordered by
    ``(ordering, id)``, and evaluation ignores it because ``AND`` or ``OR`` over a group's
    children cannot depend on the order they are read in. A group's children can be a mix of
    child groups and leaf criteria, and only CompetencyCriteriaGroup carries an ``ordering``
    field, so that mix has no total order; #641 accepts this deliberately. See ADR-0002 Decision 2.

    ``scope_code`` is derived in ``save()``, so writes that skip ``save()`` (``bulk_create()``,
    ``QuerySet.update()``, ``loaddata``, raw SQL) can leave a root without a code or with a stale
    one, and the one-root-per-competency rule then goes unenforced for that root.

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
    archived = models.BooleanField(
        default=False,
        help_text=_(
            "Set instead of deleting this group once learner status exists for it or anything beneath "
            "it. Hides the group from authoring and new associations while keeping it queryable, so its "
            "criteria and existing learner status rows stay resolvable."
        ),
    )
    # Recomputed in save(); set for roots only, archived or not, so an archived root keeps blocking a second one.
    # Null below the root: a leaf has no natural key to put here, and the check constraint below only says a
    # coded row is a root, not that every root is coded, so non-null would not make the rule any stronger.
    scope_code = models.CharField(
        max_length=255,
        null=True,
        editable=False,
        help_text=_(
            "Derived from tag and parent; \"tag:X\" for a root group, null for every other group."
        ),
    )

    # scope_code is derived bookkeeping; tag and parent, which it derives from, are already tracked.
    history = HistoricalRecords(excluded_fields=["scope_code"])

    class Meta:
        indexes = [
            # ADR-0002 Decision 5, index 1: lookups by competency tag and course scope.
            models.Index(fields=["tag", "course"]),
            # ADR-0002 Decision 5 also lists an index on `parent` (index 2), but Django already
            # indexes every ForeignKey column by default, so a second explicit one here would only
            # cost write throughput without adding any read benefit.
        ]
        # No constraint tying `logic_operator` to child count, and no UniqueConstraint on (parent,
        # ordering): a child group cannot be saved until its parent's primary key exists, so
        # neither has a single-row state to check at save time. See ADR-0002 Decision 2.
        constraints = [
            # Backstops _create_leaf_group()'s get_or_create() for course-level groups. It has no
            # `condition` because MySQL has no partial indexes: a unique index allows repeated
            # NULLs, and `course` is NULL for roots and leaves. Roots are covered by scope_code instead.
            models.UniqueConstraint(
                fields=["tag", "course", "parent"],
                name="oel_cbe_criteria_group_one_course_group_per_tag_course",
                violation_error_message=_(
                    "A competency tag may have at most one course-level CompetencyCriteriaGroup per course."
                ),
            ),
            # Over the derived scope_code column because MySQL has no partial unique index for `parent IS NULL`.
            models.UniqueConstraint(
                fields=["scope_code"],
                name="oel_cbe_criteria_group_scope_code_uniq",
                violation_error_message=_("A competency tag may have at most one root CompetencyCriteriaGroup."),
            ),
            # One-directional: on MySQL, which cannot defer constraint checks, Django's delete collector
            # sets each descendant's nullable `parent` to NULL before deleting it, so "every root has a
            # scope_code" would reject those rows mid-delete.
            models.CheckConstraint(
                condition=Q(scope_code__isnull=True) | Q(parent__isnull=True),
                name="oel_cbe_criteria_group_scope_code_root_only_check",
                violation_error_message=_("Only a root CompetencyCriteriaGroup may have a scope_code."),
            ),
        ]

    def _check_tree_position_immutable(self) -> None:
        """Raise ValidationError if tag, parent or course no longer matches what is persisted for this row."""
        if self.pk is None:
            return
        # Queried rather than compared against a value cached at load time, so a deferred load or
        # a refresh_from_db() cannot bypass the check.
        persisted = (
            CompetencyCriteriaGroup.objects.filter(pk=self.pk).values_list("tag_id", "parent_id", "course_id").first()
        )
        if persisted is not None and (self.tag_id, self.parent_id, self.course_id) != persisted:
            raise ValidationError(
                _("A CompetencyCriteriaGroup's tag, parent and course cannot be changed after creation.")
            )

    def _compute_scope_code(self) -> str | None:
        """Return this group's scope_code: set for a root group, None for every other group."""
        if self.parent_id is not None:
            return None
        return f"tag:{self.tag_id}"

    def save(self, *args, **kwargs):
        """Reject a moved or re-tagged group, then set scope_code; tag and parent are fixed, so the code is too."""
        self._check_tree_position_immutable()
        self.scope_code = self._compute_scope_code()
        super().save(*args, **kwargs)


class CompetencyRuleProfile(models.Model):
    """
    A reusable default evaluation rule, optionally scoped to a taxonomy, course, or organization.

    Each row is scoped by at most one of ``organization``, ``course``, and ``competency_taxonomy``,
    enforced by the check constraint below; the row with all three null is the system default,
    seeded once by migration and never created or deleted through the profile API. When more than
    one scope could apply to the same criterion, the most specific one wins (course beats
    organization/taxonomy), and taxonomy_overrides_org breaks the one remaining tie between an
    organization- and a taxonomy-scoped profile. See ADR-0002 Decision 3 & 4 for more details.

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
    A leaf node in a Competency Criteria tree: one tag/object association plus its rule.

    A null `rule_profile` does not mean "resolve at read time." ADR-0002 Decision 4 resolves and
    stores the applicable profile (or override) at specific write events only; `rule_profile` is
    null only when a per-criterion override is set instead. Do not add a property, manager method,
    or other helper that recomputes it; that would contradict the ADR.

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
    rule_type_override = models.CharField(
        max_length=32, choices=RuleType, null=True, blank=True,
        help_text=_("Overrides rule_profile's rule_type for this criterion. Set only when rule_profile is null."),
    )
    rule_payload_override = models.JSONField(
        null=True, blank=True,
        help_text=_("Overrides rule_profile's rule_payload for this criterion. Set only when rule_profile is null."),
    )
    archived = models.BooleanField(
        default=False,
        help_text=_(
            "Set instead of deleting this criterion once learner status exists for it. Hides the "
            "criterion from authoring and new associations while keeping it queryable, so existing "
            "learner status rows stay resolvable."
        ),
    )

    history = HistoricalRecords()

    class Meta:
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
