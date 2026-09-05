"""
Shared fixtures for the CBE criteria test modules (schema, deletion, and tree-integration tests).

Every fixture here used to be duplicated verbatim across test_criteria_models.py and
test_criteria_deletion.py. Consolidated here so both files, plus test_criteria_trees.py, share one
definition.
"""
import pytest
from organizations.api import ensure_organization
from organizations.models import Organization

from openedx_catalog.models import CatalogCourse, CourseRun
from openedx_learning.models import CompetencyCriteriaGroup, CompetencyRuleProfile, CompetencyTaxonomy
from openedx_tagging.models import ObjectTag, Tag


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
