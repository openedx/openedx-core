"""Delete-behavior tests for CompetencyCriterion's own foreign keys, and the transitive and
scope-owner cases that only exist once this model completes the criteria tree."""
import pytest
from django.apps import apps
from django.db.models import RestrictedError

from openedx_catalog.models import CourseRun
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    RuleType,
)
from openedx_tagging.models import ObjectTag, Tag

pytestmark = pytest.mark.django_db

_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}


# ---------------------------------------------------------------------------------------------
# CompetencyCriterion's three foreign keys


# ---------------------------------------------------------------------------------------------


def test_deleting_a_group_also_deletes_its_criteria(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyCriteriaGroup cascades to any CompetencyCriterion referencing it via
    `group`: the delete succeeds and the criterion row is gone too.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    group.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_a_cascaded_criterion_removal_is_recorded_in_history(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    A criterion removed by a cascade, rather than by a direct delete, still gets its own
    historical row with history_type '-'. An author or auditor reviewing history for a criterion
    that vanished this way still finds why it did.
    """
    historical_criterion = apps.get_model("openedx_learning", "HistoricalCompetencyCriterion")
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    criterion_pk = criterion.pk

    group.delete()

    assert historical_criterion.objects.filter(id=criterion_pk, history_type="-").exists()


def test_deleting_an_object_tag_also_deletes_its_criteria(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an ObjectTag cascades to any CompetencyCriterion referencing it via `object_tag`: the
    delete succeeds and the criterion row is gone too.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    object_tag.delete()

    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_deleting_a_rule_profile_referenced_by_a_criterion_raises_restricted_error(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyRuleProfile that a CompetencyCriterion references via `rule_profile`
    raises RestrictedError. Nothing cascades from a profile down to a criterion, so the criterion
    is not part of this delete and RESTRICT refuses, exactly as PROTECT would have.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )

    with pytest.raises(RestrictedError) as exc_info:
        default_rule_profile.delete()

    restricted = exc_info.value.restricted_objects
    assert any(isinstance(obj, CompetencyCriterion) and obj.pk == criterion.pk for obj in restricted)


def test_object_tag_delete_leaves_a_childless_criteria_group_behind(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an ObjectTag cascades away the CompetencyCriterion that references it, but leaves the
    CompetencyCriteriaGroup that housed that criterion in place, even when the group now has no
    children of any kind.

    This is deliberate, not a bug: CompetencyCriteriaGroup has no foreign key to ObjectTag, so
    nothing about this delete gives Django's collector a reason to reach the group. Cleaning up a
    now-childless group is authoring-API/application-layer work, not something an on_delete value
    can express here.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    object_tag.delete()

    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriteriaGroup.objects.get(pk=group.pk).criteria.exists()


# ---------------------------------------------------------------------------------------------
# Deleting a Tag, a group at depth, or a Taxonomy takes the whole referencing criteria tree
# with it. Tag.taxonomy is already CASCADE in openedx_tagging, which is what makes the tag
# case hold transitively from a taxonomy.


# ---------------------------------------------------------------------------------------------


def test_tag_delete_with_no_status_cascades_whole_criteria_tree(
    tag: Tag, group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an oel_tagging.Tag with no learner status beneath it succeeds and cascades away
    every CompetencyCriteriaGroup and CompetencyCriterion that references it, transitively:
    Tag -> CompetencyCriteriaGroup.tag (CASCADE) -> CompetencyCriterion.group (CASCADE).
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    tag.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_taxonomy_delete_cascades_every_tag_and_its_criteria(
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Deleting an oel_tagging.Taxonomy collects every Tag beneath it (Tag.taxonomy is CASCADE), so
    this taxonomy delete succeeds and cascades away the group and criterion beneath its tag too,
    the same as a direct tag delete.

    Chain exercised: CompetencyTaxonomy -> Tag (CASCADE) -> CompetencyCriteriaGroup.tag (CASCADE)
    -> CompetencyCriterion.group (CASCADE).
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert Tag.objects.filter(pk=tag.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    competency_taxonomy.delete()

    assert not Tag.objects.filter(pk=tag.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


# ---------------------------------------------------------------------------------------------
# Scope-owner deletes that reach a profile a criterion is assigned to
# These are what RESTRICT on CompetencyCriterion.rule_profile buys, and what it still refuses.
# Both are unreachable until a taxonomy- or course-scoped profile can be authored, which no code
# path does yet. See ADR-0002 Decision 7.


# ---------------------------------------------------------------------------------------------


def test_taxonomy_delete_reaching_its_scoped_profile_through_a_criterion_succeeds(
    competency_taxonomy: CompetencyTaxonomy, group: CompetencyCriteriaGroup, object_tag: ObjectTag
) -> None:
    """
    Deleting a CompetencyTaxonomy whose taxonomy-scoped profile is itself assigned to a criterion
    succeeds, and takes the profile and the criterion with it.

    This is the case RESTRICT exists for. The delete reaches the profile through
    `competency_taxonomy` (CASCADE) and reaches the criterion through the tag chain
    (Tag -> CompetencyCriteriaGroup.tag -> CompetencyCriterion.group, all CASCADE). RESTRICT then
    finds nothing left restricting the profile, because the only row referencing it is one this
    same operation is already deleting. Under PROTECT this raised ProtectedError naming that
    criterion, which was a spurious failure: an author deleting a taxonomy was told a criterion
    was in the way, when nothing about that criterion survived the delete either.
    """
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    criterion = CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=profile)

    competency_taxonomy.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


def test_course_run_delete_is_refused_by_a_criterion_outside_its_scope(
    course_run: CourseRun, group: CompetencyCriteriaGroup, object_tag: ObjectTag
) -> None:
    """
    Deleting a CourseRun whose course-scoped profile is assigned to a criterion that the same
    delete does NOT reach raises RestrictedError, and nothing is removed.

    A criterion's profile assignment is independent of its tree's `course` scope (ADR-0002
    Decision 4), so a criterion in a tree with `course=None`, which `group` is, can still be
    assigned a course-scoped profile. Deleting that run collects the profile but not the
    criterion, so RESTRICT correctly refuses: unlike the taxonomy case above, this criterion
    really would have been left pointing at a deleted profile. ADR-0002 Decision 7 records this
    as the residual case, whose fix is a fifth reassignment event on Decision 4.
    """
    profile = CompetencyRuleProfile.objects.create(
        course=course_run, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    criterion = CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=profile)
    assert group.course is None

    with pytest.raises(RestrictedError) as exc_info:
        course_run.delete()

    restricted = exc_info.value.restricted_objects
    assert any(isinstance(obj, CompetencyCriterion) and obj.pk == criterion.pk for obj in restricted)
    # Nothing was removed: the whole operation raised before any DELETE executed.
    assert CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert CourseRun.objects.filter(pk=course_run.pk).exists()
