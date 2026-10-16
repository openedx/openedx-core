"""
Tests for the CBE public API surface (openedx_learning.api).
"""
from collections.abc import Iterator
from typing import Any

import pytest
import rules
from django.apps import apps
from django.contrib.auth.models import User as UserType  # pylint: disable=imported-auth-user
from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db import connection
from django.db.utils import IntegrityError
from django.test.utils import CaptureQueriesContext
from organizations.models import Organization
from rules.permissions import permissions as rule_permissions

from openedx_catalog.models import CatalogCourse, CourseRun
from openedx_learning.api import (
    CompetencyCriterionArchivedError,
    associate_competency_criterion,
    bulk_update_competency_criteria,
    create_competency_taxonomy,
    get_competency_rule_profiles,
    is_competency_taxonomy,
    resolve_supplied_leaf_group,
    select_competency_taxonomies,
)
from openedx_learning.applets.cbe import api as cbe_api
from openedx_learning.applets.cbe.api import _create_leaf_group
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    LogicOperator,
    RuleType,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriterionStatus,
    StudentCompetencyStatus,
)
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

pytestmark = pytest.mark.django_db

GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}


def test_create_competency_taxonomy_saves_both_rows() -> None:
    """
    create_competency_taxonomy() saves a CompetencyTaxonomy and Taxonomy row that both
    carry the given field values.
    """
    result = create_competency_taxonomy(
        name="Nursing",
        description="Nursing competencies",
        enabled=False,
        allow_multiple=False,
        allow_free_text=True,
        read_only=True,
        export_id="nursing-v1",
    )

    assert isinstance(result, CompetencyTaxonomy)
    assert is_competency_taxonomy(result) is True

    for taxonomy in (
        CompetencyTaxonomy.objects.get(pk=result.pk),
        Taxonomy.objects.get(pk=result.pk),
    ):
        assert taxonomy.name == "Nursing"
        assert taxonomy.description == "Nursing competencies"
        assert taxonomy.enabled is False
        assert taxonomy.allow_multiple is False
        assert taxonomy.allow_free_text is True
        assert taxonomy.read_only is True
        assert taxonomy.export_id == "nursing-v1"


def test_create_competency_taxonomy_defaults() -> None:
    """
    create_competency_taxonomy() applies the same defaults as create_taxonomy() when
    only name is given, including an auto-generated export_id.
    """
    result = create_competency_taxonomy(name="Welding")

    assert result.enabled is True
    assert result.allow_multiple is True
    assert result.allow_free_text is False
    assert result.read_only is False
    assert result.export_id


def make_course_run(organization: Organization, course_code: str, run_code: str) -> CourseRun:
    """Create a CourseRun distinct from the `course_run` fixture, for the same-tag-different-course tests."""
    catalog_course = CatalogCourse.objects.create(org=organization, course_code=course_code)
    return CourseRun.objects.create(catalog_course=catalog_course, run_code=run_code)


def usage_key(course_run: CourseRun, block_id: str) -> str:
    """Build a gradeable-subsection-shaped usage key string under `course_run`."""
    key = course_run.course_key
    assert key is not None
    return f"block-v1:{key.org}+{key.course}+{key.run}+type@sequential+block@{block_id}"


def test_is_competency_taxonomy() -> None:
    """
    is_competency_taxonomy() is True for a competency taxonomy, False for a plain one.
    """
    competency = CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")
    plain = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1")

    assert is_competency_taxonomy(Taxonomy.objects.get(pk=competency.pk)) is True
    assert is_competency_taxonomy(plain) is False


def test_is_competency_taxonomy_on_child_instance_directly() -> None:
    """
    is_competency_taxonomy() also returns True when handed a CompetencyTaxonomy
    instance directly, not just a parent Taxonomy fetched from the DB.
    """
    competency = CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")
    assert is_competency_taxonomy(competency) is True


def test_is_competency_taxonomy_on_unsaved_instance() -> None:
    """
    is_competency_taxonomy() returns False for an unsaved Taxonomy, rather than raising.
    """
    # pk is None, so the reverse one-to-one descriptor short-circuits and raises
    # RelatedObjectDoesNotExist, which Django defines as an AttributeError subclass
    # precisely so hasattr() catches it here instead of propagating.
    unsaved = Taxonomy(name="Unsaved", export_id="unsaved-v1")
    assert is_competency_taxonomy(unsaved) is False


def test_select_competency_taxonomies_avoids_n_plus_1(django_assert_num_queries) -> None:
    """
    select_competency_taxonomies() joins the CompetencyTaxonomy row in, so checking
    is_competency_taxonomy() on every row in the queryset costs one query, not N+1.
    """
    competency1 = CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")
    competency2 = CompetencyTaxonomy.objects.create(name="Welding", export_id="welding-v1")
    plain = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1")
    # Scoped to just these three: unfiltered Taxonomy.objects.all() would also pick up
    # any taxonomies seeded outside this test, which would make the True/False counts
    # below depend on incidental fixture data.
    taxonomies = Taxonomy.objects.filter(pk__in=[competency1.pk, competency2.pk, plain.pk])

    with django_assert_num_queries(1):
        results = [is_competency_taxonomy(t) for t in select_competency_taxonomies(taxonomies)]

    assert results.count(True) == 2
    assert results.count(False) == 1


