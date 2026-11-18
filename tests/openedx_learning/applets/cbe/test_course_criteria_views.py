"""
Tests for the learner-facing course criteria endpoint, ``GET cbe/v1/courses/<course_key>/criteria/`` (#733).

Fixtures live in this directory's conftest.py. The endpoint is mounted through ``openedx_learning.learner_urls``
under the ``cbe_learner`` namespace, so the authoring URL module stays free of it.
"""
import pytest
import rules
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve, reverse
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.test import APIClient

from openedx_catalog.models import CourseRun
from openedx_learning.models import CompetencyRuleProfile, CompetencyTaxonomy
from openedx_tagging.models import Tag

from .utils import make_leaf_criterion, make_tag, usage_key

pytestmark = pytest.mark.django_db

PLAIN_KEY = "course-v1:Org1+Python100+Fall2026"
VIEW_PERMISSION = "openedx_learning.view_course_competency_criteria"
EMPTY_BODY: dict[str, list] = {"taxonomies": [], "competencies": [], "groups": [], "criteria": []}
PAGINATION_KEYS = {"count", "num_pages", "current_page", "next", "previous"}


@pytest.fixture(name="user_client")
def _user_client(api_client: APIClient, user) -> APIClient:
    """A REST client acting as `user`, an ordinary, non-staff, authenticated caller with no enrollment."""
    api_client.force_authenticate(user=user)
    return api_client


def criteria_url(course_key: str) -> str:
    """Return the course criteria endpoint's path for `course_key`, resolved through the learner URL name."""
    return reverse("cbe_learner:course-criteria", kwargs={"course_key": course_key})


def make_three_competencies(
    taxonomy: CompetencyTaxonomy, course_run: CourseRun, profile: CompetencyRuleProfile,
) -> list[Tag]:
    """Create competencies "Alpha", "Beta" and "Gamma", each with a root, a course-level group and one criterion."""
    tags = [make_tag(taxonomy, name) for name in ["Alpha", "Beta", "Gamma"]]
    for index, tag in enumerate(tags):
        make_leaf_criterion(tag, course_run, profile, f"s{index}")
    return tags


# =================================================================================================
# Authentication and permission
# =================================================================================================


