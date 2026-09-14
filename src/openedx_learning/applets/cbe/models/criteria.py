"""
Models for CompetencyAchievementCriteria: CompetencyCriteriaGroup, CompetencyRuleProfile, CompetencyCriterion.

See :ref:`openedx-learning-adr-0002` for the design this module implements, and
:ref:`openedx-learning-adr-0003` for why these three models (and not CompetencyTaxonomy) carry
``django-simple-history`` tracking.

Four of the nine foreign keys here are ``on_delete=models.CASCADE``: ``CompetencyCriteriaGroup.parent``,
``CompetencyCriteriaGroup.tag``, ``CompetencyCriterion.group``, and ``CompetencyCriterion.object_tag``.
The other five stay ``models.PROTECT``: ``CompetencyCriterion.rule_profile``,
``CompetencyCriteriaGroup.course``, ``CompetencyRuleProfile.course``,
``CompetencyRuleProfile.organization``, and ``CompetencyRuleProfile.competency_taxonomy``.

The four are CASCADE because deleting a Tag nobody holds mastery against must succeed, and #655
forbids ``openedx_tagging`` from knowing CBE exists, so the tagging side cannot clear the
criteria tree first; CASCADE lets the tag's delete take the tree with it. ``parent`` and ``group``
also need CASCADE because Django's collector looks up referencing rows in the database rather
than in the set it has already decided to delete, so even a parent and child reached in the same
batch would trip ``PROTECT`` and abort the walk partway down.

This is not a relaxation of ADR-0002 Decision 7: these four CASCADE links are what carries the
collector down to the ``PROTECT`` that enforces it, on #642's three ``Student*Status`` foreign
keys one and two levels below the tag, reached only by walking CASCADE edges. Turning any link in
that chain to ``SET_NULL`` would let a tag delete succeed while learner statuses for it still exist.

``on_delete`` governs the row a foreign key points AT, never the row holding it, and fires on
every row the collector reaches, not only the row passed to ``delete()``. So ``rule_profile``
staying PROTECT does not block a cascading tag delete; it only stops a CompetencyRuleProfile from
being deleted while a criterion references it, Decision 7's archive-only rule at the ORM layer.

The other four: both ``course`` fields match ``openedx_catalog``'s own convention
(``CourseRun.catalog_course`` and ``CatalogCourse.org`` are PROTECT too), and ``SET_NULL`` would
make a course-level group read as a root group, breaking #675's root-group rejection.
``organization`` is PROTECT because ``edx-organizations`` deactivates orgs rather than deleting
them (``remove_organization()``). ``competency_taxonomy`` is PROTECT because a profile's scope is
immutable, so ``SET_NULL`` is forbidden and CASCADE would only move the failure one hop.
"""
from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q, Value
from django.db.models.functions import Cast, Coalesce, Concat
from django.utils.translation import gettext_lazy as _
from organizations.models import Organization
from simple_history.models import HistoricalRecords

from openedx_catalog.models import CourseRun
from openedx_django_lib.fields import case_insensitive_char_field, immutable_uuid_field
from openedx_tagging.models import ObjectTag, Tag

from .competency_taxonomy import CompetencyTaxonomy

__all__ = [
    "CompetencyCriteriaGroup",
    "CompetencyCriterion",
    "CompetencyRuleProfile",
    "LogicOperator",
    "RuleType",
    "validate_rule_payload",
]


class RuleType(models.TextChoices):
    """The evaluation rule types a CompetencyRuleProfile or CompetencyCriterion override can use."""

    VIEW = "View", _("View")
    GRADE = "Grade", _("Grade")
    MASTERY_LEVEL = "MasteryLevel", _("Mastery Level")


class LogicOperator(models.TextChoices):
    """How a CompetencyCriteriaGroup combines its child nodes."""

    AND = "AND", _("And")
    OR = "OR", _("Or")