def test_get_competency_rule_profiles_returns_the_seeded_default(
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """get_competency_rule_profiles() returns the system default an instance starts with."""
    assert list(get_competency_rule_profiles()) == [default_rule_profile]


def test_get_competency_rule_profiles_excludes_archived(
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """get_competency_rule_profiles() leaves retired profiles out."""
    archived = CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE,
        rule_payload=GRADE_PAYLOAD,
        competency_taxonomy=competency_taxonomy,
        archived=True,
    )

    profiles = list(get_competency_rule_profiles())

    assert archived not in profiles
    assert profiles == [default_rule_profile]


def test_get_competency_rule_profiles_is_ordered_by_id(
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
    organization: Organization,
) -> None:
    """
    get_competency_rule_profiles() returns profiles in ascending id order.

    Without a deterministic order, paginating the collection would repeat and skip rows.
    """
    taxonomy_scoped = CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE, rule_payload=GRADE_PAYLOAD, competency_taxonomy=competency_taxonomy
    )
    organization_scoped = CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE, rule_payload=GRADE_PAYLOAD, organization=organization
    )

    expected = [default_rule_profile, taxonomy_scoped, organization_scoped]
    assert list(get_competency_rule_profiles()) == expected


# ==============================================================================================
# _create_leaf_group
# ==============================================================================================


def test_create_leaf_group_builds_the_full_hierarchy_on_first_use(
    tag: Tag, course_run: CourseRun
) -> None:
    """A first call with no existing groups creates a root, a course-level group, and a leaf."""
    leaf = _create_leaf_group(tag, course_run)

    course_level = leaf.parent
    assert course_level is not None
    root = course_level.parent
    assert root is not None
    assert root.parent is None
    assert root.tag_id == tag.id
    assert root.course_id is None
    assert root.name == f"{tag.value} (root)"
    assert course_level.tag_id == tag.id
    assert course_level.course_id == course_run.id
    assert course_level.name == f"{tag.value} — {course_run.title}"
    assert leaf.tag_id == tag.id
    assert leaf.course_id is None
    assert leaf.logic_operator == LogicOperator.OR


def test_create_leaf_group_passes_through_an_explicit_logic_operator(
    tag: Tag, course_run: CourseRun
) -> None:
    """A supplied logic_operator is stored on the leaf as-is, not defaulted to OR."""
    leaf = _create_leaf_group(tag, course_run, logic_operator=LogicOperator.AND)
    assert leaf.logic_operator == LogicOperator.AND


def test_create_leaf_group_reuses_the_root_and_course_level_group_on_a_second_call(
    tag: Tag, course_run: CourseRun
) -> None:
    """A second call for the same tag and course reuses the root and course-level group."""
    first_leaf = _create_leaf_group(tag, course_run)
    second_leaf = _create_leaf_group(tag, course_run)

    assert first_leaf.id != second_leaf.id
    assert first_leaf.parent_id == second_leaf.parent_id
    assert first_leaf.parent is not None
    assert second_leaf.parent is not None
    assert first_leaf.parent.parent_id == second_leaf.parent.parent_id
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, parent__isnull=True).count() == 1
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, course=course_run).count() == 1


def test_create_leaf_group_creates_a_new_course_level_group_for_a_different_course(
    tag: Tag, course_run: CourseRun, organization: Organization
) -> None:
    """A second course under the same tag gets its own course-level group, but shares the root."""
    other_course_run = make_course_run(organization, "Python200", "Fall2026")

    first_leaf = _create_leaf_group(tag, course_run)
    second_leaf = _create_leaf_group(tag, other_course_run)

    assert first_leaf.parent is not None
    assert second_leaf.parent is not None
    assert first_leaf.parent.parent_id == second_leaf.parent.parent_id
    assert first_leaf.parent_id != second_leaf.parent_id
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, parent__isnull=True).count() == 1
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, course__isnull=False).count() == 2


def test_create_leaf_group_reuses_a_root_a_concurrent_request_already_committed(
    tag: Tag, course_run: CourseRun
) -> None:
    """
    A root created out-of-band (standing in for a concurrent request's winning commit) is reused.

    Callers are serialized on the tag row, so a request that waited on the lock finds the
    winner's committed root and ``get_or_create()`` fetches it instead of creating a second one.
    """
    already_committed_root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None, name="pre-existing root")

    leaf = _create_leaf_group(tag, course_run)

    assert leaf.parent is not None
    assert leaf.parent.parent_id == already_committed_root.id
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, parent__isnull=True).count() == 1


