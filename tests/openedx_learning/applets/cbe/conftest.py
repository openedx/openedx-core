"""Shared fixtures for the CBE test modules: schema, deletion, tree-integration, and REST tests."""
import pytest
from django.contrib.auth.models import User as UserType  # pylint: disable=imported-auth-user
from organizations.api import ensure_organization
from organizations.models import Organization
from rest_framework.test import APIClient

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
    """An ObjectTag associating `tag` with a made-up content object, a criterion's target."""
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
    """The system-default CompetencyRuleProfile seeded by migration 0005."""
    return CompetencyRuleProfile.objects.get(
        organization__isnull=True,
        course__isnull=True,
        competency_taxonomy__isnull=True,
    )


@pytest.fixture(name="staff_user")
def _staff_user() -> UserType:
    """A user permitted to administer instance-wide competency configuration."""
    return UserType.objects.create(username="staff", email="staff@example.com", is_staff=True)


@pytest.fixture(name="user")
def _user() -> UserType:
    """A user who may not administer competency configuration."""
    return UserType.objects.create(username="user", email="user@example.com")


@pytest.fixture(name="api_client")
def _api_client() -> APIClient:
    """A REST client for a caller the system cannot identify."""
    return APIClient()


@pytest.fixture(name="staff_client")
def _staff_client(staff_user: UserType) -> APIClient:
    """A REST client acting as `staff_user`."""
    client = APIClient()
    client.force_authenticate(user=staff_user)
    return client
