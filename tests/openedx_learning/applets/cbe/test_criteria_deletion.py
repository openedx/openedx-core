"""
Delete-behavior tests for CompetencyCriteriaGroup, CompetencyRuleProfile, and CompetencyCriterion.

Four of these nine foreign keys are `on_delete=models.CASCADE` and five are `models.PROTECT`; see
the module docstring in `openedx_learning.applets.cbe.models.criteria` for which is which and why.
"""
import pytest
from django.apps import apps
from django.db.models import ProtectedError
from organizations.api import ensure_organization
from organizations.models import Organization

from openedx_catalog.models import CatalogCourse, CourseRun
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


@pytest.fixture(name="organization")
def _organization() -> Organization:
    """An Organization for use as a scope in these tests."""
    ensure_organization("Org1")
    return Organization.objects.get(short_name="Org1")


@pytest.fixture(name="organization2")
def _organization2() -> Organization:
    """A second Organization, distinct from `organization`, for use as a scope in these tests."""
    ensure_organization("Org2")
    return Organization.objects.get(short_name="Org2")


@pytest.fixture(name="course_run")
def _course_run(organization: Organization) -> CourseRun:
    """A CourseRun for use as a scope in these tests."""
    catalog_course = CatalogCourse.objects.create(org=organization, course_code="Python100")
    return CourseRun.objects.create(catalog_course=catalog_course, run_code="Fall2026")


@pytest.fixture(name="competency_taxonomy")
def _competency_taxonomy() -> CompetencyTaxonomy:
    """A CompetencyTaxonomy for use as a scope, and as the home taxonomy for `tag`."""
    return CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")


@pytest.fixture(name="tag")
def _tag(competency_taxonomy: CompetencyTaxonomy) -> Tag:
    """A Tag, from `competency_taxonomy`, for use as the competency a criteria tree evaluates."""
    return Tag.objects.create(taxonomy=competency_taxonomy, value="Writing Poetry")


@pytest.fixture(name="object_tag")
def _object_tag(competency_taxonomy: CompetencyTaxonomy, tag: Tag) -> ObjectTag:
    """An ObjectTag associating `tag` with a made-up content object, for use as a criterion's target."""
    return ObjectTag.objects.create(
        object_id="block-v1:Org1+Python100+Fall2026+problem+p1",
        taxonomy=competency_taxonomy,
        tag=tag,
    )


@pytest.fixture(name="group")
def _group(tag: Tag) -> CompetencyCriteriaGroup:
    """A root CompetencyCriteriaGroup for `tag`, for use as a criterion's parent group."""
    return CompetencyCriteriaGroup.objects.create(tag=tag)


@pytest.fixture(name="default_rule_profile")
def _default_rule_profile() -> CompetencyRuleProfile:
    """The system-default CompetencyRuleProfile seeded by migration 0003."""
    return CompetencyRuleProfile.objects.get(
        organization__isnull=True,
        course__isnull=True,
        competency_taxonomy__isnull=True,
    )


# ==============================================================================================
# One test per foreign key. The five that stayed PROTECT assert ProtectedError and inspect
# `protected_objects` to confirm which relationship actually fired: several protected
# relationships can fire on one delete (see test_rule_profile_organization_protect below for a
# real trap of that kind, where CatalogCourse.org is also PROTECT), so a bare
# `pytest.raises(ProtectedError)` would not actually prove which foreign key did the protecting.
# The four that became CASCADE assert the delete succeeds and that the referencing row is
# actually gone from the database afterward, not merely that no exception was raised, and assert
# the referencing row existed beforehand, so the "gone" assertion can't pass because a fixture
# never created it in the first place.
# ==============================================================================================


def test_group_parent_cascade(tag: Tag) -> None:
    """
    Deleting a CompetencyCriteriaGroup cascades to any child group referencing it via `parent`:
    the delete succeeds and the child row is gone too.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    assert CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()

    root.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()


def test_group_tag_cascade(tag: Tag, group: CompetencyCriteriaGroup) -> None:
    """
    Deleting a Tag cascades to any CompetencyCriteriaGroup referencing it via `tag`: the delete
    succeeds and the group row is gone. Also confirms django-simple-history records the cascaded
    removal as its own historical row (history_type='-'), not silently: an author or auditor
    reviewing history for a group that vanished this way still finds why it did.
    """
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    group_pk = group.pk

    tag.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group_pk).exists()

    historical_group = apps.get_model("openedx_learning", "HistoricalCompetencyCriteriaGroup")
    assert historical_group.objects.filter(id=group_pk, history_type="-").exists()


def test_group_course_protect(tag: Tag, course_run: CourseRun) -> None:
    """Deleting a CourseRun that a CompetencyCriteriaGroup references via `course` raises ProtectedError."""
    group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run)

    with pytest.raises(ProtectedError) as exc_info:
        course_run.delete()

    protected = exc_info.value.protected_objects
    assert any(isinstance(obj, CompetencyCriteriaGroup) and obj.pk == group.pk for obj in protected)


def test_rule_profile_organization_protect(organization2: Organization) -> None:
    """
    Deleting an Organization that a CompetencyRuleProfile references via `organization` raises
    ProtectedError naming the profile.

    Uses `organization2`, which this test never attaches a CatalogCourse to, instead of
    `organization` (the one `course_run` uses elsewhere in this module): CatalogCourse.org is
    itself PROTECT, so deleting an organization with a CatalogCourse attached raises
    ProtectedError regardless of whether a CompetencyRuleProfile references it too, and this
    test would pass for the wrong reason.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization2, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    with pytest.raises(ProtectedError) as exc_info:
        organization2.delete()

    protected = exc_info.value.protected_objects
    assert any(isinstance(obj, CompetencyRuleProfile) and obj.pk == profile.pk for obj in protected)


