"""
Public API for Competency-Based Education (CBE).
"""
from __future__ import annotations

from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey

from openedx_catalog.api import get_course_run
from openedx_catalog.models_api import CourseRun
from openedx_tagging.api import create_taxonomy, get_object_tags, tag_object
from openedx_tagging.models import ObjectTag, Tag, Taxonomy
from openedx_tagging.rules import ObjectTagPermissionItem, UserType

from .models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    LogicOperator,
)

__all__ = [
    "CompetencyCriterionArchivedError",
    "associate_competency_criterion",
    "bulk_update_competency_criteria",
    "create_competency_criterion",
    "create_competency_taxonomy",
    "get_competency_rule_profiles",
    "is_competency_taxonomy",
    "resolve_competency_tag",
    "resolve_supplied_leaf_group",
    "select_competency_taxonomies",
]


class CompetencyCriterionArchivedError(Exception):
    """
    Raised when a request would edit a CompetencyCriterion that has been archived.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this exception, or when it is raised, without a deprecation
    cycle.
    """


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


def resolve_competency_tag(tag_id: int) -> Tag:
    """
    Return the competency Tag identified by ``tag_id``.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    Raises Tag.DoesNotExist if no such Tag exists, or if it isn't on a CompetencyTaxonomy.
    Shared by the view and :func:`associate_competency_criterion` so both agree on what counts
    as valid.
    """
    tag = Tag.objects.get(pk=tag_id)
    # Explicit None check narrows the type for mypy.
    if tag.taxonomy is None or not is_competency_taxonomy(tag.taxonomy):
        raise Tag.DoesNotExist("Tag is not on a CompetencyTaxonomy.")
    return tag


def _create_leaf_group(
    tag: Tag,
    course_run: CourseRun,
    logic_operator: str | None = None,
) -> CompetencyCriteriaGroup:
    """
    Return a fresh leaf CompetencyCriteriaGroup under ``tag``'s root/course-level groups.

    Gets or creates the root and course-level groups, then always creates a new leaf: a
    tag/course pair can hold more than one leaf, one per criterion. The root and course-level
    group names are truncated to fit the name column; they are never shown in the UI.

    Must run inside a transaction holding the tag row lock.
    """
    name_max_length = CompetencyCriteriaGroup._meta.get_field("name").max_length
    # Both get_or_create() lookups match archived rows deliberately, so an archived container is
    # never replaced; rejecting or reactivating it is the archived-group checks' decision.
    root, _ = CompetencyCriteriaGroup.objects.get_or_create(
        tag=tag,
        parent=None,
        defaults={"name": f"{tag.value} (root)"[:name_max_length]},
    )
    course_level_group, _ = CompetencyCriteriaGroup.objects.get_or_create(
        tag=tag,
        course=course_run,
        parent=root,
        defaults={"name": f"{tag.value} — {course_run.title}"[:name_max_length]},
    )
    return CompetencyCriteriaGroup.objects.create(
        tag=tag,
        course=None,
        parent=course_level_group,
        logic_operator=logic_operator or LogicOperator.OR,
    )


def resolve_supplied_leaf_group(group_id: int, tag: Tag, course_run: CourseRun) -> CompetencyCriteriaGroup:
    """
    Return the CompetencyCriteriaGroup identified by ``group_id``, validated as a usable leaf.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    Raises CompetencyCriteriaGroup.DoesNotExist if it doesn't exist, or ValidationError (keyed
    "group_id") if it isn't a leaf, doesn't belong to ``tag``, or its course doesn't match
    ``course_run``.
    """
    group = CompetencyCriteriaGroup.objects.get(pk=group_id)
    if group.parent_id is None or group.course_id is not None:
        raise ValidationError({"group_id": _("group_id must reference a leaf CompetencyCriteriaGroup.")})
    if group.tag_id != tag.id:
        raise ValidationError({"group_id": _("group_id must belong to the given competency tag.")})
    if group.parent.course_id != course_run.id:
        raise ValidationError({"group_id": _("group_id must belong to the given course.")})
    return group


