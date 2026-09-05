"""
Models for CompetencyAchievementCriteria: CompetencyCriteriaGroup, CompetencyRuleProfile, CompetencyCriterion.

See :ref:`openedx-learning-adr-0002` for the design this module implements, and
:ref:`openedx-learning-adr-0003` for why these three models (and not CompetencyTaxonomy) carry
``django-simple-history`` tracking.

Seven of the nine foreign keys here are ``on_delete=models.CASCADE``: both ``CompetencyCriteriaGroup``
tree links (``parent``, ``tag``), its ``course`` scope, both ``CompetencyCriterion`` links (``group``,
``object_tag``), and ``CompetencyRuleProfile``'s ``course`` and ``competency_taxonomy`` scope links.
The other two stay ``models.PROTECT``: ``CompetencyCriterion.rule_profile`` and
``CompetencyRuleProfile.organization``.

CASCADE expresses containment: a row on the CASCADE side is meaningless once its referent is gone,
so its own delete has no separate policy to enforce. The tree links (``parent``, ``tag``, ``group``,
``object_tag``) also have to be CASCADE for a mechanical reason: Django's collector looks up
referencing rows in the database rather than in the set it has already decided to delete, so even a
parent and child reached in the same batch would trip PROTECT and abort the walk partway down. Those
same CASCADE edges are what carries the collector down to the PROTECT that actually enforces
ADR-0002 Decision 7 for learner data: #642's three ``Student*Status`` foreign keys, one and two
levels below the tag, are reached only by walking these edges, never relaxed by them.

``CompetencyRuleProfile.course`` and ``.competency_taxonomy`` are CASCADE for an ADR-level reason,
not a mechanical one: Decision 7 (as amended) says a taxonomy or course is only ever hard-deleted
once nothing beneath it needs protecting, so a profile scoped to it is safe to remove at the same
time rather than blocking that delete. ``.organization`` stays PROTECT because an ``Organization``
is not a competency-definition record covered by that reasoning, and ``edx-organizations``
deactivates orgs rather than deleting them.

``rule_profile`` staying PROTECT is Decision 7's actual backstop for a profile itself: a
CompetencyRuleProfile is never hard-deleted by a *direct* delete (retirement is archive-only), and
this is what makes that hold at the ORM layer, by blocking any attempt to delete one out from under
a criterion still assigned to it.

One non-obvious consequence of the collector's database-not-pending-set lookup described above:
deleting a CompetencyTaxonomy whose taxonomy-scoped profile is itself assigned to a
CompetencyCriterion raises ProtectedError naming that criterion, even though the criterion would
also be cascade-deleted in the same operation through the tag chain. See
test_criteria_deletion.py's "residual tension" section for what this needs before it can be fixed
(a fifth ADR-0002 Decision 4 reassignment event), and why it cannot be reached with this phase's
data.
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

from ..rule_payloads import _RULE_PAYLOAD_SPECS, RuleType, validate_rule_payload
from .competency_taxonomy import CompetencyTaxonomy

__all__ = [
    "CompetencyCriteriaGroup",
    "CompetencyCriterion",
    "CompetencyRuleProfile",
    "LogicOperator",
    "RuleType",
    "validate_rule_payload",
]

# The declared choices for both rule_type fields below, derived from the payload-spec registry
# (see rule_payloads.py) rather than hand-listed, so a rule type can never be offered as a choice
# without also having a payload spec that makes it actually saveable.
_RULE_TYPE_CHOICES = [(rule_type, RuleType(rule_type).label) for rule_type in _RULE_PAYLOAD_SPECS]


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
    criteria already resolved to this profile's scope are never silently re-governed. ``clean()``
    enforces this by comparing the current scope columns against what is actually persisted for
    this row, so the check holds regardless of whether this instance was loaded with a partial
    ``.only()``/``.defer()`` that skipped some scope columns. It does not cover a bulk
    ``QuerySet.update()``, since that path never loads or constructs a model instance at all.

    ``rule_payload``'s shape (see :func:`~openedx_learning.applets.cbe.rule_payloads.validate_rule_payload`)
    is likewise validated from ``clean()``, reached from both ``objects.create()`` and a plain
    ``instance.save()`` via ``full_clean()``. A bulk ``QuerySet.update()``, ``bulk_create()``, and a
    DRF serializer that writes straight to the database are NOT covered: none of them build or save
    a model instance, so ``clean()`` never runs.

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
    # A plain column, written explicitly in save() below, NOT a database GeneratedField: null
    # while archived, and the "org:X,course:Y,taxonomy:Z" string (see save()) while live. This is
    # what lets the UniqueConstraint below stay a plain, unconditional one on every backend this
    # project supports, including MySQL, which does not support the conditional/partial unique
    # indexes that a naive "unique unless archived" rule would otherwise need (see ADR-0002
    # Rejected Alternative 6): SQL never treats two NULLs as equal, so any number of archived rows
    # may share a scope while exactly one live row holds it. A plain column also can't be rewritten
    # by Django's collector, unlike a GeneratedField: deleting a scope owner (a CompetencyTaxonomy
    # or CourseRun) whose foreign key here is nullable and CASCADE nulls that one column on this
    # row before deleting it, on any backend that can't defer constraint checks (MySQL); a
    # GeneratedField would recompute from that nulled value and could collide with another row
    # already occupying the resulting blank scope, raising IntegrityError instead of completing
    # the cascade. A plain column is untouched by that nulling, so this row keeps its true
    # scope_code, unseen by anyone, until the row itself is deleted.
    scope_code = models.CharField(
        max_length=255,
        null=True,
        editable=False,
        help_text=_(
            "Derived from organization/course/competency_taxonomy; null while archived, otherwise "
            "\"org:X,course:Y,taxonomy:Z\" with each segment blank when that scope column is null. "
            "Recomputed in save(); never set directly."
        ),
    )
    rule_type = models.CharField(max_length=32, choices=_RULE_TYPE_CHOICES)
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

    # scope_code is excluded from history: it is a derived, non-editable bookkeeping column (see
    # above), not an author-facing fact worth its own historical row -- the columns it derives
    # from (organization, course, competency_taxonomy, archived) are already tracked, and are what
    # an audit trail actually needs.
    history = HistoricalRecords(excluded_fields=["scope_code"])

    class Meta:
        constraints = [
            # A plain, unconditional UniqueConstraint, deliberately: scope_code is a plain,
            # always-non-null-while-live column (see its definition above), not a conditional
            # index over the raw nullable scope columns. MySQL (this project's tested and
            # production database) does not support conditional/partial unique indexes -- Django
            # only raises a non-fatal system-check warning (models.W036) and silently skips
            # creating such a constraint there, while SQLite (used for quick local test runs)
            # does support them and would mask the gap in that environment. See ADR-0002 Rejected
            # Alternative 6.
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
        # Always queries the database directly, rather than comparing against a value cached at
        # load time: that avoids a deferred/only() load, or a refresh_from_db() call, silently
        # bypassing this check. Explicitly targets self._state.db, the alias this instance
        # actually belongs to, so an instance loaded from a non-default database is not silently
        # compared against the wrong one. Guarded against the row having since been deleted, in
        # which case there's nothing left to compare against either.
        persisted_scope = (
            CompetencyRuleProfile.objects.using(self._state.db)
            .filter(pk=self.pk)
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

    def save(self, *args, **kwargs):
        """Recompute scope_code, then persist this profile after full_clean() re-validates it."""
        self.scope_code = None if self.archived else (
            f"org:{'' if self.organization_id is None else self.organization_id},"
            f"course:{'' if self.course_id is None else self.course_id},"
            f"taxonomy:{'' if self.competency_taxonomy_id is None else self.competency_taxonomy_id}"
        )
        # validate_unique and validate_constraints are left to the database: the unique and check
        # constraints above enforce them identically and without the extra queries full_clean()
        # would otherwise run to pre-check them in Python. Matches CourseRun.save() at
        # src/openedx_catalog/models/course_run.py. Neither the non-editable scope_code nor the
        # nullable override-style fields on this model cause full_clean() to reject an otherwise
        # valid row: Django's own Field.validate() skips every check for a field with
        # editable=False, and a blank=True field with an empty value is skipped by clean_fields()
        # before validation runs at all.
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
        on_delete=models.PROTECT,
        related_name="criteria",
        help_text=_("The profile this criterion uses by default. Null only when overrides are set instead."),
    )
    rule_type_override = models.CharField(max_length=32, choices=_RULE_TYPE_CHOICES, null=True, blank=True)
    rule_payload_override = models.JSONField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        # No db_table override, so the table is Django's default,
        # openedx_learning_competencycriterion. ADR-0002 Decision 4's heading reads
        # "CompetencyCriterion concept (CompetencyCriteria database table)", which names the
        # domain concept the way every other heading in that ADR does rather than instructing a
        # rename. No model anywhere in src/ overrides db_table, so every table in this library
        # is <app_label>_<model>.
        #
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

    def save(self, *args, **kwargs):
        """Persist this criterion, after full_clean() re-validates the override payload, if set."""
        # See CompetencyRuleProfile.save() above for why validate_unique/validate_constraints are
        # skipped here too, and why the nullable override fields don't trip full_clean() when unset.
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)