def test_create_leaf_group_reuses_a_course_level_group_a_concurrent_request_already_committed(
    tag: Tag, course_run: CourseRun
) -> None:
    """The course-level group half of the same guarantee, isolated from the root."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)
    already_committed_course_level = CompetencyCriteriaGroup.objects.create(
        tag=tag, course=course_run, parent=root, name="pre-existing course-level group",
    )

    leaf = _create_leaf_group(tag, course_run)

    assert leaf.parent_id == already_committed_course_level.id
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, course=course_run).count() == 1


def test_create_leaf_group_truncates_derived_names_to_fit_the_name_column(
    competency_taxonomy: CompetencyTaxonomy, course_run: CourseRun
) -> None:
    """A maximum-length tag value (500) and course title (255) still produce storable root and course-level names."""
    long_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="t" * 500)
    course_run.title = "c" * 255
    course_run.save()

    leaf = _create_leaf_group(long_tag, course_run)

    course_level = leaf.parent
    assert course_level is not None
    root = course_level.parent
    assert root is not None
    name_max_length = CompetencyCriteriaGroup._meta.get_field("name").max_length
    assert len(CompetencyCriteriaGroup.objects.get(pk=root.pk).name) == name_max_length
    assert len(CompetencyCriteriaGroup.objects.get(pk=course_level.pk).name) == name_max_length


def test_course_level_group_unique_constraint_rejects_a_second_group_for_the_same_tag_and_course(
    tag: Tag, course_run: CourseRun
) -> None:
    """
    The DB constraint backstopping _create_leaf_group()'s course-level get_or_create() exists.

    Proven directly (bypassing get_or_create) so a silently-dropped migration can't go unnoticed.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)
    CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    with pytest.raises(IntegrityError):
        CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)


# ==============================================================================================
# resolve_supplied_leaf_group
# ==============================================================================================


def test_resolve_supplied_leaf_group_returns_the_leaf_when_it_is_valid(tag: Tag, course_run: CourseRun) -> None:
    """A group_id that names a genuine, matching leaf is returned unchanged."""
    leaf = _create_leaf_group(tag, course_run)
    assert resolve_supplied_leaf_group(leaf.id, tag, course_run) == leaf


def test_resolve_supplied_leaf_group_raises_does_not_exist_for_an_unknown_group(
    tag: Tag, course_run: CourseRun
) -> None:
    """An unknown group_id raises CompetencyCriteriaGroup.DoesNotExist."""
    with pytest.raises(CompetencyCriteriaGroup.DoesNotExist):
        resolve_supplied_leaf_group(999999, tag, course_run)


def test_resolve_supplied_leaf_group_rejects_a_root_group_as_not_a_leaf(tag: Tag, course_run: CourseRun) -> None:
    """A root group (no parent) is not a usable leaf."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)
    with pytest.raises(ValidationError, match="group_id"):
        resolve_supplied_leaf_group(root.id, tag, course_run)


def test_resolve_supplied_leaf_group_rejects_a_course_level_group_as_not_a_leaf(
    tag: Tag, course_run: CourseRun
) -> None:
    """A course-level group (has its own course) is not a usable leaf either."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    with pytest.raises(ValidationError, match="group_id"):
        resolve_supplied_leaf_group(course_level.id, tag, course_run)


def test_resolve_supplied_leaf_group_rejects_a_group_for_a_different_tag(
    tag: Tag, course_run: CourseRun, competency_taxonomy: CompetencyTaxonomy
) -> None:
    """A leaf that belongs to a different competency tag is rejected."""
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Other Competency")
    leaf = _create_leaf_group(other_tag, course_run)
    with pytest.raises(ValidationError, match="group_id"):
        resolve_supplied_leaf_group(leaf.id, tag, course_run)


def test_resolve_supplied_leaf_group_rejects_a_group_for_a_different_course(
    tag: Tag, course_run: CourseRun, organization: Organization
) -> None:
    """A leaf whose course-level parent belongs to a different course is rejected."""
    other_course_run = make_course_run(organization, "Python200", "Fall2026")
    leaf = _create_leaf_group(tag, other_course_run)
    with pytest.raises(ValidationError, match="group_id"):
        resolve_supplied_leaf_group(leaf.id, tag, course_run)


# ==============================================================================================
# associate_competency_criterion
# ==============================================================================================


def test_associate_competency_criterion_creates_the_hierarchy_and_criterion_when_nothing_exists(
    tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile
) -> None:
    """The happy path: no group_id, no existing groups, no rule fields supplied."""
    object_id = usage_key(course_run, "p1")

    criterion = associate_competency_criterion(tag_id=tag.id, object_id=object_id)

    assert criterion.group.tag_id == tag.id
    assert criterion.group.parent is not None
    assert criterion.group.parent.course_id == course_run.id
    root = criterion.group.parent.parent
    assert root is not None
    assert root.parent is None
    assert root.tag_id == tag.id
    assert criterion.object_tag.object_id == object_id
    assert criterion.object_tag.tag_id == tag.id
    assert criterion.rule_profile_id == default_rule_profile.id
    assert criterion.rule_type_override is None
    assert criterion.rule_payload_override is None