def create_competency_criterion(
    group: CompetencyCriteriaGroup,
    object_id: str,
    rule_profile_id: int | None = None,
    rule_type_override: str | None = None,
    rule_payload_override: dict | None = None,
) -> CompetencyCriterion:
    """
    Create and return a CompetencyCriterion under ``group``, tagging ``object_id`` along the way.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    ``tag_object()`` replaces an object's full tag list for a taxonomy rather than appending, so
    this reads the object's existing tags first and unions in the new one, rather than silently
    dropping a sibling competency's tag under the same taxonomy. Raises ValidationError (keyed
    "object_id") when the taxonomy refuses the tag, e.g. it allows only one tag per object.

    When no rule fields are supplied, resolves to the live profile scoped to the tag's taxonomy,
    else the live system default, rather than persisting null: ADR-0002 Decision 4 always
    resolves to a concrete profile, and the model's own xor-override constraint forbids leaving
    all three fields null. Course- and organization-scoped profiles are not resolved here, since
    a criterion is authored at taxonomy scope.
    """
    tag = group.tag
    # tag.taxonomy_id is nullable at the model level; already guaranteed set by the caller.
    assert tag.taxonomy_id is not None
    existing_values = [existing_tag.value for existing_tag in get_object_tags(object_id, taxonomy_id=tag.taxonomy_id)]
    if tag.value not in existing_values:
        existing_values.append(tag.value)
    try:
        tag_object(object_id, tag.taxonomy, existing_values)
    except ValueError as exc:
        raise ValidationError({"object_id": str(exc)}) from exc
    object_tag = ObjectTag.objects.get(object_id=object_id, taxonomy_id=tag.taxonomy_id, tag_id=tag.id)

    if rule_profile_id is None and rule_type_override is None and rule_payload_override is None:
        live = CompetencyRuleProfile.objects.filter(archived=False, organization__isnull=True, course__isnull=True)
        # CompetencyTaxonomy shares its pk with its parent Taxonomy row.
        profile = live.filter(competency_taxonomy_id=tag.taxonomy_id).first() or live.get(
            competency_taxonomy__isnull=True
        )
        rule_profile_id = profile.id

    # Parent-competency dominance/containment validation belongs here, before creation.

    return CompetencyCriterion.objects.create(
        group=group,
        object_tag=object_tag,
        rule_profile_id=rule_profile_id,
        rule_type_override=rule_type_override,
        rule_payload_override=rule_payload_override,
    )


def _reject_duplicate_criterion(tag: Tag, object_id: str, group_id: int | None) -> None:
    """
    Raise ValidationError if ``object_id`` is already associated with ``tag`` (in ``group_id``, when supplied).

    Must run inside a transaction holding the tag row lock.
    """
    assert tag.taxonomy_id is not None  # narrowing for mypy; callers resolve competency tags only
    existing_object_tag = ObjectTag.objects.filter(
        object_id=object_id, taxonomy_id=tag.taxonomy_id, tag_id=tag.id,
    ).first()
    if existing_object_tag is None:
        return
    duplicate_criteria = CompetencyCriterion.objects.filter(object_tag=existing_object_tag)
    if group_id is None:
        # Can't know which group slot is intended, so any existing criterion is a duplicate.
        if duplicate_criteria.exists():
            raise ValidationError(
                {"object_id": _("A CompetencyCriterion already associates this tag with this object_id.")}
            )
    elif duplicate_criteria.filter(group_id=group_id).exists():
        # A different explicit group is a deliberate second association (ADR-0002), not a duplicate.
        raise ValidationError(
            {"group_id": _("A CompetencyCriterion already associates this tag with this object_id in this group.")}
        )


