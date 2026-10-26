"""
Tests for the CBE REST API views.

Fixtures live in this directory's conftest.py. Several scenarios here create rule profile rows
directly rather than through a live call, because no create or archive endpoint exists yet.
"""
from datetime import datetime
from typing import Any, Generator
from unittest import mock

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
    MasteryStatus,
    RuleType,
    StudentCompetencyCriterionStatus,
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
                # The bulk-update and group-update endpoints check permission against the group's course key.
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


def criterion_delete_url(tag_id: int, criterion_id: int) -> str:
    """Return the delete-criterion endpoint's path for `tag_id`/`criterion_id`, resolved through the URL name."""
    return reverse("cbe:criterion-delete", kwargs={"tag_id": tag_id, "criterion_id": criterion_id})


def usage_key(course_run: CourseRun, block_id: str) -> str:
    """Build a gradeable-subsection-shaped usage key string under `course_run`."""
    key = course_run.course_key
    assert key is not None
    return f"block-v1:{key.org}+{key.course}+{key.run}+type@sequential+block@{block_id}"


def make_course_run(organization: Organization, course_code: str, run_code: str) -> CourseRun:
    """Create a CourseRun distinct from the `course_run` fixture, for the different-course tests."""
    catalog_course = CatalogCourse.objects.create(org=organization, course_code=course_code)
    return CourseRun.objects.create(catalog_course=catalog_course, run_code=run_code)