def test_associate_competency_criterion_uses_a_supplied_group_id(tag: Tag, course_run: CourseRun) -> None:
    """A caller-supplied group_id is used as-is, rather than deriving or creating a new leaf."""
    leaf = _create_leaf_group(tag, course_run)
    object_id = usage_key(course_run, "p1")

    criterion = associate_competency_criterion(tag_id=tag.id, object_id=object_id, group_id=leaf.id)

    assert criterion.group_id == leaf.id


def test_associate_competency_criterion_rejects_group_id_and_logic_operator_together(
    tag: Tag, course_run: CourseRun
) -> None:
    """Supplying both group_id and logic_operator is rejected before anything is created."""
    leaf = _create_leaf_group(tag, course_run)
    object_id = usage_key(course_run, "p1")

    with pytest.raises(ValidationError, match="logic_operator"):
        associate_competency_criterion(
            tag_id=tag.id, object_id=object_id, group_id=leaf.id, logic_operator=LogicOperator.AND,
        )


def test_associate_competency_criterion_rejects_a_duplicate_tag_object_association(
    tag: Tag, course_run: CourseRun
) -> None:
    """Via the derive-or-create path (no group_id), a second criterion for the same pair is rejected."""
    object_id = usage_key(course_run, "p1")
    associate_competency_criterion(tag_id=tag.id, object_id=object_id)

    with pytest.raises(ValidationError, match="object_id"):
        associate_competency_criterion(tag_id=tag.id, object_id=object_id)


def test_associate_competency_criterion_rejects_re_targeting_the_same_group(
    tag: Tag, course_run: CourseRun
) -> None:
    """Re-supplying the exact group a (tag_id, object_id) pair is already associated with is rejected."""
    object_id = usage_key(course_run, "p1")
    first = associate_competency_criterion(tag_id=tag.id, object_id=object_id)

    with pytest.raises(ValidationError, match="group_id"):
        associate_competency_criterion(tag_id=tag.id, object_id=object_id, group_id=first.group_id)


def test_associate_competency_criterion_allows_a_different_explicit_group_for_the_same_pair(
    tag: Tag, course_run: CourseRun
) -> None:
    """
    A different, explicitly supplied existing group creates a second, deliberate association --
    not a duplicate. ADR-0002's own worked example requires the same tag/object association to
    be able to participate in more than one CompetencyCriteriaGroup.
    """
    object_id = usage_key(course_run, "p1")
    first = associate_competency_criterion(tag_id=tag.id, object_id=object_id)
    other_leaf = _create_leaf_group(tag, course_run)

    second = associate_competency_criterion(tag_id=tag.id, object_id=object_id, group_id=other_leaf.id)

    assert second.object_tag_id == first.object_tag_id
    assert second.group_id == other_leaf.id
    assert CompetencyCriterion.objects.filter(object_tag_id=first.object_tag_id).count() == 2


def test_associate_competency_criterion_raises_does_not_exist_for_an_unknown_tag_id(course_run: CourseRun) -> None:
    """An unresolvable tag_id raises Tag.DoesNotExist before anything else is validated."""
    object_id = usage_key(course_run, "p1")
    with pytest.raises(Tag.DoesNotExist):
        associate_competency_criterion(tag_id=999999, object_id=object_id)


def test_associate_competency_criterion_rejects_a_malformed_object_id(tag: Tag) -> None:
    """An object_id that isn't a parseable usage key is a 400, keyed by object_id."""
    with pytest.raises(ValidationError, match="object_id"):
        associate_competency_criterion(tag_id=tag.id, object_id="not-a-usage-key")


def test_associate_competency_criterion_rejects_an_unresolvable_course(tag: Tag) -> None:
    """A well-formed usage key whose course has no matching CourseRun is a 400, keyed by object_id."""
    object_id = "block-v1:NoOrg+NoCourse+NoRun+type@sequential+block@p1"
    with pytest.raises(ValidationError, match="object_id"):
        associate_competency_criterion(tag_id=tag.id, object_id=object_id)


def test_associate_competency_criterion_rolls_back_newly_created_groups_when_criterion_creation_fails(
    monkeypatch: pytest.MonkeyPatch, tag: Tag, course_run: CourseRun
) -> None:
    """
    A downstream failure during criterion creation rolls back any group this call created.

    ADR-0002 forbids persisting empty groups, so a failed attempt via the derive-or-create path
    must not leave a root, course-level, or leaf group behind. Simulated here by making the final
    CompetencyCriterion.objects.create() call itself fail -- standing in for any failure at that
    point, including the containment check #666 will add right before it.
    """
    def _reject(**_kwargs) -> None:
        raise ValidationError({"object_id": "rejected for this test"})

    monkeypatch.setattr(cbe_api.CompetencyCriterion.objects, "create", _reject)
    object_id = usage_key(course_run, "p1")

    with pytest.raises(ValidationError):
        associate_competency_criterion(tag_id=tag.id, object_id=object_id)

    assert not CompetencyCriteriaGroup.objects.filter(tag=tag).exists()