def _validate_rule_fields(
    rule_profile_id: int | None,
    rule_type_override: str | None,
    rule_payload_override: dict | None,
) -> None:
    """
    Raise ValidationError, keyed by the offending field, unless the rule fields form a valid combination.

    Valid: a live ``rule_profile_id`` alone, both overrides without a profile, or none of the three.
    """
    if rule_profile_id is not None and (rule_type_override is not None or rule_payload_override is not None):
        raise ValidationError({"rule_profile_id": _(
            "rule_profile_id cannot be combined with rule_type_override or rule_payload_override."
        )})
    if (rule_type_override is None) != (rule_payload_override is None):
        supplied_field = "rule_type_override" if rule_type_override is not None else "rule_payload_override"
        raise ValidationError({supplied_field: _(
            "rule_type_override and rule_payload_override must be supplied together."
        )})
    if rule_profile_id is None:
        return
    archived = CompetencyRuleProfile.objects.filter(pk=rule_profile_id).values_list("archived", flat=True).first()
    if archived is None:
        raise ValidationError({"rule_profile_id": _("No CompetencyRuleProfile matches rule_profile_id.")})
    if archived:
        # Archived profiles stay resolvable for existing criteria but are hidden from new associations.
        raise ValidationError({"rule_profile_id": _("rule_profile_id references an archived CompetencyRuleProfile.")})


def associate_competency_criterion(
    tag_id: int,
    object_id: str,
    *,
    group_id: int | None = None,
    logic_operator: str | None = None,
    rule_profile_id: int | None = None,
    rule_type_override: str | None = None,
    rule_payload_override: dict | None = None,
) -> CompetencyCriterion:
    """
    Associate ``object_id`` with the competency ``tag_id`` names, creating a criterion for it.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    The public entry point for the create-criterion endpoint; the caller is expected to have
    already checked ``oel_tagging.can_tag_object``. Rejects re-targeting the same group a
    (tag_id, object_id) pair already uses, and rejects a duplicate via the derive-or-create
    path; a *different* explicit group is a deliberate second association per ADR-0002, not a
    duplicate. Branches on ``group_id`` to resolve or derive/create a leaf, then creates the
    criterion, all inside one transaction so a downstream failure rolls back any group just
    created.

    Raises ValidationError (never DRF's) on rejected input, including an invalid combination of
    rule fields or a missing or archived ``rule_profile_id``; Tag.DoesNotExist if ``tag_id``
    doesn't resolve to a competency tag; CompetencyCriteriaGroup.DoesNotExist if ``group_id``
    doesn't resolve.
    """
    tag = resolve_competency_tag(tag_id)
    # tag.taxonomy is already confirmed set; re-asserted since that doesn't cross function boundaries for mypy.
    assert tag.taxonomy_id is not None

    try:
        usage_key = UsageKey.from_string(object_id)
    except InvalidKeyError as exc:
        raise ValidationError({"object_id": _("object_id is not a valid usage key.")}) from exc

    try:
        course_run = get_course_run(usage_key.course_key)
    except CourseRun.DoesNotExist as exc:
        raise ValidationError({"object_id": _("No course run matches object_id's course.")}) from exc

    if group_id is not None and logic_operator is not None:
        raise ValidationError({"logic_operator": _("group_id and logic_operator cannot both be supplied.")})
    _validate_rule_fields(rule_profile_id, rule_type_override, rule_payload_override)

    with transaction.atomic():
        # Serializes the duplicate check and root creation; MySQL has no partial unique index for one root per tag.
        Tag.objects.select_for_update().get(pk=tag.pk)
        _reject_duplicate_criterion(tag, object_id, group_id)
        if group_id is not None:
            group = resolve_supplied_leaf_group(group_id, tag, course_run)
        else:
            group = _create_leaf_group(tag, course_run, logic_operator)
        return create_competency_criterion(
            group=group,
            object_id=object_id,
            rule_profile_id=rule_profile_id,
            rule_type_override=rule_type_override,
            rule_payload_override=rule_payload_override,
        )