def make_tag(taxonomy: CompetencyTaxonomy, value: str, parent: Tag | None = None) -> Tag:
    """Create a Tag on `taxonomy`, optionally under `parent`, for taxonomy-hierarchy tests."""
    return Tag.objects.create(taxonomy=taxonomy, value=value, parent=parent)


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
    assert response.data["archived"] is False


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
CRITERION_FIELDS = {
    "id", "group_id", "rule_profile_id", "rule_type_override", "rule_payload_override", "object_tag_id", "archived",
}


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
        ("unsupported comparison", "rule_payload_override"),
        ("threshold out of range", "rule_payload_override"),
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
    assert list(response.data) == [error_key]
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
    """One id outside the group fails the whole batch with a 404 naming it, and nothing changes."""
    if stray == "from another group":
        stray_id = make_criterion(_create_leaf_group(tag, course_run), "p3", **NEW_RULE).id
    else:
        stray_id = 999999
    before = stored_rules(batch)

    response = user_client.patch(
        bulk_update_url(leaf.id), {"criterion_ids": [batch[0].id, stray_id], **NEW_RULE}, format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert str(stray_id) in response.data["detail"]
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


def test_bulk_update_on_an_archived_group_is_409(
    user_client: APIClient, batch: tuple[CompetencyCriterion, CompetencyCriterion], leaf: CompetencyCriteriaGroup,
) -> None:
    """An archived leaf refuses the whole batch with a 409 naming it, though its criteria aren't archived."""
    CompetencyCriteriaGroup.objects.filter(pk=leaf.pk).update(archived=True)
    before = stored_rules(batch)

    response = user_client.patch(
        bulk_update_url(leaf.id), {"criterion_ids": [c.id for c in batch], **NEW_RULE}, format="json",
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert f"group {leaf.id}" in response.data["detail"]
    assert stored_rules(batch) == before


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


# ==============================================================================================
# CompetencyCriteriaTreeView (#681)
# ==============================================================================================


def criteria_tree_url(tag_id: int) -> str:
    """Return the criteria-tree endpoint's path for `tag_id`, resolved through the URL name."""
    return reverse("cbe:criteria-tree", kwargs={"tag_id": tag_id})


def test_full_tree_response_shape(
    user_client: APIClient,
    tag: Tag,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A permitted caller reads the full tree: every group and the leaf's criterion, complete enough to act on."""
    leaf = _create_leaf_group(tag, course_run)
    object_id = usage_key(course_run, "p1")
    object_tag = ObjectTag.objects.create(object_id=object_id, taxonomy=tag.taxonomy, tag=tag)
    criterion = CompetencyCriterion.objects.create(
        group=leaf, object_tag=object_tag, rule_profile=default_rule_profile,
    )

    course_level = leaf.parent
    assert course_level is not None
    root = course_level.parent
    assert root is not None

    response = user_client.get(criteria_tree_url(tag.id))

    assert response.status_code == status.HTTP_200_OK
    groups_by_id = {group["id"]: group for group in response.data["groups"]}
    assert set(groups_by_id) == {leaf.id, course_level.id, root.id}
    assert set(groups_by_id[leaf.id]) == {
        "id", "parent_id", "tag_id", "course_key", "name", "ordering", "logic_operator",
    }
    assert groups_by_id[root.id]["parent_id"] is None
    assert groups_by_id[root.id]["course_key"] is None
    assert groups_by_id[course_level.id]["parent_id"] == root.id
    assert groups_by_id[leaf.id]["parent_id"] == course_level.id
    assert len(response.data["criteria"]) == 1
    criterion_data = response.data["criteria"][0]
    assert criterion_data["id"] == criterion.id
    assert criterion_data["group_id"] == leaf.id
    assert criterion_data["object_tag_id"] == object_tag.id
    assert criterion_data["object_id"] == object_id
    assert criterion_data["rule_profile_id"] == default_rule_profile.id
    assert criterion_data["rule_type_override"] is None
    assert criterion_data["rule_payload_override"] is None


def test_full_tree_combines_multiple_courses_in_one_unscoped_response(
    user_client: APIClient,
    tag: Tag,
    course_run: CourseRun,
    organization: Organization,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A GET request for one competency returns every course's groups and criteria together,
    with no course_id or date-window scoping parameter to narrow the request.
    """
    other_course_run = make_course_run(organization, "Python200", "Fall2026")
    leaf1 = _create_leaf_group(tag, course_run)
    leaf2 = _create_leaf_group(tag, other_course_run)
    object_tag1 = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    object_tag2 = ObjectTag.objects.create(
        object_id=usage_key(other_course_run, "p1"), taxonomy=tag.taxonomy, tag=tag,
    )
    criterion1 = CompetencyCriterion.objects.create(
        group=leaf1, object_tag=object_tag1, rule_profile=default_rule_profile,
    )
    criterion2 = CompetencyCriterion.objects.create(
        group=leaf2, object_tag=object_tag2, rule_profile=default_rule_profile,
    )
    course_level1 = leaf1.parent
    course_level2 = leaf2.parent
    assert course_level1 is not None
    assert course_level2 is not None
    root = course_level1.parent
    assert root is not None
    assert course_level2.parent_id == root.id

    response = user_client.get(criteria_tree_url(tag.id))

    assert response.status_code == status.HTTP_200_OK
    returned_group_ids = {group["id"] for group in response.data["groups"]}
    assert returned_group_ids == {root.id, course_level1.id, leaf1.id, course_level2.id, leaf2.id}
    assert {c["id"] for c in response.data["criteria"]} == {criterion1.id, criterion2.id}


def test_tree_response_never_references_a_group_it_left_out(
    user_client: APIClient,
    organization: Organization,
    tag: Tag,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """With only a course-level group archived, every parent_id and group_id still names a returned group."""
    leaf = _create_leaf_group(tag, course_run)
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, rule_profile=default_rule_profile)
    # The kept leaf lives under a second course's course-level group, so archiving the first
    # course-level group leaves it attached to a returned parent.
    other_course_run = make_course_run(organization, "Python200", "Fall2026")
    kept_leaf = _create_leaf_group(tag, other_course_run)
    kept_object_tag = ObjectTag.objects.create(
        object_id=usage_key(other_course_run, "p1"), taxonomy=tag.taxonomy, tag=tag,
    )
    kept_criterion = CompetencyCriterion.objects.create(
        group=kept_leaf, object_tag=kept_object_tag, rule_profile=default_rule_profile,
    )
    course_level = leaf.parent
    assert course_level is not None
    course_level.archived = True
    course_level.save()

    response = user_client.get(criteria_tree_url(tag.id))

    assert response.status_code == status.HTTP_200_OK
    assert [c["id"] for c in response.data["criteria"]] == [kept_criterion.id]
    returned_ids = {group["id"] for group in response.data["groups"]}
    assert leaf.id not in returned_ids
    assert all(group["parent_id"] is None or group["parent_id"] in returned_ids for group in response.data["groups"])
    assert all(criterion["group_id"] in returned_ids for criterion in response.data["criteria"])


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_criteria_tree_offers_only_get(method: str, user_client: APIClient, tag: Tag) -> None:
    """Every method other than GET is refused as not allowed."""
    response = getattr(user_client, method)(criteria_tree_url(tag.id), {}, format="json")

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


def test_empty_tree_is_200_with_two_empty_arrays(user_client: APIClient, tag: Tag) -> None:
    """A tag with no CompetencyCriteriaGroup rows yet is a valid 200 with two empty arrays, not a 404."""
    response = user_client.get(criteria_tree_url(tag.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["groups"] == []
    assert response.data["criteria"] == []


def test_archived_group_and_criterion_are_excluded(
    user_client: APIClient,
    tag: Tag,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """An archived leaf group and an archived criterion under it are left out of the response."""
    live_leaf = _create_leaf_group(tag, course_run)
    live_object_tag = ObjectTag.objects.create(
        object_id=usage_key(course_run, "live"), taxonomy=tag.taxonomy, tag=tag,
    )
    live_criterion = CompetencyCriterion.objects.create(
        group=live_leaf, object_tag=live_object_tag, rule_profile=default_rule_profile,
    )
    archived_leaf = _create_leaf_group(tag, course_run)
    archived_leaf.archived = True
    archived_leaf.save()
    archived_object_tag = ObjectTag.objects.create(
        object_id=usage_key(course_run, "archived"), taxonomy=tag.taxonomy, tag=tag,
    )
    CompetencyCriterion.objects.create(
        group=archived_leaf, object_tag=archived_object_tag, rule_profile=default_rule_profile, archived=True,
    )

    response = user_client.get(criteria_tree_url(tag.id))

    assert response.status_code == status.HTTP_200_OK
    returned_group_ids = {group["id"] for group in response.data["groups"]}
    assert live_leaf.id in returned_group_ids
    assert archived_leaf.id not in returned_group_ids
    assert [c["id"] for c in response.data["criteria"]] == [live_criterion.id]


def test_unknown_tag_id_404s(user_client: APIClient) -> None:
    """An unresolvable tag_id 404s."""
    response = user_client.get(criteria_tree_url(999999))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_tag_not_on_a_competency_taxonomy_404s(user_client: APIClient) -> None:
    """A tag_id that resolves to a real Tag, but not one on a CompetencyTaxonomy, 404s."""
    plain_taxonomy = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1")
    plain_tag = Tag.objects.create(taxonomy=plain_taxonomy, value="Not A Competency")

    response = user_client.get(criteria_tree_url(plain_tag.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_no_view_access_to_the_taxonomy_is_403(api_client: APIClient, user, tag: Tag) -> None:
    """A non-staff caller may not read the tree of a competency on a disabled taxonomy."""
    taxonomy = tag.taxonomy
    assert taxonomy is not None
    taxonomy.enabled = False
    taxonomy.save()
    api_client.force_authenticate(user=user)

    response = api_client.get(criteria_tree_url(tag.id))

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_anonymous_request_is_401_and_discloses_nothing(
    tag: Tag,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """An unauthenticated caller gets 401 and none of the tree, even for a competency with a criterion."""
    leaf = _create_leaf_group(tag, course_run)
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, rule_profile=default_rule_profile)

    response = APIClient().get(criteria_tree_url(tag.id))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "groups" not in response.data
    assert "criteria" not in response.data


def test_view_checks_the_view_taxonomy_rule_not_view_tag(user_client: APIClient, tag: Tag) -> None:
    """The object check follows oel_tagging.view_taxonomy, which openedx-platform overrides, not view_tag."""
    view_tag = "oel_tagging.view_tag"
    view_taxonomy = "oel_tagging.view_taxonomy"
    original_view_tag = rules.permissions.permissions[view_tag]
    original_view_taxonomy = rules.permissions.permissions[view_taxonomy]
    rules.set_perm(view_tag, rules.always_allow)
    rules.set_perm(view_taxonomy, rules.always_deny)
    try:
        response = user_client.get(criteria_tree_url(tag.id))
    finally:
        rules.set_perm(view_tag, original_view_tag)
        rules.set_perm(view_taxonomy, original_view_taxonomy)

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_descendant_is_rejected_when_its_ancestor_already_has_a_criterion_in_the_same_course(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, course_run: CourseRun,
) -> None:
    """A tag is rejected when its taxonomy ancestor already has a criterion in the same course."""
    parent = make_tag(competency_taxonomy, "S1 Parent")
    child = make_tag(competency_taxonomy, "S1 Child", parent=parent)
    first = user_client.post(
        criterion_create_url(parent.id), {"object_id": usage_key(course_run, "p1")}, format="json",
    )
    assert first.status_code == status.HTTP_201_CREATED

    response = user_client.post(
        criterion_create_url(child.id), {"object_id": usage_key(course_run, "p2")}, format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert parent.value in str(response.data["tag_id"])
    assert str(course_run.course_key) in str(response.data["tag_id"])


def test_ancestor_is_rejected_when_a_descendant_already_has_a_criterion_in_the_same_course(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, course_run: CourseRun,
) -> None:
    """A tag is rejected when one of its taxonomy descendants already has a criterion in the same course."""
    parent = make_tag(competency_taxonomy, "S2 Parent")
    child = make_tag(competency_taxonomy, "S2 Child", parent=parent)
    first = user_client.post(
        criterion_create_url(child.id), {"object_id": usage_key(course_run, "p1")}, format="json",
    )
    assert first.status_code == status.HTTP_201_CREATED

    response = user_client.post(
        criterion_create_url(parent.id), {"object_id": usage_key(course_run, "p2")}, format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert child.value in str(response.data["tag_id"])
    assert str(course_run.course_key) in str(response.data["tag_id"])


def test_multi_level_ancestor_conflict_names_the_actual_conflicting_tag(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, course_run: CourseRun,
) -> None:
    """
    A conflict several levels up the taxonomy is rejected, and the response names the real
    conflicting tag (the grandparent), not just any ancestor.
    """
    grandparent = make_tag(competency_taxonomy, "S3 Grandparent")
    parent = make_tag(competency_taxonomy, "S3 Parent", parent=grandparent)
    child = make_tag(competency_taxonomy, "S3 Child", parent=parent)
    first = user_client.post(
        criterion_create_url(grandparent.id), {"object_id": usage_key(course_run, "p1")}, format="json",
    )
    assert first.status_code == status.HTTP_201_CREATED

    response = user_client.post(
        criterion_create_url(child.id), {"object_id": usage_key(course_run, "p2")}, format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert grandparent.value in str(response.data["tag_id"])
    assert str(course_run.course_key) in str(response.data["tag_id"])


def test_sibling_tags_are_allowed(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, course_run: CourseRun,
) -> None:
    """Sibling tags, sharing a parent but neither one the other's ancestor, are both allowed."""
    parent = make_tag(competency_taxonomy, "S4 Parent")
    sibling_a = make_tag(competency_taxonomy, "S4 Sibling A", parent=parent)
    sibling_b = make_tag(competency_taxonomy, "S4 Sibling B", parent=parent)
    first = user_client.post(
        criterion_create_url(sibling_a.id), {"object_id": usage_key(course_run, "p1")}, format="json",
    )
    assert first.status_code == status.HTTP_201_CREATED

    response = user_client.post(
        criterion_create_url(sibling_b.id), {"object_id": usage_key(course_run, "p2")}, format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED


def test_an_archived_ancestor_criterion_does_not_block_its_descendant(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, course_run: CourseRun,
) -> None:
    """Once the ancestor's criterion is archived, the descendant may be associated in the same course."""
    parent = make_tag(competency_taxonomy, "S7 Parent")
    child = make_tag(competency_taxonomy, "S7 Child", parent=parent)
    parent_object_id = usage_key(course_run, "p1")
    first = user_client.post(criterion_create_url(parent.id), {"object_id": parent_object_id}, format="json")
    assert first.status_code == status.HTTP_201_CREATED
    CompetencyCriterion.objects.filter(object_tag__object_id=parent_object_id).update(archived=True)

    response = user_client.post(
        criterion_create_url(child.id), {"object_id": usage_key(course_run, "p2")}, format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED


def test_hierarchically_related_tags_scoped_to_different_courses_are_allowed(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, course_run: CourseRun, organization: Organization,
) -> None:
    """An ancestor/descendant pair is allowed when each criterion is scoped to a different course."""
    other_course_run = make_course_run(organization, "Python200", "Fall2026")
    parent = make_tag(competency_taxonomy, "S5 Parent")
    child = make_tag(competency_taxonomy, "S5 Child", parent=parent)
    first = user_client.post(criterion_create_url(parent.id), {"object_id": usage_key(course_run, "p1")}, format="json")
    assert first.status_code == status.HTTP_201_CREATED

    response = user_client.post(
        criterion_create_url(child.id), {"object_id": usage_key(other_course_run, "p1")}, format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED


def test_the_same_tag_with_two_different_criteria_in_the_same_course_is_allowed(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, course_run: CourseRun,
) -> None:
    """A repeat criterion for the same tag in the same course stays allowed."""
    tag = make_tag(competency_taxonomy, "S6 Tag")
    first = user_client.post(criterion_create_url(tag.id), {"object_id": usage_key(course_run, "p1")}, format="json")
    assert first.status_code == status.HTTP_201_CREATED

    response = user_client.post(criterion_create_url(tag.id), {"object_id": usage_key(course_run, "p2")}, format="json")

    assert response.status_code == status.HTTP_201_CREATED


# ==============================================================================================
# CompetencyCriteriaGroupDetailView (#760)
# ==============================================================================================

GROUP_FIELDS = {"id", "parent_id", "tag_id", "course_key", "name", "ordering", "logic_operator"}


def group_detail_url(tag_id: int, group_id: int) -> str:
    """Return the group detail endpoint's path, resolved through the URL name."""
    return reverse("cbe:criteria-group-detail", kwargs={"tag_id": tag_id, "group_id": group_id})


@pytest.fixture(name="course_level")
def _course_level(leaf: CompetencyCriteriaGroup) -> CompetencyCriteriaGroup:
    """The course-level group `leaf` sits under, combining its children with AND."""
    course_level = leaf.parent
    assert course_level is not None
    course_level.logic_operator = LogicOperator.AND
    course_level.save()
    return course_level


def stored_group(group: CompetencyCriteriaGroup) -> tuple:
    """Return `group`'s columns as stored, to prove a refused request changed nothing."""
    group.refresh_from_db()
    return (
        group.logic_operator, group.name, group.ordering, group.tag_id, group.course_id, group.parent_id,
        group.archived,
    )


def test_group_detail_resolves_to_the_documented_path() -> None:
    """The group's own route sits under the competency's criteria-groups collection."""
    assert group_detail_url(7, 9) == "/api/cbe/v1/competencies/7/criteria-groups/9/"


@pytest.mark.parametrize(
    "before, after", [(LogicOperator.AND, LogicOperator.OR), (LogicOperator.OR, LogicOperator.AND)],
)
def test_update_group_switches_its_operator_and_echoes_the_rest(  # pylint: disable=too-many-positional-arguments
    before: str,
    after: str,
    user_client: APIClient,
    tag: Tag,
    course_run: CourseRun,
    course_level: CompetencyCriteriaGroup,
) -> None:
    """The response is the group as stored: the new operator, everything else as before."""
    course_level.logic_operator = before
    course_level.save()

    response = user_client.patch(group_detail_url(tag.id, course_level.id), {"logic_operator": after}, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert set(response.data.keys()) == GROUP_FIELDS
    assert response.data["logic_operator"] == after
    assert response.data["id"] == course_level.id
    assert response.data["name"] == course_level.name
    assert response.data["ordering"] == course_level.ordering
    assert response.data["tag_id"] == tag.id
    assert response.data["course_key"] == str(course_run.course_key)
    assert response.data["parent_id"] == course_level.parent_id


def test_update_group_accepts_back_the_representation_the_tree_endpoint_gave(
    user_client: APIClient, tag: Tag, course_level: CompetencyCriteriaGroup,
) -> None:
    """A client can read the group, change only its operator, and send the whole object back."""
    tree = user_client.get(criteria_tree_url(tag.id))
    read = next(group for group in tree.data["groups"] if group["id"] == course_level.id)

    response = user_client.patch(
        group_detail_url(tag.id, course_level.id), {**read, "logic_operator": LogicOperator.OR}, format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {**read, "logic_operator": LogicOperator.OR}


@pytest.mark.parametrize("sent, expected_status", [
    ("  Padded name  ", status.HTTP_200_OK),
    ("Padded name", status.HTTP_400_BAD_REQUEST),
    ("  padded name  ", status.HTTP_400_BAD_REQUEST),
])
def test_update_group_compares_a_restated_name_exactly(
    sent: str,
    expected_status: int,
    user_client: APIClient,
    tag: Tag,
    course_level: CompetencyCriteriaGroup,
) -> None:
    """A restated name matches only if it is identical, so neither trimming nor a change of case gets through."""
    course_level.name = "  Padded name  "
    course_level.save()

    response = user_client.patch(
        group_detail_url(tag.id, course_level.id), {"logic_operator": LogicOperator.OR, "name": sent}, format="json",
    )

    assert response.status_code == expected_status


def test_update_group_that_changes_nothing_is_200(
    user_client: APIClient, tag: Tag, course_level: CompetencyCriteriaGroup,
) -> None:
    """Sending the operator the group already has succeeds and reports it."""
    response = user_client.patch(
        group_detail_url(tag.id, course_level.id), {"logic_operator": LogicOperator.AND}, format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["logic_operator"] == LogicOperator.AND


@pytest.mark.parametrize(
    "case, error_key",
    [
        ("missing operator", "logic_operator"),
        ("null operator", "logic_operator"),
        ("unsupported operator", "logic_operator"),
        ("unrecognized key", "children"),
        ("rename", "name"),
        ("reorder", "ordering"),
        ("re-parent", "parent_id"),
        ("change course", "course_key"),
        ("change competency", "tag_id"),
        ("archive", "archived"),
        ("change id", "id"),
    ],
)
def test_update_group_rejects_a_malformed_body_with_400(  # pylint: disable=too-many-positional-arguments
    case: str,
    error_key: str,
    user_client: APIClient,
    tag: Tag,
    leaf: CompetencyCriteriaGroup,
    course_level: CompetencyCriteriaGroup,
) -> None:
    """Each malformed body is a 400 naming the offending field, and the group is unchanged."""
    change = {"logic_operator": LogicOperator.OR}
    bodies: dict[str, dict[str, Any]] = {
        "missing operator": {},
        "null operator": {"logic_operator": None},
        "unsupported operator": {"logic_operator": "XOR"},
        # A group's membership is not this endpoint's to change.
        "unrecognized key": {**change, "children": [leaf.id]},
        "rename": {**change, "name": "Renamed"},
        "reorder": {**change, "ordering": course_level.ordering + 1},
        "re-parent": {**change, "parent_id": None},
        "change course": {**change, "course_key": "course-v1:Org1+Other+Run"},
        "change competency": {**change, "tag_id": tag.id + 1},
        "archive": {**change, "archived": True},
        "change id": {**change, "id": leaf.id},
    }
    before = stored_group(course_level)

    response = user_client.patch(group_detail_url(tag.id, course_level.id), bodies[case], format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert error_key in response.data
    assert stored_group(course_level) == before


def test_update_root_group_is_400(user_client: APIClient, tag: Tag, course_level: CompetencyCriteriaGroup) -> None:
    """A root group is refused with a 400 saying roots cannot be targeted, and it is unchanged."""
    root = course_level.parent
    assert root is not None
    before = stored_group(root)

    response = user_client.patch(
        group_detail_url(tag.id, root.id), {"logic_operator": LogicOperator.AND}, format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "root" in str(response.data["group_id"])
    assert stored_group(root) == before


def test_update_course_less_child_of_the_root_is_400_but_not_as_a_root(
    user_client: APIClient, tag: Tag, course_level: CompetencyCriteriaGroup,
) -> None:
    """A group with a parent but no course anywhere above it is refused for having no course, not as a root."""
    root = course_level.parent
    assert root is not None
    course_less = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, logic_operator=LogicOperator.AND)
    before = stored_group(course_less)

    response = user_client.patch(
        group_detail_url(tag.id, course_less.id), {"logic_operator": LogicOperator.OR}, format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "no course" in str(response.data["group_id"])
    assert "root" not in str(response.data["group_id"])
    assert stored_group(course_less) == before


def test_update_group_nested_below_a_leaf_is_200(
    user_client: APIClient, tag: Tag, leaf: CompetencyCriteriaGroup,
) -> None:
    """A group nested below a leaf is editable, its course coming from the nearest ancestor that has one."""
    nested = CompetencyCriteriaGroup.objects.create(tag=tag, parent=leaf, logic_operator=LogicOperator.OR)

    response = user_client.patch(
        group_detail_url(tag.id, nested.id), {"logic_operator": LogicOperator.AND}, format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["logic_operator"] == LogicOperator.AND


def test_update_group_under_a_tag_that_is_not_a_competency_is_404(
    user_client: APIClient, course_run: CourseRun,
) -> None:
    """A group under a non-competency taxonomy is a missing resource, and it is unchanged."""
    plain_tag = Tag.objects.create(taxonomy=Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1"), value="x")
    root = CompetencyCriteriaGroup.objects.create(tag=plain_tag, parent=None)
    # Shaped like any editable group, so only its taxonomy sets it apart.
    course_level = CompetencyCriteriaGroup.objects.create(
        tag=plain_tag, course=course_run, parent=root, logic_operator=LogicOperator.AND,
    )
    before = stored_group(course_level)

    # The mismatched name would be a 400 if the body were checked before the tag, as it isn't on the other endpoints.
    response = user_client.patch(
        group_detail_url(plain_tag.id, course_level.id),
        {"logic_operator": LogicOperator.OR, "name": "Renamed"},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert stored_group(course_level) == before


def test_update_group_addressed_under_another_competency_is_404(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, course_level: CompetencyCriteriaGroup,
) -> None:
    """A group addressed through a competency it doesn't belong to doesn't exist there, and is unchanged."""
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Reading Poetry")
    before = stored_group(course_level)

    response = user_client.patch(
        group_detail_url(other_tag.id, course_level.id), {"logic_operator": LogicOperator.OR}, format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert stored_group(course_level) == before


def test_update_group_that_does_not_exist_is_404(user_client: APIClient, tag: Tag) -> None:
    """An unknown group id in the URL is a missing resource."""
    response = user_client.patch(group_detail_url(tag.id, 999999), {"logic_operator": LogicOperator.OR}, format="json")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_update_archived_group_is_409(user_client: APIClient, tag: Tag, leaf: CompetencyCriteriaGroup) -> None:
    """An archived group conflicts with any edit, and stays archived and otherwise unchanged."""
    leaf.archived = True
    leaf.save()
    before = stored_group(leaf)

    response = user_client.patch(
        group_detail_url(tag.id, leaf.id), {"logic_operator": LogicOperator.AND}, format="json",
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "archived" in str(response.data["detail"])
    assert stored_group(leaf) == before


@pytest.mark.parametrize("lacks", ["course write access", "taxonomy view access"])
def test_update_group_without_permission_is_403(
    lacks: str, user_client: APIClient, tag: Tag, organization: Organization,
) -> None:
    """A caller who may not tag objects in the group's course, or may not view its taxonomy, is refused."""
    course_code = UNAUTHORIZED_MARKER if lacks == "course write access" else "Python200"
    leaf = _create_leaf_group(tag, make_course_run(organization, course_code, "Fall2026"))
    if lacks == "taxonomy view access":
        taxonomy = tag.taxonomy
        assert taxonomy is not None
        taxonomy.enabled = False
        taxonomy.save()
    before = stored_group(leaf)

    response = user_client.patch(
        group_detail_url(tag.id, leaf.id), {"logic_operator": LogicOperator.AND}, format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert stored_group(leaf) == before


@pytest.mark.parametrize("guess", ["wrong name", "right name", "wrong course_key", "wrong parent_id", "unknown group"])
def test_update_group_tells_a_caller_who_cannot_read_the_tree_nothing_about_what_is_stored(
    guess: str, user_client: APIClient, tag: Tag, course_level: CompetencyCriteriaGroup,
) -> None:
    """
    Without view access to the taxonomy, every guess is the same 403, and nothing changes.

    Were the body compared or the group looked up first, a wrong guess would be a 400 or a 404 and a right one a 403.
    """
    taxonomy = tag.taxonomy
    assert taxonomy is not None
    taxonomy.enabled = False
    taxonomy.save()
    change = {"logic_operator": LogicOperator.OR}
    bodies: dict[str, dict[str, Any]] = {
        "wrong name": {**change, "name": "Guessed"},
        "right name": {**change, "name": course_level.name},
        "wrong course_key": {**change, "course_key": "course-v1:Org1+Other+Run"},
        "wrong parent_id": {**change, "parent_id": None},
        "unknown group": change,
    }
    group_id = 999999 if guess == "unknown group" else course_level.id
    before = stored_group(course_level)

    response = user_client.patch(group_detail_url(tag.id, group_id), bodies[guess], format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert stored_group(course_level) == before


def test_update_group_from_an_unidentified_caller_is_401(
    api_client: APIClient, tag: Tag, leaf: CompetencyCriteriaGroup,
) -> None:
    """A caller the system cannot identify is refused before anything is read."""
    response = api_client.patch(group_detail_url(tag.id, leaf.id), {"logic_operator": LogicOperator.AND}, format="json")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("method", ["get", "put", "post", "delete"])
def test_group_detail_offers_only_patch(
    method: str, user_client: APIClient, tag: Tag, leaf: CompetencyCriteriaGroup,
) -> None:
    """Every method other than PATCH, PUT included, is refused as not allowed."""
    response = getattr(user_client, method)(group_detail_url(tag.id, leaf.id), {}, format="json")

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


# ==============================================================================================
# CompetencyCriterionDeleteView (#674)
# ==============================================================================================


@pytest.fixture(name="criterion")
def _criterion(
    group: CompetencyCriteriaGroup, tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile
) -> CompetencyCriterion:
    """A CompetencyCriterion directly under the root `group`, for the delete-view tests."""
    # The conftest object_tag's object_id isn't a parseable usage key, which _allow_object_level_tagging denies.
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    return CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=default_rule_profile)


def test_hard_delete_200s_with_archived_false(user_client: APIClient, criterion: CompetencyCriterion) -> None:
    """With no learner status on the criterion, the DELETE removes the row and the body says archived=false."""
    response = user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {"id": criterion.id, "archived": False}
    assert not CompetencyCriterion.objects.filter(id=criterion.id).exists()


def test_archive_200s_with_archived_true(
    user_client: APIClient, user, criterion: CompetencyCriterion, now: datetime,
) -> None:
    """With a learner status on the criterion, the DELETE keeps the row, flags it archived, and says archived=true."""
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    response = user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {"id": criterion.id, "archived": True}
    criterion.refresh_from_db()
    assert criterion.archived is True


def test_repeat_delete_on_an_archived_criterion_is_idempotent_200(
    user_client: APIClient, user, criterion: CompetencyCriterion, now: datetime,
) -> None:
    """A second DELETE against an already-archived criterion still 200s with the same body."""
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion.id))

    response = user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {"id": criterion.id, "archived": True}


def test_unknown_criterion_id_404s(user_client: APIClient, tag: Tag) -> None:
    """A criterion_id that never existed under a real tag 404s from the view's own lookup."""
    response = user_client.delete(criterion_delete_url(tag.id, 999999))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_repeat_delete_after_hard_delete_404s(
    user_client: APIClient, criterion: CompetencyCriterion,
) -> None:
    """Repeating a DELETE after a hard-delete removed the row is not idempotent like the archived case: it 404s."""
    url = criterion_delete_url(criterion.group.tag_id, criterion.id)
    first = user_client.delete(url)
    assert first.status_code == status.HTTP_200_OK

    second = user_client.delete(url)

    assert second.status_code == status.HTTP_404_NOT_FOUND


def test_cascade_deleted_group_404s_on_subsequent_create(
    user_client: APIClient, tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Hard-deleting a group's only criterion cascade-deletes that now-empty leaf group, and a later
    create-criterion POST naming that same (now-gone) group_id 404s rather than resolving.
    """
    leaf = _create_leaf_group(tag, course_run)
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    criterion = CompetencyCriterion.objects.create(
        group=leaf, object_tag=object_tag, rule_profile=default_rule_profile,
    )

    delete_response = user_client.delete(criterion_delete_url(tag.id, criterion.id))
    assert delete_response.status_code == status.HTTP_200_OK
    assert not CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()

    create_response = user_client.post(
        criterion_create_url(tag.id),
        {"object_id": usage_key(course_run, "p2"), "group_id": leaf.id},
        format="json",
    )

    assert create_response.status_code == status.HTTP_404_NOT_FOUND


def test_tag_id_criterion_id_mismatch_404s(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, criterion: CompetencyCriterion,
) -> None:
    """A criterion that exists, but under a different competency tag than the URL names, 404s."""
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Other Competency")

    response = user_client.delete(criterion_delete_url(other_tag.id, criterion.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_delete_no_permission_is_403(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, tag: Tag, group: CompetencyCriteriaGroup,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A caller refused by the object-level tagging rule gets a 403, and the criterion and ObjectTag remain."""
    object_tag = ObjectTag.objects.create(
        object_id=f"block-v1:unused+{UNAUTHORIZED_MARKER}+unused+type@sequential+block@p1",
        taxonomy=competency_taxonomy,
        tag=tag,
    )
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile,
    )

    response = user_client.delete(criterion_delete_url(tag.id, criterion.id))

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert CompetencyCriterion.objects.filter(id=criterion.id).exists()
    assert ObjectTag.objects.filter(id=object_tag.id).exists()


def test_delete_without_view_access_to_the_taxonomy_is_403(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, criterion: CompetencyCriterion,
) -> None:
    """A disabled taxonomy refuses an ordinary caller even when the object id itself is permitted."""
    competency_taxonomy.enabled = False
    competency_taxonomy.save()

    response = user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion.id))

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert CompetencyCriterion.objects.filter(id=criterion.id).exists()
    assert ObjectTag.objects.filter(id=criterion.object_tag_id).exists()


def test_delete_of_a_criterion_gone_by_the_time_of_the_call_is_404(
    user_client: APIClient, criterion: CompetencyCriterion,
) -> None:
    """A criterion removed between the view's lookup and the API call is a 404, not a 500."""
    with mock.patch(
        "openedx_learning.applets.cbe.rest_api.v1.views.delete_or_archive_competency_criterion",
        side_effect=CompetencyCriterion.DoesNotExist,
    ):
        response = user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND
