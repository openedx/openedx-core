"""
Tests for the CBE REST API views.

Fixtures live in this directory's conftest.py. Several scenarios here create rule profile rows
directly rather than through a live call, because no create or archive endpoint exists yet.
"""
from typing import Any, Generator

import pytest
import rules
from django.urls import reverse
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from organizations.models import Organization
from rest_framework import status
from rest_framework.test import APIClient

from openedx_catalog.models import CatalogCourse, CourseRun
from openedx_learning.applets.cbe.api import _create_leaf_group
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    LogicOperator,
    RuleType,
)
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

pytestmark = pytest.mark.django_db

# A marker embedded in an object_id to signal "this caller has no object-level tagging
# permission", for the one test that needs a permission failure. oel_tagging.rules hardcodes
# change_objecttag_objectid to False ("should be defined in other apps for proper permission
# checking"): real Studio-role logic lives only in openedx-platform, never in this repo. Following
# tests/openedx_tagging/test_views.py's TestObjectTagViewSet.setUp() precedent, this override
# simulates a permitted caller for every object_id except ones carrying this marker. Like the
# platform's rule, it also denies an object_id that is neither a parseable usage key nor a course key.
UNAUTHORIZED_MARKER = "unauthorized"


@pytest.fixture(autouse=True)
def _allow_object_level_tagging() -> Generator[None, None, None]:
    """Let an ordinary authenticated user tag any usage or course key except one carrying UNAUTHORIZED_MARKER."""
    def _predicate(_user, object_id: str) -> bool:
        try:
            UsageKey.from_string(object_id)
        except InvalidKeyError:
            try:
                # The bulk-update endpoint checks permission against the group's course key.
                CourseKey.from_string(object_id)
            except InvalidKeyError:
                return False
        return UNAUTHORIZED_MARKER not in object_id

    perm = "oel_tagging.change_objecttag_objectid"
    original = rules.permissions.permissions[perm]
    rules.set_perm(perm, _predicate)
    yield
    rules.set_perm(perm, original)


@pytest.fixture(name="user_client")
def _user_client(api_client: APIClient, user) -> APIClient:
    """A REST client acting as `user`, an ordinary, non-staff, authenticated caller."""
    api_client.force_authenticate(user=user)
    return api_client


def criterion_create_url(tag_id: int) -> str:
    """Return the create-criterion endpoint's path for `tag_id`, resolved through the URL name."""
    return reverse("cbe:criterion-create", kwargs={"tag_id": tag_id})


def usage_key(course_run: CourseRun, block_id: str) -> str:
    """Build a gradeable-subsection-shaped usage key string under `course_run`."""
    key = course_run.course_key
    assert key is not None
    return f"block-v1:{key.org}+{key.course}+{key.run}+type@sequential+block@{block_id}"


def make_course_run(organization: Organization, course_code: str, run_code: str) -> CourseRun:
    """Create a CourseRun distinct from the `course_run` fixture, for the different-course tests."""
    catalog_course = CatalogCourse.objects.create(org=organization, course_code=course_code)
    return CourseRun.objects.create(catalog_course=catalog_course, run_code=run_code)


# What migration 0005 seeds the system default with. Asserted verbatim rather than imported, so
# that a change to the seed surfaces here as a failing contract instead of passing silently.
SEEDED_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}

# A different valid payload for rows these tests create, so no assertion about the seeded row
# can pass by accident against a row a test made itself.
FIXTURE_GRADE_PAYLOAD = {"op": "gte", "value": 0.6, "scale": "percent"}

RESPONSE_FIELDS = {"id", "scope_type", "rule_type", "rule_payload", "archived"}


def rule_profiles_url() -> str:
    """Return the collection's path, resolved through the router rather than hardcoded."""
    return reverse("cbe:rule_profile-list")


def make_profile(**scope) -> CompetencyRuleProfile:
    """Create a live rule profile at `scope`, which is at most one of the three scope columns."""
    return CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE, rule_payload=dict(FIXTURE_GRADE_PAYLOAD), **scope
    )


def test_collection_resolves_to_the_documented_path() -> None:
    """
    The router and the mounts compose into the path the CBE tickets name.

    Pinned in one place so the rest of these tests can use reverse() instead.
    """
    assert rule_profiles_url() == "/api/cbe/v1/rule_profiles/"