def validate_rule_payload(rule_type: str, payload: Any) -> None:
    """
    Validate ``payload`` against the shape ADR-0002 Decision 3 defines for ``rule_type``.

    Only ``RuleType.GRADE`` has a defined payload shape in this phase. ``RuleType.VIEW`` and
    ``RuleType.MASTERY_LEVEL`` are valid choices elsewhere but are rejected here, since no
    payload contract exists for them yet. Raises ``django.core.exceptions.ValidationError`` on
    any mismatch; never returns a value.
    """
    if rule_type != RuleType.GRADE:
        raise ValidationError(
            _("Rule type '%(rule_type)s' is not supported yet; only 'Grade' has a defined rule_payload shape.")
            % {"rule_type": rule_type}
        )
    if not isinstance(payload, dict):
        raise ValidationError(_("A 'Grade' rule_payload must be a JSON object."))

    allowed_keys = {"op", "value", "scale"}
    if set(payload.keys()) != allowed_keys:
        raise ValidationError(
            _("A 'Grade' rule_payload must have exactly these keys, no more and no fewer: op, value, scale.")
        )

    if payload.get("op") not in {"gte", "lte", "eq"}:
        raise ValidationError(_("The 'op' in a 'Grade' rule_payload must be one of: gte, lte, eq."))

    value = payload.get("value")
    # isinstance(True, int) is True in Python, so a bool would otherwise pass the numeric check below.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(_("The 'value' in a 'Grade' rule_payload must be a number, not a boolean."))
    if not 0.0 <= value <= 1.0:
        raise ValidationError(
            _(
                "The 'value' in a 'Grade' rule_payload must be a fraction between 0.0 and 1.0 inclusive "
                "(e.g. 0.8 for a passing grade of 80%%), not %(value)r."
            )
            % {"value": value}
        )

    if payload.get("scale") != "percent":
        raise ValidationError(_("The 'scale' in a 'Grade' rule_payload must be 'percent'."))