# ==============================================================================================
# bulk_update_competency_criteria (#759)
# ==============================================================================================

# Migration 0005's seeded system-default rule, written as the override values a caller would submit.
SEEDED_RULE: dict[str, Any] = {"rule_type_override": RuleType.GRADE, "rule_payload_override": GRADE_PAYLOAD}
# The threshold most tests apply, distinct from the seeded default so it stays an override.
NEW_PAYLOAD = {"op": "gte", "value": 0.75, "scale": "percent"}
NEW_RULE: dict[str, Any] = {"rule_type_override": RuleType.GRADE, "rule_payload_override": NEW_PAYLOAD}
# A starting override that neither of the above matches.
OLD_PAYLOAD = {"op": "lte", "value": 0.5, "scale": "percent"}

CHANGE_OBJECTTAG_OBJECTID = "oel_tagging.change_objecttag_objectid"


@pytest.fixture(name="checked_object_ids", autouse=True)
def _checked_object_ids() -> Iterator[list[str]]:
    """
    Let any user tag any object, recording each object_id checked; restore the real rule afterwards.

    openedx_tagging denies change_objecttag_objectid to everyone and leaves the real Studio-role
    check to openedx-platform, so these tests substitute a permissive one.
    """
    original = rule_permissions[CHANGE_OBJECTTAG_OBJECTID]
    checked: list[str] = []

    def _predicate(_user: UserType, object_id: str) -> bool:
        checked.append(object_id)
        return True

    rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, _predicate)
    yield checked
    rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, original)


@pytest.fixture(name="leaf")
def _leaf(tag: Tag, course_run: CourseRun) -> CompetencyCriteriaGroup:
    """A leaf group under `tag` and `course_run`. Only a leaf group holds criteria."""
    return _create_leaf_group(tag, course_run)


def make_criterion(leaf: CompetencyCriteriaGroup, block_id: str, **rule) -> CompetencyCriterion:
    """Create a criterion on `leaf` for a fresh object in its course, with `rule` as its rule columns."""
    assert leaf.parent is not None and leaf.parent.course is not None
    object_tag = ObjectTag.objects.create(
        object_id=usage_key(leaf.parent.course, block_id), taxonomy=leaf.tag.taxonomy, tag=leaf.tag,
    )
    return CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, **rule)


@pytest.fixture(name="batch")
def _batch(
    leaf: CompetencyCriteriaGroup, default_rule_profile: CompetencyRuleProfile
) -> tuple[CompetencyCriterion, CompetencyCriterion]:
    """Two criteria on `leaf` with different rule sources: one follows the default, one has an override."""
    return (
        make_criterion(leaf, "p1", rule_profile=default_rule_profile),
        make_criterion(leaf, "p2", rule_type_override=RuleType.GRADE, rule_payload_override=OLD_PAYLOAD),
    )


def rule_state(criterion: CompetencyCriterion) -> tuple:
    """Return `criterion`'s three rule columns as stored."""
    criterion.refresh_from_db()
    return (criterion.rule_profile_id, criterion.rule_type_override, criterion.rule_payload_override)


def history_of(criterion: CompetencyCriterion):
    """
    Return `criterion`'s history rows, newest first.

    Looked up through the app registry, as the model tests do, because simple_history's `.history`
    descriptor has no type stubs.
    """
    historical_criterion = apps.get_model("openedx_learning", "HistoricalCompetencyCriterion")
    return historical_criterion.objects.filter(id=criterion.pk).order_by("-history_date", "-history_id")


def snapshot(criteria) -> dict[int, tuple]:
    """Record each criterion's stored rule and history length, to prove a refused call changed nothing."""
    return {criterion.id: (rule_state(criterion), history_of(criterion).count()) for criterion in criteria}


def test_bulk_update_gives_every_criterion_the_rule_values_whatever_its_starting_source(
    batch: tuple[CompetencyCriterion, CompetencyCriterion], leaf: CompetencyCriteriaGroup, user: UserType,
) -> None:
    """Rule values replace a profile link and an existing override alike, and the result keeps request order."""
    on_profile, on_override = batch

    result = bulk_update_competency_criteria([on_override.id, on_profile.id], leaf.id, user=user, **NEW_RULE)

    assert [criterion.id for criterion in result] == [on_override.id, on_profile.id]
    for criterion in batch:
        assert rule_state(criterion) == (None, RuleType.GRADE, NEW_PAYLOAD)