@pytest.mark.parametrize("course_key", [PLAIN_KEY, "not-a-key"], ids=["valid key", "malformed key"])
def test_signed_out_caller_gets_401_and_no_data(
    course_key: str,
    api_client: APIClient,
    course_run: CourseRun,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A caller who is not signed in is refused with 401 before the key is parsed, and sees no competency data."""
    make_leaf_criterion(tag, course_run, default_rule_profile, "s1")

    response = api_client.get(criteria_url(course_key))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert set(response.json()) == {"detail"}


def test_signed_in_user_without_enrollment_or_staff_status_gets_the_full_result(
    user_client: APIClient, course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """The default permission only asks that the caller be signed in, so a plain user receives the data."""
    criterion = make_leaf_criterion(tag, course_run, default_rule_profile, "s1")

    response = user_client.get(criteria_url(PLAIN_KEY))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert [competency["name"] for competency in body["competencies"]] == ["Writing Poetry"]
    assert len(body["groups"]) == 3
    assert [(c["id"], c["usage_key"]) for c in body["criteria"]] == [(criterion.id, usage_key(course_run, "s1"))]


def test_permission_is_checked_by_name_against_the_normalized_course_key(
    user_client: APIClient, course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A deny rule registered under the contract name refuses the caller with 403 and no data, and it receives
    the course key with its branch stripped, which is the object a host's override can rely on.
    """
    make_leaf_criterion(tag, course_run, default_rule_profile, "s1")
    seen: list = []

    def _deny_and_record(_user, course_key=None) -> bool:
        seen.append(course_key)
        return False

    original = rules.permissions.permissions[VIEW_PERMISSION]
    rules.set_perm(VIEW_PERMISSION, _deny_and_record)
    try:
        response = user_client.get(criteria_url(f"{PLAIN_KEY}+branch@draft"))
    finally:
        rules.set_perm(VIEW_PERMISSION, original)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert set(response.json()) == {"detail"}
    assert len(seen) == 1
    assert isinstance(seen[0], CourseKey)
    assert seen[0] == CourseKey.from_string(PLAIN_KEY)


def test_malformed_key_gets_400_even_when_the_permission_would_deny(user_client: APIClient) -> None:
    """The key is parsed before the permission is checked, so a malformed key never reaches a deny rule."""
    seen: list = []

    def _deny_and_record(_user, course_key=None) -> bool:
        seen.append(course_key)
        return False

    original = rules.permissions.permissions[VIEW_PERMISSION]
    rules.set_perm(VIEW_PERMISSION, _deny_and_record)
    try:
        response = user_client.get(criteria_url("not-a-key"))
    finally:
        rules.set_perm(VIEW_PERMISSION, original)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not seen


# =================================================================================================
# Course key handling
# =================================================================================================


@pytest.mark.parametrize(
    "course_key",
    [
        "library-v1:Org1+Lib1",
        "lib:Org1:Lib1",
        "block-v1:Org1+Python100+Fall2026+type@sequential+block@s1",
        "not-a-key",
        "course-v1:version@0123456789abcdef01234567",
    ],
    ids=["legacy library key", "library key", "usage key", "garbage", "version-only key"],
)
def test_value_that_is_not_a_course_run_key_gets_400(user_client: APIClient, course_key: str) -> None:
    """A library key, a usage key, a version-only key or garbage gets 400 and a message naming the course run key."""
    response = user_client.get(criteria_url(course_key))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert set(body) == {"course_key"}
    assert "course run key" in body["course_key"][0]


@pytest.mark.parametrize(
    "course_key",
    [PLAIN_KEY, "Org1/Python100/Fall2026", "ccx-v1:Org1+Python100+Fall2026+ccx@3"],
    ids=["course-v1", "slash separated", "ccx"],
)
def test_every_course_key_format_is_accepted(user_client: APIClient, course_key: str) -> None:
    """A course-v1, an old slash-separated and a CCX key are valid, and one with no criteria gives empty arrays."""
    response = user_client.get(criteria_url(course_key))

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == EMPTY_BODY


@pytest.mark.parametrize(
    "suffix",
    ["+branch@draft", "+branch@draft+version@0123456789abcdef01234567"],
    ids=["branch", "branch and version"],
)
def test_branch_and_version_suffixes_give_the_result_of_the_plain_key(
    suffix: str,
    user_client: APIClient,
    course_run: CourseRun,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A key carrying a branch, or a branch and a version, resolves to the plain course run's criteria."""
    make_leaf_criterion(tag, course_run, default_rule_profile, "s1")
    plain = user_client.get(criteria_url(PLAIN_KEY))

    suffixed = user_client.get(criteria_url(f"{PLAIN_KEY}{suffix}"))

    assert suffixed.status_code == status.HTTP_200_OK
    assert len(plain.json()["criteria"]) == 1
    assert suffixed.json() == plain.json()


# =================================================================================================
# Response shape and pagination
# =================================================================================================


def test_response_has_exactly_the_four_arrays_with_exact_item_fields(
    user_client: APIClient, course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Without pagination the body holds four arrays and no wrapper, and every item carries exactly its fields."""
    make_leaf_criterion(tag, course_run, default_rule_profile, "s1")

    body = user_client.get(criteria_url(PLAIN_KEY)).json()

    assert set(body) == {"taxonomies", "competencies", "groups", "criteria"}
    assert [set(item) for item in body["taxonomies"]] == [{"id", "name"}]
    assert [set(item) for item in body["competencies"]] == [
        {"id", "name", "external_id", "lineage", "taxonomy_id"},
    ]
    assert {frozenset(item) for item in body["groups"]} == {
        frozenset({"id", "competency_id", "parent_id", "name", "logic_operator", "ordering"}),
    }
    assert [set(item) for item in body["criteria"]] == [
        {"id", "group_id", "usage_key", "rule_type", "rule_payload"},
    ]
    assert body["competencies"][0]["lineage"] == ["Writing Poetry"]
    assert body["criteria"][0]["rule_payload"] == {"op": "gte", "value": 0.8, "scale": "percent"}


def test_pagination_is_off_by_default(
    user_client: APIClient,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Three competencies come back together in one response with no pagination fields."""
    tags = make_three_competencies(competency_taxonomy, course_run, default_rule_profile)

    body = user_client.get(criteria_url(PLAIN_KEY)).json()

    assert [competency["id"] for competency in body["competencies"]] == [tag.id for tag in tags]
    assert set(body) == {"taxonomies", "competencies", "groups", "criteria"}


def test_page_size_pages_by_competency_and_carries_each_competencys_groups_and_criteria(
    user_client: APIClient,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    With page_size=2 the first page holds two competencies with their taxonomy once, all their groups
    and their criteria, plus the count fields. The second page holds the third competency.
    """
    tags = make_three_competencies(competency_taxonomy, course_run, default_rule_profile)

    first = user_client.get(criteria_url(PLAIN_KEY), {"page_size": 2}).json()
    second = user_client.get(criteria_url(PLAIN_KEY), {"page": 2, "page_size": 2}).json()

    assert set(first) == {"taxonomies", "competencies", "groups", "criteria"} | PAGINATION_KEYS
    assert [competency["id"] for competency in first["competencies"]] == [tags[0].id, tags[1].id]
    assert [taxonomy["id"] for taxonomy in first["taxonomies"]] == [competency_taxonomy.id]
    assert {group["competency_id"] for group in first["groups"]} == {tags[0].id, tags[1].id}
    assert len(first["groups"]) == 6
    first_group_ids = {group["id"] for group in first["groups"]}
    assert all(group["parent_id"] is None or group["parent_id"] in first_group_ids for group in first["groups"])
    assert len(first["criteria"]) == 2
    assert all(criterion["group_id"] in first_group_ids for criterion in first["criteria"])
    assert (first["count"], first["num_pages"], first["current_page"]) == (3, 2, 1)
    assert "page=2" in first["next"]
    assert first["previous"] is None
    assert [competency["id"] for competency in second["competencies"]] == [tags[2].id]
    assert [taxonomy["id"] for taxonomy in second["taxonomies"]] == [competency_taxonomy.id]
    assert len(second["groups"]) == 3
    assert len(second["criteria"]) == 1
    assert (second["count"], second["num_pages"], second["current_page"]) == (3, 2, 2)
    assert second["next"] is None
    assert second["previous"] is not None


def test_page_alone_turns_pagination_on(
    user_client: APIClient,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A request with only ``page`` is paginated with the default page size, so the pagination fields appear."""
    tags = make_three_competencies(competency_taxonomy, course_run, default_rule_profile)

    body = user_client.get(criteria_url(PLAIN_KEY), {"page": 1}).json()

    assert set(body) == {"taxonomies", "competencies", "groups", "criteria"} | PAGINATION_KEYS
    assert [competency["id"] for competency in body["competencies"]] == [tag.id for tag in tags]
    assert (body["count"], body["num_pages"], body["current_page"]) == (3, 1, 1)
    assert body["next"] is None
    assert body["previous"] is None


def test_query_count_is_the_same_with_and_without_pagination_and_for_any_page_size(
    user_client: APIClient,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Paging happens in memory after one fixed set of queries, so the page size cannot change the count."""
    make_three_competencies(competency_taxonomy, course_run, default_rule_profile)

    counts = {}
    for label, params in {
        "unpaginated": {},
        "page": {"page": 1},
        "page size 1": {"page_size": 1},
        "page size 500": {"page_size": 500},
    }.items():
        with CaptureQueriesContext(connection) as queries:
            response = user_client.get(criteria_url(PLAIN_KEY), params)
        assert response.status_code == status.HTTP_200_OK
        counts[label] = len(queries)

    assert set(counts.values()) == {counts["unpaginated"]}
    assert counts["unpaginated"] > 0


def test_page_past_the_end_gets_404(
    user_client: APIClient, course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """With one competency and a page size of one, page 2 does not exist."""
    make_leaf_criterion(tag, course_run, default_rule_profile, "s1")

    response = user_client.get(criteria_url(PLAIN_KEY), {"page": 2, "page_size": 1})

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_only_get_is_allowed(method: str, user_client: APIClient) -> None:
    """The endpoint is read-only: every other method is answered with 405."""
    response = getattr(user_client, method)(criteria_url(PLAIN_KEY))

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


# =================================================================================================
# URL modules
# =================================================================================================


def test_learner_url_module_serves_the_route_and_the_authoring_module_does_not() -> None:
    """The route resolves to the view through the learner module, and the authoring module has no such route."""
    path = f"/api/cbe/v1/courses/{PLAIN_KEY}/criteria/"
    learner_path = path.removeprefix("/api")

    assert criteria_url(PLAIN_KEY) == path
    for match in (resolve(path), resolve(learner_path, urlconf="openedx_learning.learner_urls")):
        assert match.view_name == "cbe_learner:course-criteria"
    with pytest.raises(Resolver404):
        resolve(learner_path, urlconf="openedx_learning.urls")