def test_read_the_rule_the_instance_requires_for_mastery(
    staff_client: APIClient,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A permitted caller reads the instance-wide default, complete enough to act on.

    The threshold arrives with the scale it is expressed on, so a caller cannot read the
    fraction as a percentage or the reverse.
    """
    response = staff_client.get(rule_profiles_url())

    assert response.status_code == status.HTTP_200_OK
    assert len(response.data["results"]) == 1
    profile = response.data["results"][0]
    assert profile["id"] == default_rule_profile.id
    assert profile["scope_type"] == "system_default"
    assert profile["rule_type"] == "Grade"
    assert profile["rule_payload"] == SEEDED_GRADE_PAYLOAD
    assert profile["archived"] is False


def test_response_omits_internal_scope_bookkeeping(
    staff_client: APIClient,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """The response describes scope in terms a caller outside this system can act on."""
    response = staff_client.get(rule_profiles_url())

    profile = response.data["results"][0]
    assert profile["id"] == default_rule_profile.id
    assert set(profile.keys()) == RESPONSE_FIELDS
    assert "scope_code" not in profile
    assert "organization" not in profile
    assert "course" not in profile
    assert "competency_taxonomy" not in profile


def test_every_profile_reports_the_scope_it_applies_to(
    staff_client: APIClient,
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    organization: Organization,
) -> None:
    """
    A caller tells the instance-wide default apart from a narrower profile by scope alone.

    Each row here holds a different scope_code, which is what shows the derivation reads the
    scope columns rather than matching that string.
    """
    taxonomy_scoped = make_profile(competency_taxonomy=competency_taxonomy)
    course_scoped = make_profile(course=course_run)
    organization_scoped = make_profile(organization=organization)

    response = staff_client.get(rule_profiles_url())

    assert response.status_code == status.HTTP_200_OK
    scope_types = {row["id"]: row["scope_type"] for row in response.data["results"]}
    assert scope_types == {
        default_rule_profile.id: "system_default",
        taxonomy_scoped.id: "taxonomy",
        course_scoped.id: "course",
        organization_scoped.id: "organization",
    }


def test_retired_profiles_are_left_out(
    staff_client: APIClient,
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """A retired profile is not returned, while the instance-wide default still is."""
    archived = make_profile(competency_taxonomy=competency_taxonomy, archived=True)

    response = staff_client.get(rule_profiles_url())

    returned_ids = [row["id"] for row in response.data["results"]]
    assert archived.id not in returned_ids
    assert returned_ids == [default_rule_profile.id]


def test_instance_with_no_rule_profiles_reports_an_empty_collection(staff_client: APIClient) -> None:
    """
    An instance holding no profiles is an empty collection, not a missing resource.

    Migration 0005 seeds the system default into the test database, so the rows are cleared
    explicitly here rather than assuming an empty table.
    """
    CompetencyRuleProfile.objects.all().delete()

    response = staff_client.get(rule_profiles_url())

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 0
    assert response.data["results"] == []


def test_response_is_the_paginated_envelope(
    staff_client: APIClient,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """The collection arrives in an envelope, never as a bare array."""
    response = staff_client.get(rule_profiles_url())

    assert isinstance(response.data, dict)
    assert {"count", "next", "previous", "results"} <= set(response.data.keys())
    assert isinstance(response.data["results"], list)
    assert [row["id"] for row in response.data["results"]] == [default_rule_profile.id]


def test_a_collection_larger_than_one_response_arrives_whole(
    staff_client: APIClient,
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    organization: Organization,
) -> None:
    """
    Following the link to the remainder yields every profile once, with none skipped.

    The page size divides the four profiles unevenly on purpose, so a partial last page is
    exercised rather than a run of exactly full ones.
    """
    expected_ids = [
        default_rule_profile.id,
        make_profile(competency_taxonomy=competency_taxonomy).id,
        make_profile(course=course_run).id,
        make_profile(organization=organization).id,
    ]

    collected_ids: list[int] = []
    pages = 0
    next_url = f"{rule_profiles_url()}?page_size=3"
    while next_url:
        response = staff_client.get(next_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == len(expected_ids)
        collected_ids.extend(row["id"] for row in response.data["results"])
        next_url = response.data["next"]
        pages += 1

    assert pages == 2, "page_size=3 should have split four profiles across two responses"
    assert collected_ids == expected_ids


def test_profiles_arrive_in_the_same_order_every_time(
    staff_client: APIClient,
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    organization: Organization,
) -> None:
    """Two requests with nothing changing in between return the profiles in the same order."""
    make_profile(competency_taxonomy=competency_taxonomy)
    make_profile(course=course_run)
    make_profile(organization=organization)

    first = [row["id"] for row in staff_client.get(rule_profiles_url()).data["results"]]
    second = [row["id"] for row in staff_client.get(rule_profiles_url()).data["results"]]

    assert first == second
    assert first == sorted(first)
    assert default_rule_profile.id in first


@pytest.mark.parametrize(
    "user_fixture, expected_status",
    [
        (None, status.HTTP_401_UNAUTHORIZED),
        ("user", status.HTTP_200_OK),
        ("staff_user", status.HTTP_200_OK),
    ],
)
def test_any_signed_in_caller_may_read_the_collection(
    request: pytest.FixtureRequest,
    api_client: APIClient,
    default_rule_profile: CompetencyRuleProfile,
    user_fixture: str | None,
    expected_status: int,
) -> None:
    if user_fixture is not None:
        api_client.force_authenticate(user=request.getfixturevalue(user_fixture))

    response = api_client.get(rule_profiles_url())

    assert response.status_code == expected_status
    if expected_status == status.HTTP_200_OK:
        assert [row["id"] for row in response.data["results"]] == [default_rule_profile.id]
    else:
        assert "results" not in response.data


# ==============================================================================================
# CompetencyCriterionCreateView (#665)
# ==============================================================================================


def test_no_group_no_existing_groups_creates_the_full_hierarchy_and_201s(
    user_client: APIClient, tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """The happy path: no group_id, no groups yet, no rule fields supplied."""
    object_id = usage_key(course_run, "p1")

    response = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    criterion = CompetencyCriterion.objects.get(pk=response.data["id"])
    assert criterion.group.tag_id == tag.id
    course_level = criterion.group.parent
    assert course_level is not None
    assert course_level.course_id == course_run.id
    root = course_level.parent
    assert root is not None
    assert root.parent is None
    assert response.data["object_tag_id"] == criterion.object_tag_id
    assert response.data["group_id"] == criterion.group_id
    assert response.data["rule_profile_id"] == default_rule_profile.id


def test_logic_operator_provided_is_stored_on_the_new_leaf(
    user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """A supplied logic_operator is stored on the newly created leaf, not defaulted."""
    object_id = usage_key(course_run, "p1")

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": object_id, "logic_operator": "AND"}, format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    criterion = CompetencyCriterion.objects.get(pk=response.data["id"])
    assert criterion.group.logic_operator == LogicOperator.AND


def test_logic_operator_omitted_defaults_to_or(user_client: APIClient, tag: Tag, course_run: CourseRun) -> None:
    """Omitting logic_operator on the derive-or-create path stores "OR" literally."""
    object_id = usage_key(course_run, "p1")

    response = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    criterion = CompetencyCriterion.objects.get(pk=response.data["id"])
    assert criterion.group.logic_operator == LogicOperator.OR


def test_group_id_and_logic_operator_together_is_rejected(
    user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """Supplying both group_id and logic_operator is a 400, before anything is created."""
    leaf = _create_leaf_group(tag, course_run)
    object_id = usage_key(course_run, "p1")

    response = user_client.post(
        criterion_create_url(tag.id),
        {"object_id": object_id, "group_id": leaf.id, "logic_operator": "AND"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "logic_operator" in response.data


def test_course_level_group_is_reused_within_the_same_course(
    user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """Two criteria created for the same tag and course share one course-level group."""
    first = user_client.post(
        criterion_create_url(tag.id), {"object_id": usage_key(course_run, "p1")}, format="json",
    )
    second = user_client.post(
        criterion_create_url(tag.id), {"object_id": usage_key(course_run, "p2")}, format="json",
    )

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED
    first_group = CompetencyCriterion.objects.get(pk=first.data["id"]).group
    second_group = CompetencyCriterion.objects.get(pk=second.data["id"]).group
    assert first_group.id != second_group.id
    assert first_group.parent_id == second_group.parent_id


def test_new_course_level_group_for_a_different_course(
    user_client: APIClient, tag: Tag, course_run: CourseRun, organization: Organization,
) -> None:
    """A second course under the same tag gets its own course-level group, but shares the root."""
    other_course_run = make_course_run(organization, "Python200", "Fall2026")

    first = user_client.post(
        criterion_create_url(tag.id), {"object_id": usage_key(course_run, "p1")}, format="json",
    )
    second = user_client.post(
        criterion_create_url(tag.id), {"object_id": usage_key(other_course_run, "p1")}, format="json",
    )

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED
    first_group = CompetencyCriterion.objects.get(pk=first.data["id"]).group
    second_group = CompetencyCriterion.objects.get(pk=second.data["id"]).group
    assert first_group.parent_id != second_group.parent_id
    assert first_group.parent is not None
    assert second_group.parent is not None
    assert first_group.parent.parent_id == second_group.parent.parent_id


def test_supplied_leaf_group_happy_path(user_client: APIClient, tag: Tag, course_run: CourseRun) -> None:
    """Supplying an existing, valid leaf group_id uses it directly."""
    leaf = _create_leaf_group(tag, course_run)
    object_id = usage_key(course_run, "p1")

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": object_id, "group_id": leaf.id}, format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["group_id"] == leaf.id


def test_supplied_group_non_leaf_is_rejected(user_client: APIClient, tag: Tag, course_run: CourseRun) -> None:
    """A supplied group_id that names a root (non-leaf) group is a 400."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)
    object_id = usage_key(course_run, "p1")

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": object_id, "group_id": root.id}, format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "group_id" in response.data


def test_supplied_group_for_a_different_competency_is_rejected(
    user_client: APIClient, tag: Tag, course_run: CourseRun, competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """A supplied group_id that belongs to a different competency tag is a 400."""
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Other Competency")
    other_leaf = _create_leaf_group(other_tag, course_run)
    object_id = usage_key(course_run, "p1")

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": object_id, "group_id": other_leaf.id}, format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "group_id" in response.data


def test_supplied_group_for_a_different_course_is_rejected(
    user_client: APIClient, tag: Tag, course_run: CourseRun, organization: Organization,
) -> None:
    """A supplied group_id whose course-level parent belongs to a different course is a 400."""
    other_course_run = make_course_run(organization, "Python200", "Fall2026")
    other_leaf = _create_leaf_group(tag, other_course_run)
    object_id = usage_key(course_run, "p1")

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": object_id, "group_id": other_leaf.id}, format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "group_id" in response.data


def test_duplicate_association_supplying_the_same_group_is_rejected(
    user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """A second criterion for the same (tag, object_id), re-supplying the group it just created, is a 400."""
    object_id = usage_key(course_run, "p1")
    first = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")
    first_group_id = first.data["group_id"]

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": object_id, "group_id": first_group_id}, format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "group_id" in response.data


def test_duplicate_association_supplying_a_different_existing_leaf_creates_a_second_criterion(
    user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """
    A second criterion for the same (tag, object_id), explicitly aimed at a *different* existing
    leaf, is a deliberate second association, not a duplicate -- per ADR-0002's worked example,
    the same tag/object association may participate in more than one CompetencyCriteriaGroup.
    Only re-targeting the exact same group already used is rejected (see the sibling test).
    """
    object_id = usage_key(course_run, "p1")
    first = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")
    first_group_id = first.data["group_id"]
    other_leaf = _create_leaf_group(tag, course_run)
    groups_before = CompetencyCriteriaGroup.objects.count()

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": object_id, "group_id": other_leaf.id}, format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["group_id"] == other_leaf.id
    # No new groups: other_leaf already existed and was supplied explicitly.
    assert CompetencyCriteriaGroup.objects.count() == groups_before
    criterion_group_ids = set(
        CompetencyCriterion.objects.filter(object_tag_id=response.data["object_tag_id"])
        .values_list("group_id", flat=True)
    )
    assert criterion_group_ids == {first_group_id, other_leaf.id}


def test_duplicate_association_via_the_derive_path_is_rejected_before_creating_a_group(
    user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """
    A second criterion for the same (tag, object_id), with no group_id, is rejected before any new group.

    Rejected regardless of which group the existing criterion is actually in: with no group_id
    supplied, there's no way to know which group slot this request intends, so any existing
    criterion for the pair counts as a duplicate.
    """
    object_id = usage_key(course_run, "p1")
    user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")
    groups_before = CompetencyCriteriaGroup.objects.count()

    response = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "object_id" in response.data
    assert CompetencyCriteriaGroup.objects.count() == groups_before


def test_all_null_rule_fields_resolve_to_the_system_default_profile_id(
    user_client: APIClient, tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Omitting rule_profile_id, rule_type_override, and rule_payload_override together resolves
    rule_profile_id to the seeded system-default profile's id, not null.

    CompetencyCriterion's own oel_cbe_criterion_profile_xor_override_check constraint never
    allows all three fields null.
    """
    object_id = usage_key(course_run, "p1")

    response = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["rule_profile_id"] == default_rule_profile.id
    assert response.data["rule_type_override"] is None
    assert response.data["rule_payload_override"] is None


def test_reusing_a_subsection_already_tagged_with_a_different_competency_preserves_both_tags(
    user_client: APIClient, tag: Tag, course_run: CourseRun, competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """
    Creating a criterion for a second competency on an already-tagged object merges, not overwrites.

    Both tags here come from the SAME CompetencyTaxonomy, per the plan's explicit note: only the
    same-taxonomy case exercises tag_object()'s replace-the-whole-list behavior, since two
    different taxonomies' ObjectTag rows would never collide even against a naive
    overwrite-instead-of-merge implementation.
    """
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Other Competency")
    object_id = usage_key(course_run, "p1")

    first = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")
    second = user_client.post(criterion_create_url(other_tag.id), {"object_id": object_id}, format="json")

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED
    applied_tag_ids = set(
        ObjectTag.objects.filter(object_id=object_id, taxonomy=competency_taxonomy).values_list("tag_id", flat=True)
    )
    assert applied_tag_ids == {tag.id, other_tag.id}


def test_competency_not_found_404s(user_client: APIClient, course_run: CourseRun) -> None:
    """An unresolvable tag_id (URL path parameter) 404s."""
    object_id = usage_key(course_run, "p1")

    response = user_client.post(criterion_create_url(999999), {"object_id": object_id}, format="json")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.data["detail"] == "Tag matching query does not exist."


def test_group_not_found_404s(user_client: APIClient, tag: Tag, course_run: CourseRun) -> None:
    """An unresolvable group_id 404s."""
    object_id = usage_key(course_run, "p1")

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": object_id, "group_id": 999999}, format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.data["detail"] == "CompetencyCriteriaGroup matching query does not exist."


def test_missing_object_id_is_400(user_client: APIClient, tag: Tag) -> None:
    """A request with no object_id at all is a 400."""
    response = user_client.post(criterion_create_url(tag.id), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "object_id" in response.data


def test_malformed_object_id_is_400(user_client: APIClient, tag: Tag) -> None:
    """
    An object_id that isn't a parseable usage key is a 400, keyed by object_id.

    A 400 even though the permission predicate denies an unparseable key, so the body is
    validated before permission is checked.
    """
    response = user_client.post(criterion_create_url(tag.id), {"object_id": "not-a-usage-key"}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "object_id" in response.data


def test_unresolvable_course_is_400(user_client: APIClient, tag: Tag) -> None:
    """A well-formed usage key whose course has no matching CourseRun is a 400."""
    object_id = "block-v1:NoOrg+NoCourse+NoRun+type@sequential+block@p1"

    response = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "object_id" in response.data


def test_no_permission_is_403(user_client: APIClient, tag: Tag, course_run: CourseRun) -> None:
    """A caller without object-level tagging permission on this object_id is refused with a 403."""
    object_id = usage_key(course_run, UNAUTHORIZED_MARKER)

    response = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_tag_on_a_plain_taxonomy_404s(user_client: APIClient, course_run: CourseRun) -> None:
    """A tag_id naming a tag outside any CompetencyTaxonomy 404s."""
    plain_tag = Tag.objects.create(taxonomy=Taxonomy.objects.create(name="Plain Tags"), value="Plain")

    response = user_client.post(
        criterion_create_url(plain_tag.id), {"object_id": usage_key(course_run, "p1")}, format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.data["detail"] == "Tag is not on a CompetencyTaxonomy."


def test_all_null_rule_fields_resolve_to_the_live_profile_for_the_tags_taxonomy(
    user_client: APIClient, tag: Tag, course_run: CourseRun, competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """A live profile scoped to the tag's own taxonomy wins over the system default."""
    taxonomy_scoped = make_profile(competency_taxonomy=competency_taxonomy)

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": usage_key(course_run, "p1")}, format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["rule_profile_id"] == taxonomy_scoped.id
    assert response.data["rule_type_override"] is None
    assert response.data["rule_payload_override"] is None


def test_all_null_rule_fields_skip_an_archived_profile_for_the_tags_taxonomy(
    user_client: APIClient,
    tag: Tag,
    course_run: CourseRun,
    competency_taxonomy: CompetencyTaxonomy,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """An archived profile scoped to the tag's taxonomy is passed over for the system default."""
    make_profile(competency_taxonomy=competency_taxonomy, archived=True)

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": usage_key(course_run, "p1")}, format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["rule_profile_id"] == default_rule_profile.id


def test_all_null_rule_fields_ignore_a_profile_for_a_different_taxonomy(
    user_client: APIClient, tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A live profile scoped to some other competency taxonomy does not apply to this tag."""
    make_profile(competency_taxonomy=CompetencyTaxonomy.objects.create(name="Welding", export_id="welding-v1"))

    response = user_client.post(
        criterion_create_url(tag.id), {"object_id": usage_key(course_run, "p1")}, format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["rule_profile_id"] == default_rule_profile.id


@pytest.mark.parametrize(
    "rule_fields, rejected_field",
    [
        ({"rule_type_override": RuleType.GRADE}, "rule_type_override"),
        ({"rule_payload_override": FIXTURE_GRADE_PAYLOAD}, "rule_payload_override"),
        ({"use_live_profile": True, "rule_type_override": RuleType.GRADE}, "rule_profile_id"),
        ({"use_live_profile": True, "rule_payload_override": FIXTURE_GRADE_PAYLOAD}, "rule_profile_id"),
        (
            {
                "use_live_profile": True,
                "rule_type_override": RuleType.GRADE,
                "rule_payload_override": FIXTURE_GRADE_PAYLOAD,
            },
            "rule_profile_id",
        ),
    ],
)
def test_invalid_rule_field_combination_is_400_and_creates_nothing(
    user_client: APIClient,
    tag: Tag,
    course_run: CourseRun,
    rule_fields: dict,
    rejected_field: str,
) -> None:
    """A profile alongside any override, or one override without the other, is a 400 keyed by the offending field."""
    body = {"object_id": usage_key(course_run, "p1"), **rule_fields}
    if body.pop("use_live_profile", False):
        body["rule_profile_id"] = make_profile(competency_taxonomy_id=tag.taxonomy_id).id

    response = user_client.post(criterion_create_url(tag.id), body, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert rejected_field in response.data
    assert not CompetencyCriterion.objects.exists()
    assert not CompetencyCriteriaGroup.objects.exists()


@pytest.mark.parametrize("archived", [True, None], ids=["archived", "unknown"])
def test_unusable_rule_profile_id_is_400_and_creates_nothing(
    user_client: APIClient,
    tag: Tag,
    course_run: CourseRun,
    archived: bool | None,
) -> None:
    """A rule_profile_id naming an archived or nonexistent profile is a 400 keyed by rule_profile_id."""
    rule_profile_id = make_profile(competency_taxonomy_id=tag.taxonomy_id, archived=True).id if archived else 999999

    response = user_client.post(
        criterion_create_url(tag.id),
        {"object_id": usage_key(course_run, "p1"), "rule_profile_id": rule_profile_id},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "rule_profile_id" in response.data
    assert not CompetencyCriterion.objects.exists()
    assert not CompetencyCriteriaGroup.objects.exists()


def test_tag_refused_by_a_single_tag_taxonomy_is_400_and_creates_nothing(
    user_client: APIClient, tag: Tag, course_run: CourseRun, competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """A second competency on an object in a taxonomy allowing one tag per object is a 400 keyed by object_id."""
    competency_taxonomy.allow_multiple = False
    competency_taxonomy.save()
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Other Competency")
    object_id = usage_key(course_run, "p1")
    ObjectTag.objects.create(object_id=object_id, taxonomy=competency_taxonomy, tag=other_tag)

    response = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "object_id" in response.data
    assert not CompetencyCriterion.objects.exists()
    assert not CompetencyCriteriaGroup.objects.exists()


def test_tag_refused_at_the_object_tag_limit_is_400_and_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """An object already at the tag limit is a 400 keyed by object_id."""
    # Lowered so one existing tag reaches the limit, instead of creating 100 rows.
    monkeypatch.setattr("openedx_tagging.api.OBJECT_MAX_TAGS", 1)
    other_taxonomy = Taxonomy.objects.create(name="Other Taxonomy")
    other_tag = Tag.objects.create(taxonomy=other_taxonomy, value="Other Tag")
    object_id = usage_key(course_run, "p1")
    ObjectTag.objects.create(object_id=object_id, taxonomy=other_taxonomy, tag=other_tag)

    response = user_client.post(criterion_create_url(tag.id), {"object_id": object_id}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Cannot add more than 1 tags" in str(response.data["object_id"])
    assert not CompetencyCriterion.objects.exists()
    assert not CompetencyCriteriaGroup.objects.exists()


# ==============================================================================================
# CompetencyCriterionBulkUpdateView (#759)
# ==============================================================================================

NEW_PAYLOAD = {"op": "gte", "value": 0.75, "scale": "percent"}
NEW_RULE: dict[str, Any] = {"rule_type_override": "Grade", "rule_payload_override": NEW_PAYLOAD}
CRITERION_FIELDS = {"id", "group_id", "rule_profile_id", "rule_type_override", "rule_payload_override", "object_tag_id"}


def bulk_update_url(group_id: int) -> str:
    """Return the bulk-update endpoint's path for `group_id`, resolved through the URL name."""
    return reverse("cbe:criterion-bulk-update", kwargs={"group_id": group_id})


def make_criterion(leaf: CompetencyCriteriaGroup, block_id: str, **rule) -> CompetencyCriterion:
    """Create a criterion on `leaf` for a fresh object in its course, with `rule` as its rule columns."""
    assert leaf.parent is not None and leaf.parent.course is not None
    object_tag = ObjectTag.objects.create(
        object_id=usage_key(leaf.parent.course, block_id), taxonomy=leaf.tag.taxonomy, tag=leaf.tag,
    )
    return CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, **rule)


@pytest.fixture(name="leaf")
def _leaf(tag: Tag, course_run: CourseRun) -> CompetencyCriteriaGroup:
    """A leaf group under `tag` and `course_run`. Only a leaf group holds criteria."""
    return _create_leaf_group(tag, course_run)


@pytest.fixture(name="batch")
def _batch(
    leaf: CompetencyCriteriaGroup, default_rule_profile: CompetencyRuleProfile
) -> tuple[CompetencyCriterion, CompetencyCriterion]:
    """Two criteria on `leaf` with different rule sources: one follows the default, one has an override."""
    return (
        make_criterion(leaf, "p1", rule_profile=default_rule_profile),
        make_criterion(
            leaf, "p2", rule_type_override=RuleType.GRADE, rule_payload_override=dict(FIXTURE_GRADE_PAYLOAD),
        ),
    )


def stored_rules(criteria) -> list[tuple]:
    """Return each criterion's three rule columns as stored, to prove a refused request changed nothing."""
    rows = []
    for criterion in criteria:
        criterion.refresh_from_db()
        rows.append((criterion.rule_profile_id, criterion.rule_type_override, criterion.rule_payload_override))
    return rows


def test_bulk_update_resolves_to_the_documented_path() -> None:
    """The bulk-update route sits under the group whose criteria it edits."""
    assert bulk_update_url(7) == "/api/cbe/v1/criteria-groups/7/criteria/bulk-update/"


def test_bulk_update_with_rule_values_echoes_every_criterion_as_stored(
    user_client: APIClient, batch: tuple[CompetencyCriterion, CompetencyCriterion], leaf: CompetencyCriteriaGroup,
) -> None:
    """Every named criterion comes back, in request order, carrying the values and no profile."""
    ids = [batch[1].id, batch[0].id]

    response = user_client.patch(bulk_update_url(leaf.id), {"criterion_ids": ids, **NEW_RULE}, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert [row["id"] for row in response.data] == ids
    for row in response.data:
        assert set(row.keys()) == CRITERION_FIELDS
        assert row["group_id"] == leaf.id
        assert (row["rule_profile_id"], row["rule_type_override"], row["rule_payload_override"]) == (
            None, "Grade", NEW_PAYLOAD,
        )


def test_bulk_update_with_a_named_profile_echoes_every_criterion_following_it(
    user_client: APIClient,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Every named criterion comes back following the profile, with no values of its own."""
    ids = [criterion.id for criterion in batch]

    response = user_client.patch(
        bulk_update_url(leaf.id), {"criterion_ids": ids, "rule_profile_id": default_rule_profile.id}, format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert [row["id"] for row in response.data] == ids
    for row in response.data:
        assert (row["rule_profile_id"], row["rule_type_override"], row["rule_payload_override"]) == (
            default_rule_profile.id, None, None,
        )


def test_bulk_update_with_values_matching_the_default_reports_the_default(
    user_client: APIClient,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Values equal to the applicable default are reported as following it, not as an override."""
    body = {
        "criterion_ids": [criterion.id for criterion in batch],
        "rule_type_override": "Grade",
        "rule_payload_override": SEEDED_GRADE_PAYLOAD,
    }

    response = user_client.patch(bulk_update_url(leaf.id), body, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert {row["rule_profile_id"] for row in response.data} == {default_rule_profile.id}
    assert {row["rule_payload_override"] for row in response.data} == {None}


@pytest.mark.parametrize(
    "case, error_key",
    [
        ("empty id list", "criterion_ids"),
        ("duplicate id", "criterion_ids"),
        ("ids not a list", "criterion_ids"),
        ("both rule forms", "rule_profile_id"),
        ("neither rule form", "__all__"),
        ("rule type without payload", "rule_type_override"),
        ("payload without rule type", "rule_payload_override"),
        ("unknown profile", "rule_profile_id"),
        ("archived profile", "rule_profile_id"),
        ("unsupported comparison", "__all__"),
        ("threshold out of range", "__all__"),
        ("unimplemented rule type", "rule_type_override"),
        ("unrecognized key", "group_id"),
    ],
)
def test_bulk_update_rejects_a_malformed_body_with_400(  # pylint: disable=too-many-positional-arguments
    case: str,
    error_key: str,
    user_client: APIClient,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """Each malformed body is a 400 naming the offending field, and no criterion changes."""
    first, second = batch
    ids = [first.id, second.id]
    archived = make_profile(competency_taxonomy=competency_taxonomy, archived=True)
    bodies: dict[str, dict[str, Any]] = {
        "empty id list": {"criterion_ids": [], **NEW_RULE},
        "duplicate id": {"criterion_ids": [first.id, first.id], **NEW_RULE},
        "ids not a list": {"criterion_ids": first.id, **NEW_RULE},
        "both rule forms": {"criterion_ids": ids, "rule_profile_id": default_rule_profile.id, **NEW_RULE},
        "neither rule form": {"criterion_ids": ids},
        "rule type without payload": {"criterion_ids": ids, "rule_type_override": "Grade"},
        "payload without rule type": {"criterion_ids": ids, "rule_payload_override": NEW_PAYLOAD},
        "unknown profile": {"criterion_ids": ids, "rule_profile_id": 999999},
        "archived profile": {"criterion_ids": ids, "rule_profile_id": archived.id},
        "unsupported comparison": {
            "criterion_ids": ids, **NEW_RULE, "rule_payload_override": {**NEW_PAYLOAD, "op": "gt"},
        },
        "threshold out of range": {
            "criterion_ids": ids, **NEW_RULE, "rule_payload_override": {**NEW_PAYLOAD, "value": 75},
        },
        "unimplemented rule type": {"criterion_ids": ids, **NEW_RULE, "rule_type_override": "Completion"},
        # A criterion's group is fixed; this endpoint has no field that could move one.
        "unrecognized key": {"criterion_ids": ids, **NEW_RULE, "group_id": leaf.id},
    }
    before = stored_rules(batch)

    response = user_client.patch(bulk_update_url(leaf.id), bodies[case], format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert error_key in response.data
    assert stored_rules(batch) == before


def test_bulk_update_on_a_group_that_is_not_a_leaf_is_400(
    user_client: APIClient, batch: tuple[CompetencyCriterion, CompetencyCriterion], leaf: CompetencyCriteriaGroup,
) -> None:
    """A course-level group holds no criteria of its own, so it cannot be addressed here."""
    assert leaf.parent is not None

    response = user_client.patch(
        bulk_update_url(leaf.parent.id), {"criterion_ids": [batch[0].id], **NEW_RULE}, format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "group_id" in response.data


@pytest.mark.parametrize("stray", ["from another group", "nonexistent"])
def test_bulk_update_naming_a_criterion_outside_the_group_is_404(  # pylint: disable=too-many-positional-arguments
    stray: str,
    user_client: APIClient,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    tag: Tag,
    course_run: CourseRun,
) -> None:
    """One id outside the group fails the whole batch with a 404, and nothing changes."""
    if stray == "from another group":
        stray_id = make_criterion(_create_leaf_group(tag, course_run), "p3", **NEW_RULE).id
    else:
        stray_id = 999999
    before = stored_rules(batch)

    response = user_client.patch(
        bulk_update_url(leaf.id), {"criterion_ids": [batch[0].id, stray_id], **NEW_RULE}, format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert stored_rules(batch) == before


def test_bulk_update_on_a_group_that_does_not_exist_is_404(
    user_client: APIClient, batch: tuple[CompetencyCriterion, CompetencyCriterion],
) -> None:
    """An unknown group id in the URL is a missing resource."""
    response = user_client.patch(bulk_update_url(999999), {"criterion_ids": [batch[0].id], **NEW_RULE}, format="json")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_bulk_update_naming_an_archived_criterion_is_409(
    user_client: APIClient,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
    leaf: CompetencyCriteriaGroup,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """One archived criterion refuses the whole batch with a 409 naming it, and nothing changes."""
    archived = make_criterion(leaf, "p3", rule_profile=default_rule_profile, archived=True)
    criteria = [batch[0], archived, batch[1]]
    before = stored_rules(criteria)

    response = user_client.patch(
        bulk_update_url(leaf.id), {"criterion_ids": [c.id for c in criteria], **NEW_RULE}, format="json",
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert str(archived.id) in response.data["detail"]
    assert stored_rules(criteria) == before


def test_bulk_update_without_permission_on_the_groups_course_is_403(
    user_client: APIClient, tag: Tag, organization: Organization, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A caller who may not tag objects in the group's course is refused, and nothing changes."""
    unauthorized_course = make_course_run(organization, UNAUTHORIZED_MARKER, "Fall2026")
    leaf = _create_leaf_group(tag, unauthorized_course)
    criterion = make_criterion(leaf, "p1", rule_profile=default_rule_profile)

    response = user_client.patch(bulk_update_url(leaf.id), {"criterion_ids": [criterion.id], **NEW_RULE}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert stored_rules([criterion]) == [(default_rule_profile.id, None, None)]


def test_bulk_update_from_an_unidentified_caller_is_401(
    api_client: APIClient, batch: tuple[CompetencyCriterion, CompetencyCriterion], leaf: CompetencyCriteriaGroup,
) -> None:
    """A caller the system cannot identify is refused before anything is read."""
    response = api_client.patch(bulk_update_url(leaf.id), {"criterion_ids": [batch[0].id], **NEW_RULE}, format="json")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("method", ["get", "put", "post", "delete"])
def test_bulk_update_offers_only_patch(method: str, user_client: APIClient, leaf: CompetencyCriteriaGroup) -> None:
    """Every method other than PATCH is refused as not allowed."""
    response = getattr(user_client, method)(bulk_update_url(leaf.id), {}, format="json")

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