@pytest.mark.parametrize("named", ["system default", "taxonomy-scoped"])
def test_bulk_update_assigns_a_named_profile_clearing_values(  # pylint: disable=too-many-positional-arguments
    named: str,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """Naming a profile clears any override values, and the history row says why it changed."""
    if named == "system default":
        profile = default_rule_profile
    else:
        profile = CompetencyRuleProfile.objects.create(
            rule_type=RuleType.GRADE, rule_payload=NEW_PAYLOAD, competency_taxonomy=competency_taxonomy,
        )
    on_override = batch[1]

    bulk_update_competency_criteria([c.id for c in batch], leaf.id, rule_profile_id=profile.id, user=user)

    for criterion in batch:
        assert rule_state(criterion) == (profile.id, None, None)
    assert history_of(on_override).first().history_change_reason == "Reassigned to rule profile"


def test_the_profile_applicable_to_a_criterion_is_the_system_default(
    batch: tuple[CompetencyCriterion, CompetencyCriterion], default_rule_profile: CompetencyRuleProfile,
) -> None:
    """In this phase every criterion resolves to the system default, the only profile there is."""
    system_default = cbe_api._get_system_default_rule_profile()  # pylint: disable=protected-access
    resolve = cbe_api._resolve_applicable_rule_profile  # pylint: disable=protected-access
    assert system_default == default_rule_profile
    for criterion in batch:
        assert resolve(criterion, system_default) == default_rule_profile


def test_bulk_update_looks_up_the_applicable_profile_once_per_batch(
    leaf: CompetencyCriteriaGroup, user: UserType, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """However many criteria a batch names, comparing override values against the default reads it once."""
    criteria = [make_criterion(leaf, f"p{n}", rule_profile=default_rule_profile) for n in range(5)]
    profile_table = CompetencyRuleProfile._meta.db_table

    with CaptureQueriesContext(connection) as queries:
        bulk_update_competency_criteria([c.id for c in criteria], leaf.id, user=user, **NEW_RULE)

    profile_reads = [q for q in queries.captured_queries if q["sql"].startswith("SELECT") and profile_table in q["sql"]]
    assert len(profile_reads) == 1


def test_bulk_update_values_that_match_the_default_return_the_criterion_to_it(
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Values equal to the applicable profile's rule store the profile link, not a redundant override.

    The payload's keys arrive in a different order from the seeded row's, to show the comparison is
    on the parsed rule and never on JSON text.
    """
    reordered_payload = {"scale": "percent", "value": 0.8, "op": "gte"}

    bulk_update_competency_criteria(
        [c.id for c in batch], leaf.id, rule_type_override=RuleType.GRADE, rule_payload_override=reordered_payload,
        user=user,
    )

    for criterion in batch:
        assert rule_state(criterion) == (default_rule_profile.id, None, None)


def test_bulk_update_decides_a_return_to_a_profile_per_criterion(  # pylint: disable=too-many-positional-arguments
    monkeypatch: pytest.MonkeyPatch,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """
    Two criteria given the same values can end in different states, each by its own applicable profile.

    Only the system default exists in this phase, so the per-criterion lookup is stubbed to give the
    first criterion a profile whose rule the submitted values match.
    """
    first, second = batch
    matching = CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE, rule_payload=NEW_PAYLOAD, competency_taxonomy=competency_taxonomy,
    )
    applicable = {first.id: matching, second.id: default_rule_profile}
    monkeypatch.setattr(
        cbe_api, "_resolve_applicable_rule_profile", lambda criterion, system_default: applicable[criterion.id],
    )

    bulk_update_competency_criteria([first.id, second.id], leaf.id, user=user, **NEW_RULE)

    assert rule_state(first) == (matching.id, None, None)
    assert rule_state(second) == (None, RuleType.GRADE, NEW_PAYLOAD)


def test_bulk_update_writes_history_only_for_criteria_it_changes(
    leaf: CompetencyCriteriaGroup, user: UserType, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A criterion already carrying the rule gets no new history row; one that changes gets exactly one."""
    unchanged = make_criterion(leaf, "p1", **NEW_RULE)
    changed = make_criterion(leaf, "p2", rule_profile=default_rule_profile)
    unchanged_before, changed_before = history_of(unchanged).count(), history_of(changed).count()

    result = bulk_update_competency_criteria([unchanged.id, changed.id], leaf.id, user=user, **NEW_RULE)

    assert [criterion.id for criterion in result] == [unchanged.id, changed.id]
    assert history_of(unchanged).count() == unchanged_before
    assert history_of(changed).count() == changed_before + 1
    latest = history_of(changed).first()
    assert latest.history_user == user
    assert latest.history_change_reason == "Rule values set"
    assert (latest.rule_profile_id, latest.rule_type_override, latest.rule_payload_override) == (
        None, RuleType.GRADE, NEW_PAYLOAD,
    )


@pytest.mark.parametrize(
    "case",
    [
        "rule values",
        "named profile",
        "values matching the default",
        "nothing to change",
        "malformed request",
        "id outside the group",
        "archived criterion",
        "no permission",
    ],
)
def test_bulk_update_never_reads_or_writes_learner_status(
    case: str,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    No path, accepted or refused, sends a query to a learner status table.

    Whether a learner has a recorded status plays no part in accepting a batch: #723 owns that check,
    in front of this endpoint. A create, update, delete or read would each be a query, so checking
    the SQL covers all four.
    """
    first, second = batch
    ids = [first.id, second.id]
    unchanged = make_criterion(leaf, "p3", **NEW_RULE)
    archived = make_criterion(leaf, "p4", rule_profile=default_rule_profile, archived=True)
    requests: dict[str, tuple[list[int], dict[str, Any], type[Exception] | None]] = {
        "rule values": (ids, NEW_RULE, None),
        "named profile": (ids, {"rule_profile_id": default_rule_profile.id}, None),
        "values matching the default": (ids, SEEDED_RULE, None),
        "nothing to change": ([unchanged.id], NEW_RULE, None),
        "malformed request": ([first.id, first.id], NEW_RULE, ValidationError),
        "id outside the group": ([first.id, 999999], NEW_RULE, CompetencyCriterion.DoesNotExist),
        "archived criterion": ([first.id, archived.id], NEW_RULE, CompetencyCriterionArchivedError),
        "no permission": (ids, NEW_RULE, PermissionDenied),
    }
    criterion_ids, rule, error = requests[case]
    if case == "no permission":
        rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, lambda _user, _object_id: False)
    status_tables = [
        model._meta.db_table
        for model in (StudentCompetencyStatus, StudentCompetencyCriteriaGroupStatus, StudentCompetencyCriterionStatus)
    ]

    with CaptureQueriesContext(connection) as queries:
        if error is None:
            bulk_update_competency_criteria(criterion_ids, leaf.id, user=user, **rule)
        else:
            with pytest.raises(error):
                bulk_update_competency_criteria(criterion_ids, leaf.id, user=user, **rule)

    assert queries.captured_queries
    assert not [q["sql"] for q in queries.captured_queries if any(table in q["sql"] for table in status_tables)]


@pytest.mark.parametrize(
    "case",
    [
        "empty id list",
        "duplicate id",
        "both rule forms",
        "neither rule form",
        "rule type without payload",
        "payload without rule type",
        "unknown profile",
        "archived profile",
        "unsupported comparison",
        "threshold above 1.0",
        "threshold below 0.0",
        "unimplemented rule type",
    ],
)
def test_bulk_update_rejects_a_malformed_request_changing_nothing(  # pylint: disable=too-many-positional-arguments
    case: str,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """Each malformed request is refused with an error naming what is wrong, and no criterion changes."""
    first, second = batch
    ids = [first.id, second.id]
    archived = CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE, rule_payload=NEW_PAYLOAD, competency_taxonomy=competency_taxonomy, archived=True,
    )
    requests: dict[str, tuple[list[int], dict[str, Any], str]] = {
        "empty id list": ([], NEW_RULE, "criterion_ids"),
        "duplicate id": ([first.id, first.id, second.id], NEW_RULE, "criterion_ids"),
        "both rule forms": (
            ids, {"rule_profile_id": default_rule_profile.id, **NEW_RULE}, "rule_profile_id",
        ),
        "neither rule form": (ids, {}, NON_FIELD_ERRORS),
        "rule type without payload": (ids, {"rule_type_override": RuleType.GRADE}, "rule_type_override"),
        "payload without rule type": (ids, {"rule_payload_override": NEW_PAYLOAD}, "rule_payload_override"),
        "unknown profile": (ids, {"rule_profile_id": 999999}, "rule_profile_id"),
        "archived profile": (ids, {"rule_profile_id": archived.id}, "rule_profile_id"),
        "unsupported comparison": (
            ids, {**NEW_RULE, "rule_payload_override": {**NEW_PAYLOAD, "op": "gt"}}, NON_FIELD_ERRORS,
        ),
        "threshold above 1.0": (
            ids, {**NEW_RULE, "rule_payload_override": {**NEW_PAYLOAD, "value": 1.5}}, NON_FIELD_ERRORS,
        ),
        "threshold below 0.0": (
            ids, {**NEW_RULE, "rule_payload_override": {**NEW_PAYLOAD, "value": -0.1}}, NON_FIELD_ERRORS,
        ),
        "unimplemented rule type": (ids, {**NEW_RULE, "rule_type_override": "Completion"}, "rule_type_override"),
    }
    criterion_ids, rule, error_key = requests[case]
    before = snapshot(batch)

    with pytest.raises(ValidationError) as exc_info:
        bulk_update_competency_criteria(criterion_ids, leaf.id, user=user, **rule)

    assert error_key in exc_info.value.message_dict
    assert snapshot(batch) == before


def test_bulk_update_names_both_rule_forms_as_not_combinable(
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """The refusal of a profile plus values says the two cannot be combined, not merely that the request failed."""
    with pytest.raises(ValidationError, match="cannot be combined"):
        bulk_update_competency_criteria(
            [c.id for c in batch], leaf.id, rule_profile_id=default_rule_profile.id, user=user, **NEW_RULE,
        )


def test_bulk_update_names_half_a_rule_as_needing_its_pair(
    batch: tuple[CompetencyCriterion, CompetencyCriterion], leaf: CompetencyCriteriaGroup, user: UserType,
) -> None:
    """A rule type without its payload is refused with a message saying the two must be supplied together."""
    with pytest.raises(ValidationError, match="must be supplied together"):
        bulk_update_competency_criteria([c.id for c in batch], leaf.id, rule_type_override=RuleType.GRADE, user=user)


@pytest.mark.parametrize("stray", ["from another group", "nonexistent"])
def test_bulk_update_raises_does_not_exist_for_ids_outside_the_group(  # pylint: disable=too-many-positional-arguments
    stray: str,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
    tag: Tag,
    course_run: CourseRun,
) -> None:
    """One id outside the group raises CompetencyCriterion.DoesNotExist naming it, and nothing changes."""
    if stray == "from another group":
        stray_id = make_criterion(_create_leaf_group(tag, course_run), "p3", **NEW_RULE).id
    else:
        stray_id = 999999
    before = snapshot(batch)

    with pytest.raises(CompetencyCriterion.DoesNotExist, match=str(stray_id)):
        bulk_update_competency_criteria([batch[0].id, stray_id, batch[1].id], leaf.id, user=user, **SEEDED_RULE)

    assert snapshot(batch) == before


def test_bulk_update_raises_does_not_exist_for_an_unknown_group(
    batch: tuple[CompetencyCriterion, CompetencyCriterion], user: UserType,
) -> None:
    """An unknown group id raises CompetencyCriteriaGroup.DoesNotExist."""
    with pytest.raises(CompetencyCriteriaGroup.DoesNotExist):
        bulk_update_competency_criteria([c.id for c in batch], 999999, user=user, **NEW_RULE)


def test_bulk_update_refuses_the_whole_batch_if_any_criterion_is_archived(
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """One archived criterion refuses the whole batch, and nothing changes, not even the valid criteria."""
    archived = make_criterion(leaf, "p3", rule_profile=default_rule_profile, archived=True)
    criteria = [batch[0], archived, batch[1]]
    before = snapshot(criteria)

    with pytest.raises(CompetencyCriterionArchivedError, match=str(archived.id)):
        bulk_update_competency_criteria([c.id for c in criteria], leaf.id, user=user, **NEW_RULE)

    assert snapshot(criteria) == before


@pytest.mark.parametrize("level", ["root", "course-level"])
def test_bulk_update_rejects_a_group_that_is_not_a_leaf(
    level: str, leaf: CompetencyCriteriaGroup, user: UserType,
) -> None:
    """Only a leaf group holds criteria, so the root and course-level groups are refused."""
    course_level = leaf.parent
    assert course_level is not None and course_level.parent is not None
    group = course_level.parent if level == "root" else course_level

    with pytest.raises(ValidationError, match="group_id"):
        bulk_update_competency_criteria([1], group.id, user=user, **NEW_RULE)


def test_bulk_update_checks_permission_against_the_leafs_course(
    checked_object_ids: list[str],
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
    course_run: CourseRun,
) -> None:
    """The object-level check asks about the leaf's course, resolved through its course-level parent."""
    bulk_update_competency_criteria([c.id for c in batch], leaf.id, user=user, **NEW_RULE)

    assert checked_object_ids == [str(course_run.course_key)]


@pytest.mark.parametrize("lacks", ["course write access", "taxonomy view access"])
def test_bulk_update_refuses_a_user_without_permission_changing_nothing(
    lacks: str,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """A user failing oel_tagging.can_tag_object's taxonomy check or object_id check is refused, and nothing changes."""
    if lacks == "course write access":
        rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, lambda _user, _object_id: False)
    else:
        # can_tag_object's taxonomy check fails on a disabled taxonomy for anyone but a superuser.
        competency_taxonomy.enabled = False
        competency_taxonomy.save()
    before = snapshot(batch)

    with pytest.raises(PermissionDenied):
        bulk_update_competency_criteria([c.id for c in batch], leaf.id, user=user, **NEW_RULE)

    assert snapshot(batch) == before


def test_bulk_update_rolls_back_every_criterion_if_one_save_fails(
    monkeypatch: pytest.MonkeyPatch,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    user: UserType,
) -> None:
    """A failure partway through the batch undoes the saves already made, history included."""
    before = snapshot(batch)
    original_save = CompetencyCriterion.save
    saves: list[int] = []

    def _fail_on_second_save(self, *args, **kwargs) -> None:
        saves.append(self.pk)
        if len(saves) == 2:
            raise IntegrityError("simulated failure")
        original_save(self, *args, **kwargs)

    monkeypatch.setattr(CompetencyCriterion, "save", _fail_on_second_save)

    with pytest.raises(IntegrityError):
        bulk_update_competency_criteria([c.id for c in batch], leaf.id, user=user, **NEW_RULE)

    monkeypatch.undo()
    assert len(saves) == 2
    assert snapshot(batch) == before