def _get_system_default_rule_profile() -> CompetencyRuleProfile | None:
    """
    Return the system-default profile, or None once it has been archived.
    """
    return CompetencyRuleProfile.objects.filter(
        organization__isnull=True,
        course__isnull=True,
        competency_taxonomy__isnull=True,
        archived=False,
    ).first()


def _resolve_applicable_rule_profile(
    criterion: CompetencyCriterion,  # pylint: disable=unused-argument
    system_default: CompetencyRuleProfile | None,
) -> CompetencyRuleProfile | None:
    """
    Return the profile ADR-0002 Decision 4's assignment table gives ``criterion``, or None.

    A stand-in for the shared resolution helper #679 owns. Only the system default exists in this
    phase, so every criterion resolves to ``system_default``, which the caller looks up once per
    batch so a large batch costs no extra queries. Callers still ask per criterion: once scoped
    profiles exist, two criteria in the same group need not resolve to the same one.
    """
    return system_default


def _validate_rule_source(
    rule_profile_id: int | None,
    rule_type_override: str | None,
    rule_payload_override: dict | None,
) -> None:
    """
    Validate the one rule source a bulk update applies.

    Runs the create path's rule-field checks, then also refuses a request naming no rule source: a
    bulk update has no applicable-profile fallback, and ADR-0002 Decision 4 allows a criterion no
    other state. Override values are validated once for the whole batch through the model's own
    ``full_clean()``, so the payload contract has no second copy here. Raises ValidationError keyed
    by the REST field names.
    """
    _validate_rule_fields(rule_profile_id, rule_type_override, rule_payload_override)
    if rule_profile_id is not None:
        return
    # _validate_rule_fields has already refused half a pair, so one override being None means both are.
    if rule_type_override is None:
        raise ValidationError(
            {NON_FIELD_ERRORS: _("Supply rule_profile_id, or both rule_type_override and rule_payload_override.")}
        )
    CompetencyCriterion(
        rule_type_override=rule_type_override,
        rule_payload_override=rule_payload_override,
    ).full_clean(exclude=["group", "object_tag", "rule_profile"], validate_unique=False, validate_constraints=False)


def _apply_rule_source(
    criterion: CompetencyCriterion,
    rule_profile_id: int | None,
    rule_type_override: str | None,
    rule_payload_override: dict | None,
    user: UserType,
    *,
    system_default: CompetencyRuleProfile | None,
) -> None:
    """
    Point ``criterion`` at ``rule_profile_id``, or give it the override values, saving only if that changes it.

    Override values that exactly match the criterion's applicable profile reassign it to that
    profile instead. All three rule columns are written in one save, so the criterion's new history
    row never records both a profile and overrides, or neither.
    """
    if rule_profile_id is None:
        applicable = _resolve_applicable_rule_profile(criterion, system_default)
        # Compares the parsed rule, never stored JSON text, so key order and formatting don't matter.
        if (
            applicable is not None
            and applicable.rule_type == rule_type_override
            and applicable.rule_payload == rule_payload_override
        ):
            rule_profile_id = applicable.id

    target: tuple[int | None, str | None, dict | None]
    if rule_profile_id is not None:
        target, change_reason = (rule_profile_id, None, None), "Reassigned to rule profile"
    else:
        target, change_reason = (None, rule_type_override, rule_payload_override), "Rule values set"
    if (criterion.rule_profile_id, criterion.rule_type_override, criterion.rule_payload_override) == target:
        return

    criterion.rule_profile_id, criterion.rule_type_override, criterion.rule_payload_override = target
    # Set on the instance so attribution doesn't depend on simple_history's request middleware.
    criterion._history_user = user  # type: ignore[attr-defined]  # pylint: disable=protected-access
    criterion._change_reason = change_reason  # type: ignore[attr-defined]  # pylint: disable=protected-access
    # Never QuerySet.update(): it skips post_save, so simple_history would record nothing.
    criterion.save(update_fields=["rule_profile", "rule_type_override", "rule_payload_override"])