def test_rule_profile_course_protect(course_run: CourseRun) -> None:
    """Deleting a CourseRun that a CompetencyRuleProfile references via `course` raises ProtectedError."""
    profile = CompetencyRuleProfile.objects.create(
        course=course_run, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    with pytest.raises(ProtectedError) as exc_info:
        course_run.delete()

    protected = exc_info.value.protected_objects
    assert any(isinstance(obj, CompetencyRuleProfile) and obj.pk == profile.pk for obj in protected)


def test_rule_profile_competency_taxonomy_protect(competency_taxonomy: CompetencyTaxonomy) -> None:
    """
    Deleting a CompetencyTaxonomy that a CompetencyRuleProfile references via
    `competency_taxonomy` raises ProtectedError naming the profile.

    Deliberately does not use the `tag` or `group` fixtures: they are not needed to isolate this
    relationship. Tag.taxonomy and CompetencyCriteriaGroup.tag are both CASCADE now, so a tag and
    group under this taxonomy would just be silently left untouched by the aborted delete (the
    whole operation rolls back once any PROTECT fires) rather than competing for the raised
    error's `protected_objects`; keeping this test to only what it needs stays the clearer read.
    """
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    with pytest.raises(ProtectedError) as exc_info:
        competency_taxonomy.delete()

    protected = exc_info.value.protected_objects
    assert any(isinstance(obj, CompetencyRuleProfile) and obj.pk == profile.pk for obj in protected)


def test_criterion_group_cascade(
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


def test_criterion_object_tag_cascade(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an ObjectTag cascades to any CompetencyCriterion referencing it via `object_tag`: the
    delete succeeds and the criterion row is gone too. Doubles as the "OURS" half of #641's
    Deletions criterion for oel_tagging_objecttag, since ObjectTag has only this one hop down to
    CompetencyCriterion.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    object_tag.delete()

    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_criterion_rule_profile_protect(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyRuleProfile that a CompetencyCriterion references via `rule_profile`
    raises ProtectedError.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )

    with pytest.raises(ProtectedError) as exc_info:
        default_rule_profile.delete()

    protected = exc_info.value.protected_objects
    assert any(isinstance(obj, CompetencyCriterion) and obj.pk == criterion.pk for obj in protected)


# ==============================================================================================
# Transitive deletion tests required by #641's Deletions criteria: deleting an oel_tagging.Tag,
# a CompetencyCriteriaGroup at depth, an oel_tagging.ObjectTag, or an oel_tagging.Taxonomy, when
# no learner status exists beneath the target, must succeed and take the whole referencing
# criteria tree with it.
#
# Each of these criteria also has a "raises ProtectedError when a learner status row exists
# beneath it" half. That half is NOT covered here: it needs #642's Student*Status tables, which
# do not exist on this branch, and #642's own criterion says those tests belong in the slice that
# follows #641, once those tables exist. This file does not stub, mock, or fake a status model to
# test them; their absence here is deliberate, not an oversight.
# ==============================================================================================


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


def test_group_delete_at_depth_cascades_descendants_and_their_criteria(
    tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyCriteriaGroup that is not a root removes it, every descendant group, and
    every CompetencyCriterion under any of them, while leaving the rest of the tree (here, the
    root) alone.

    Builds a genuinely nested tree, root -> child -> grandchild, with criteria at two different
    levels (on `child` and on `grandchild`), so "at depth" and "every descendant" both mean
    something: a shallower tree could pass this by accident.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    grandchild = CompetencyCriteriaGroup.objects.create(tag=tag, parent=child)
    child_criterion = CompetencyCriterion.objects.create(
        group=child, object_tag=object_tag, rule_profile=default_rule_profile
    )
    grandchild_criterion = CompetencyCriterion.objects.create(
        group=grandchild, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=grandchild.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=child_criterion.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=grandchild_criterion.pk).exists()

    child.delete()

    assert CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=grandchild.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=child_criterion.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=grandchild_criterion.pk).exists()


def test_taxonomy_delete_cascades_every_tag_and_its_criteria(
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Deleting an oel_tagging.Taxonomy collects every Tag beneath it (Tag.taxonomy is CASCADE), so
    the tag-deletion cases above hold transitively through a taxonomy delete too. This asserts the
    succeeding case (no learner status beneath the tag), which is what #641's Deletions criterion
    for taxonomy-level deletion requires "at minimum".

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