class CompetencyCriteriaGroup(models.Model):
    """
    An internal AND/OR node in a CompetencyAchievementCriteria expression tree.

    A single CompetencyAchievementCriteria is one root CompetencyCriteriaGroup plus all of its
    descendant groups and leaf :class:`CompetencyCriterion` rows. ``logic_operator`` says how
    this group's children combine; ``ordering`` gives their deterministic evaluation sequence,
    which read-time evaluation and event-driven recomputation both rely on for short-circuiting.
    See ADR-0002 Decision 2.

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
        on_delete=models.PROTECT,
        related_name="competency_criteria_groups",
        help_text=_("The course run that scopes this criteria tree for evaluation windowing, if any."),
    )
    name = case_insensitive_char_field(max_length=255, blank=True, default="")
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
        help_text=_("How this group's children combine. Null until the group has children to combine."),
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
        # ADR-0002 Decision 2 explicitly excludes two constraints here, both for the same reason:
        # a child group cannot be saved until its parent's primary key exists, so at the moment a
        # parent group is being validated/saved, its clean() always sees zero children, whether or
        # not more are about to be attached. There's no single-row state at save time to check either
        # of these against:
        # - A constraint tying `logic_operator` to child count.
        # - A UniqueConstraint on (parent, ordering), which would need to see all siblings, not just
        #   the row being saved.


class CompetencyRuleProfile(models.Model):
    """
    A reusable default evaluation rule, optionally scoped to a taxonomy, course, or organization.

    Each row is scoped by at most one of ``organization``, ``course``, and ``competency_taxonomy``,
    enforced by the check constraint below; the row with all three null is the system default,
    seeded once by migration and never created or deleted through the profile API. See ADR-0002
    Decision 3 for how a :class:`CompetencyCriterion` is assigned one of these, and Decision 4 for
    what happens when more than one scope's profile could apply to the same criterion.

    Editing a profile may change ``rule_type``/``rule_payload`` only: the scope fields
    (``organization``, ``course``, ``competency_taxonomy``) are immutable after creation, so that
    criteria already resolved to this profile's scope are never silently re-governed. This is
    enforced in ``clean()`` and ``save()`` by comparing against the scope this row had when
    loaded. That comparison covers every ``instance.save()``, including one loaded with
    ``.only()``/``.defer()`` that skipped some scope columns, in which case the comparison falls
    back to reading the persisted scope directly rather than skipping the check. It does not
    cover a bulk ``QuerySet.update()``, since that path never loads or constructs a model
    instance at all.

    .. no_pii:
    """

    # Set at from_db() time to the scope this row had when it was loaded from the database, so
    # clean()/save() can detect an attempt to change it. None for a newly-constructed instance,
    # meaning there's nothing yet to compare against. Deliberately not underscore-prefixed:
    # from_db() is a classmethod, so it sets this through a local `instance` variable rather than
    # `self`, which pylint's protected-access check can't tell apart from reaching into another
    # object's internals.
    loaded_scope: tuple[int | None, int | None, int | None] | None = None

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
        on_delete=models.PROTECT,
        related_name="competency_rule_profiles",
        help_text=_("The course run this profile is scoped to, if any."),
    )
    competency_taxonomy = models.ForeignKey(
        CompetencyTaxonomy,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="rule_profiles",
        help_text=_("The competency taxonomy this profile is scoped to, if any."),
    )
    # Always non-null, including for the system-default row (all three scope columns null), so a
    # plain UniqueConstraint on this one column enforces "at most one profile row per distinct
    # scope" identically on every backend. SQL never treats two NULLs as equal, so a unique
    # constraint directly on the three nullable scope columns would let e.g. two rows that both
    # set only organization_id=5 both exist. See ADR-0002 Decision 3.
    scope_code = models.GeneratedField(
        expression=Concat(
            Value("org:"),
            Coalesce(Cast(F("organization_id"), output_field=models.CharField(max_length=20)), Value("")),
            Value(",course:"),
            Coalesce(Cast(F("course_id"), output_field=models.CharField(max_length=20)), Value("")),
            Value(",taxonomy:"),
            Coalesce(Cast(F("competency_taxonomy_id"), output_field=models.CharField(max_length=20)), Value("")),
        ),
        output_field=models.CharField(max_length=255),
        db_persist=True,
    )
    rule_type = models.CharField(max_length=32, choices=RuleType)
    rule_payload = models.JSONField(
        help_text=_("Structured payload keyed by rule_type; see validate_rule_payload for the shape it must match.")
    )
    archived = models.BooleanField(
        default=False,
        help_text=_(
            "Set instead of deleting a profile that's no longer wanted. Archived profiles are hidden from "
            "authoring and new associations but remain queryable, so existing criteria stay resolvable."
        ),
    )
    uuid = immutable_uuid_field()

    history = HistoricalRecords(excluded_fields=["scope_code"])

    class Meta:
        constraints = [
            # Do NOT add `condition=` here. A conditional UniqueConstraint compiles to a partial
            # index, which MySQL (this project's tested and production database) does not support:
            # Django only raises a non-fatal system-check warning (models.W036) and silently skips
            # creating the constraint, leaving uniqueness completely unenforced there, while SQLite
            # (used for quick local test runs) does support partial indexes and would mask the gap
            # in that environment. See ADR-0002 Rejected Alternative 6. The generated `scope_code`
            # column above exists specifically so a plain, unconditional UniqueConstraint works
            # identically on every backend.
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
        ]

    @classmethod
    def from_db(cls, db, field_names, values):
        """Capture the scope this row had when loaded, so clean()/save() can detect an edit to it."""
        instance = super().from_db(db, field_names, values)
        # field_names holds attnames (e.g. "organization_id"), not field names. Only capture when
        # all three are present and unloaded (not deferred), so this never triggers extra queries.
        scope_attnames = {"organization_id", "course_id", "competency_taxonomy_id"}
        if scope_attnames.issubset(field_names):
            instance.loaded_scope = (
                instance.organization_id,
                instance.course_id,
                instance.competency_taxonomy_id,
            )
        return instance

    def _check_scope_immutable(self) -> None:
        """Raise ValidationError if the scope columns no longer match what was loaded from the database."""
        loaded_scope = self.loaded_scope
        if loaded_scope is None:
            if self.pk is None:
                # A new, unsaved instance: there's no persisted scope yet to compare against.
                return
            # from_db() didn't capture the scope, because this instance came from a deferred/
            # only() load that skipped one or more scope columns. Read the persisted scope back
            # from the database directly, rather than silently skipping the check: a deferred
            # load must not be a way to bypass immutability. This costs one extra query, but only
            # on this rare path, which is already paying for extra field-loading queries anyway.
            # Guarded against the row having since been deleted, in which case there's nothing
            # left to compare against either.
            row = (
                CompetencyRuleProfile.objects
                .filter(pk=self.pk)
                .values_list("organization_id", "course_id", "competency_taxonomy_id")
                .first()
            )
            if row is None:
                return
            loaded_scope = row
        current_scope = (self.organization_id, self.course_id, self.competency_taxonomy_id)
        if current_scope != loaded_scope:
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

    def save(self, *args, **kwargs):
        """Persist this profile, after re-checking scope immutability."""
        self._check_scope_immutable()
        super().save(*args, **kwargs)
        self.loaded_scope = (self.organization_id, self.course_id, self.competency_taxonomy_id)


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
        on_delete=models.PROTECT,
        related_name="criteria",
        help_text=_("The profile this criterion uses by default. Null only when overrides are set instead."),
    )
    rule_type_override = models.CharField(max_length=32, choices=RuleType, null=True, blank=True)
    rule_payload_override = models.JSONField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        db_table = "openedx_learning_competencycriteria"
        # Django's default pluralization of "CompetencyCriterion" is the ungrammatical
        # "competency criterions"; set both explicitly, matching ADR-0002 Decision 4's
        # terminology (one leaf is a criterion, the collection is CompetencyCriteria) and
        # following CompetencyTaxonomy, which sets both for the same reason.
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