def bulk_update_competency_criteria(
    criterion_ids: list[int],
    group_id: int,
    *,
    rule_profile_id: int | None = None,
    rule_type_override: str | None = None,
    rule_payload_override: dict | None = None,
    user: UserType,
) -> list[CompetencyCriterion]:
    """
    Apply one rule source to every criterion ``criterion_ids`` names in one leaf group, atomically.

    UNSTABLE: the criteria-authoring family is incomplete, so its validation and group update
    and archive behavior may change this function's shape without a deprecation cycle.

    Supply either ``rule_profile_id`` or both override values; each criterion's three rule columns
    are then rewritten together, so a profile clears any overrides and overrides clear any profile.
    Learner status is never touched.

    Returns the named criteria in request order. Raises PermissionDenied if ``user`` fails
    ``oel_tagging.can_tag_object`` for the group's taxonomy and course,
    CompetencyCriteriaGroup.DoesNotExist if the group doesn't exist, CompetencyCriterion.DoesNotExist
    if any named id isn't a criterion of that group, and ValidationError, keyed by the REST field
    names, for a non-leaf group, an empty or repeated id list, or an invalid rule source. Raises
    CompetencyCriterionArchivedError if any named criterion is archived.
    """
    with transaction.atomic():
        group = CompetencyCriteriaGroup.objects.select_related("tag__taxonomy", "parent__course").get(
            pk=group_id
        )
        # Criteria hang only off leaf groups, and a leaf's course is on its course-level parent.
        if group.parent is None or group.course_id is not None or group.parent.course is None:
            raise ValidationError({"group_id": _("group_id must reference a leaf CompetencyCriteriaGroup.")})
        # A group's tag is always a competency tag, so its taxonomy is always set.
        assert group.tag.taxonomy is not None
        perm_obj = ObjectTagPermissionItem(taxonomy=group.tag.taxonomy, object_id=str(group.parent.course.course_key))
        if not user.has_perm("oel_tagging.can_tag_object", perm_obj):
            raise PermissionDenied

        if not criterion_ids:
            raise ValidationError({"criterion_ids": _("criterion_ids must name at least one criterion.")})
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValidationError({"criterion_ids": _("criterion_ids must not name the same criterion twice.")})
        _validate_rule_source(rule_profile_id, rule_type_override, rule_payload_override)

        # Locked so a concurrent archive can't slip in after the archived check, and so a concurrent
        # identical request waits and then sees nothing to change instead of writing a second history row.
        criteria_by_id = CompetencyCriterion.objects.select_for_update().filter(group=group).in_bulk(criterion_ids)
        missing_ids = [criterion_id for criterion_id in criterion_ids if criterion_id not in criteria_by_id]
        if missing_ids:
            raise CompetencyCriterion.DoesNotExist(
                _("Not criteria of this group: {criterion_ids}.").format(
                    criterion_ids=", ".join(str(criterion_id) for criterion_id in missing_ids)
                )
            )

        # One archived criterion refuses the whole batch: ADR 0002 Decision 4 closes archived criteria to authoring.
        archived_ids = [criterion_id for criterion_id in criterion_ids if criteria_by_id[criterion_id].archived]
        if archived_ids:
            raise CompetencyCriterionArchivedError(
                _("Archived criteria cannot be edited: {criterion_ids}.").format(
                    criterion_ids=", ".join(str(criterion_id) for criterion_id in archived_ids)
                )
            )

        # Looked up once for the whole batch, and only when override values need comparing against it.
        system_default = _get_system_default_rule_profile() if rule_profile_id is None else None
        criteria = [criteria_by_id[criterion_id] for criterion_id in criterion_ids]
        for criterion in criteria:
            _apply_rule_source(
                criterion,
                rule_profile_id,
                rule_type_override,
                rule_payload_override,
                user,
                system_default=system_default,
            )
        return criteria
