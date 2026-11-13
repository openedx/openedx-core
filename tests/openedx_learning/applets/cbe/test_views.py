"""
Tests for the CBE REST API views.

Fixtures live in this directory's conftest.py. Several scenarios here create rule profile rows
directly rather than through a live call, because no create or archive endpoint exists yet.
"""
from datetime import datetime
from typing import Any, Generator
from unittest import mock
from urllib.parse import urlencode

import pytest
import rules
from django.core.exceptions import ValidationError
from django.urls import reverse
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from organizations.models import Organization
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from openedx_catalog.models import CourseRun
from openedx_learning.applets.cbe import api as cbe_api
from openedx_learning.applets.cbe.api import _create_leaf_group
from openedx_learning.applets.cbe.rest_api.paginators import CompetencyBlockedCoursesPagination
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    LogicOperator,
    MasteryStatus,
    RuleType,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriterionStatus,
)
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

from .utils import make_course_run, make_leaf_criterion, make_skill_family, make_tag, usage_key

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


def group_delete_url(tag_id: int, group_id: int) -> str:
    """Return the group detail endpoint's path for `tag_id`/`group_id`, which DELETE is sent to."""
    return reverse("cbe:criteria-group-detail", kwargs={"tag_id": tag_id, "group_id": group_id})


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


# course_keys scoping


def test_course_keys_scopes_and_orders_the_response_over_a_real_request(
    user_client: APIClient,
    tag: Tag,
    course_run: CourseRun,
    organization: Organization,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A real GET request scoped to two of three course runs returns only those two courses'
    groups and criteria, in the order requested, with nothing from the third course run. This
    covers the scoping and ordering behavior end to end (view, permission, serializers), not
    just at the get_competency_criteria_tree() function level.
    """
    course_b = make_course_run(organization, "Python200", "Fall2026")
    course_c = make_course_run(organization, "Python300", "Fall2026")
    leaf_a = _create_leaf_group(tag, course_run)
    leaf_b = _create_leaf_group(tag, course_b)
    leaf_c = _create_leaf_group(tag, course_c)
    for leaf, run, name in [(leaf_a, course_run, "a"), (leaf_b, course_b, "b"), (leaf_c, course_c, "c")]:
        object_tag = ObjectTag.objects.create(object_id=usage_key(run, name), taxonomy=tag.taxonomy, tag=tag)
        CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, rule_profile=default_rule_profile)
    assert course_run.course_key is not None
    assert course_b.course_key is not None
    b_group_ids = {leaf_b.id, leaf_b.parent_id}
    a_group_ids = {leaf_a.id, leaf_a.parent_id}
    c_group_ids = {leaf_c.id, leaf_c.parent_id}

    response = user_client.get(
        criteria_tree_url(tag.id), {"course_keys": f"{course_b.course_key},{course_run.course_key}"},
    )

    assert response.status_code == status.HTTP_200_OK
    returned_group_ids = {group["id"] for group in response.data["groups"]}
    assert not returned_group_ids & c_group_ids
    b_positions = [i for i, g in enumerate(response.data["groups"]) if g["id"] in b_group_ids]
    a_positions = [i for i, g in enumerate(response.data["groups"]) if g["id"] in a_group_ids]
    assert max(b_positions) < min(a_positions)
    assert response.data["criteria_count"] == 2
    assert response.data["total_criteria_count"] == 3


def test_course_keys_with_a_trailing_comma_matches_the_clean_list(
    user_client: APIClient, tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A trailing comma in course_keys is ignored: the response is identical to the clean list."""
    leaf = _create_leaf_group(tag, course_run)
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, rule_profile=default_rule_profile)
    assert course_run.course_key is not None
    key = str(course_run.course_key)

    clean = user_client.get(criteria_tree_url(tag.id), {"course_keys": key})
    trailing_comma = user_client.get(criteria_tree_url(tag.id), {"course_keys": f"{key},"})

    assert clean.status_code == trailing_comma.status_code == status.HTTP_200_OK
    assert trailing_comma.data == clean.data


def test_course_keys_with_a_doubled_comma_matches_the_clean_list(
    user_client: APIClient, tag: Tag, course_run: CourseRun, organization: Organization,
) -> None:
    """A doubled comma between two course_keys is ignored: the response is identical to the clean list."""
    other_course_run = make_course_run(organization, "Python200", "Fall2026")
    _create_leaf_group(tag, course_run)
    _create_leaf_group(tag, other_course_run)
    assert course_run.course_key is not None
    assert other_course_run.course_key is not None
    key_a = str(course_run.course_key)
    key_b = str(other_course_run.course_key)

    clean = user_client.get(criteria_tree_url(tag.id), {"course_keys": f"{key_a},{key_b}"})
    doubled_comma = user_client.get(criteria_tree_url(tag.id), {"course_keys": f"{key_a},,{key_b}"})

    assert clean.status_code == doubled_comma.status_code == status.HTTP_200_OK
    assert doubled_comma.data == clean.data


def test_course_keys_of_only_commas_behaves_like_an_empty_scope(
    user_client: APIClient, tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A course_keys value of only commas behaves like an omitted/empty scope: only the root group."""
    leaf = _create_leaf_group(tag, course_run)
    course_level = leaf.parent
    assert course_level is not None
    root = course_level.parent
    assert root is not None
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, rule_profile=default_rule_profile)

    response = user_client.get(criteria_tree_url(tag.id), {"course_keys": ",,,"})

    assert response.status_code == status.HTTP_200_OK
    assert {group["id"] for group in response.data["groups"]} == {root.id}
    assert response.data["criteria"] == []
    assert response.data["criteria_count"] == 0
    assert response.data["total_criteria_count"] == 1


def test_a_malformed_course_key_is_400(user_client: APIClient, tag: Tag) -> None:
    """An entry in course_keys that isn't a valid course key 400s, with the error keyed by course_keys."""
    response = user_client.get(criteria_tree_url(tag.id), {"course_keys": "not-a-course-key"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(response.data) == {"course_keys"}


def test_a_version_only_course_key_is_400(user_client: APIClient, tag: Tag) -> None:
    """A version-only course key parses but can't drop its version, so it is rejected like a malformed one."""
    version_only = "course-v1:version@aaaaaaaaaaaaaaaaaaaaaaaa"
    response = user_client.get(criteria_tree_url(tag.id), {"course_keys": version_only})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(response.data) == {"course_keys"}


@pytest.mark.parametrize("library_key", ["library-v1:Org1+Lib1", "library-v1:version@aaaaaaaaaaaaaaaaaaaaaaaa"])
def test_a_library_key_is_400(user_client: APIClient, tag: Tag, library_key: str) -> None:
    """A library key parses as a CourseKey but can never name a course run, so it is rejected like a malformed one."""
    response = user_client.get(criteria_tree_url(tag.id), {"course_keys": library_key})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert library_key in str(response.data["course_keys"])


def test_more_than_the_limit_of_course_keys_is_400(user_client: APIClient, tag: Tag) -> None:
    """More than 100 course_keys entries 400s, keyed by course_keys, and the error names the 100 limit."""
    too_many = ",".join(f"course-v1:Org+Course{i}+Run" for i in range(101))

    response = user_client.get(criteria_tree_url(tag.id), {"course_keys": too_many})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(response.data) == {"course_keys"}
    assert "100" in str(response.data)


def test_exactly_the_limit_of_course_keys_is_200(user_client: APIClient, tag: Tag) -> None:
    """Exactly 100 distinct, well-formed course_keys entries is accepted."""
    at_limit = ",".join(f"course-v1:Org+Course{i}+Run" for i in range(100))

    response = user_client.get(criteria_tree_url(tag.id), {"course_keys": at_limit})

    assert response.status_code == status.HTTP_200_OK


def test_repeating_one_course_key_past_the_limit_is_400(user_client: APIClient, tag: Tag) -> None:
    """101 copies of one key 400s: the cap counts entries before duplicates are removed."""
    repeated = ",".join(["course-v1:Org+Course+Run"] * 101)

    response = user_client.get(criteria_tree_url(tag.id), {"course_keys": repeated})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(response.data) == {"course_keys"}


def test_a_repeated_course_keys_query_parameter_is_400(user_client: APIClient, tag: Tag) -> None:
    """Supplying course_keys twice 400s, keyed by course_keys, instead of silently using the last value."""
    query = urlencode({"course_keys": ["course-v1:Org+A+Run", "course-v1:Org+B+Run"]}, doseq=True)

    response = user_client.get(f"{criteria_tree_url(tag.id)}?{query}")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(response.data) == {"course_keys"}


@pytest.mark.parametrize("suffix", [
    "+branch@draft",
    "+branch@draft+version@aaaaaaaaaaaaaaaaaaaaaaaa",
    "+version@aaaaaaaaaaaaaaaaaaaaaaaa",
])
def test_a_branch_qualified_course_key_matches_the_plain_course(
    user_client: APIClient,
    tag: Tag,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
    suffix: str,
) -> None:
    """A course key with a branch or version scopes the tree exactly as the plain course key does."""
    leaf = _create_leaf_group(tag, course_run)
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, rule_profile=default_rule_profile)
    key = str(course_run.course_key)

    plain = user_client.get(criteria_tree_url(tag.id), {"course_keys": key})
    branched = user_client.get(criteria_tree_url(tag.id), {"course_keys": f"{key}{suffix}"})

    assert plain.status_code == branched.status_code == status.HTTP_200_OK
    assert plain.data["criteria_count"] == 1
    assert branched.data == plain.data


def test_no_view_access_with_course_keys_still_403s_without_leaking_data(
    api_client: APIClient, user, tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    A 403 for missing view access discloses neither array nor either count, even with garbage
    in course_keys: permission is checked before course_keys is parsed.
    """
    leaf = _create_leaf_group(tag, course_run)
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, rule_profile=default_rule_profile)
    taxonomy = tag.taxonomy
    assert taxonomy is not None
    taxonomy.enabled = False
    taxonomy.save()
    api_client.force_authenticate(user=user)

    response = api_client.get(criteria_tree_url(tag.id), {"course_keys": "not-a-course-key"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "groups" not in response.data
    assert "criteria" not in response.data
    assert "criteria_count" not in response.data
    assert "total_criteria_count" not in response.data


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


@pytest.mark.parametrize("method", ["get", "put", "post"])
def test_group_detail_offers_only_patch_and_delete(
    method: str, user_client: APIClient, tag: Tag, leaf: CompetencyCriteriaGroup,
) -> None:
    """Every method other than PATCH and DELETE, PUT included, is refused as not allowed."""
    response = getattr(user_client, method)(group_detail_url(tag.id, leaf.id), {}, format="json")

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


# ==============================================================================================
# CompetencyBlockedCoursesView
# ==============================================================================================


def blocked_courses_url(tag_id: int, **params: str) -> str:
    """Return the blocked-courses endpoint's path for `tag_id`, with `params` as its query string."""
    url = reverse("cbe:blocked-courses", kwargs={"tag_id": tag_id})
    return f"{url}?{urlencode(params)}" if params else url


def collect_pages(client: APIClient, url: str) -> tuple[int, list[list[dict]]]:
    """Follow `next` links from `url` and return the first page's count and each page's rows."""
    response = client.get(url)
    assert response.status_code == status.HTTP_200_OK
    count = response.data["count"]
    pages = [response.data["results"]]
    while response.data["next"]:
        response = client.get(response.data["next"])
        assert response.status_code == status.HTTP_200_OK
        pages.append(response.data["results"])
    return count, pages


def test_blocked_courses_reports_a_relative_claim_with_the_row_shape(
    user_client: APIClient,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A course an ancestor has a criterion in is reported in a paginated envelope, naming that ancestor."""
    _, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    make_leaf_criterion(critical_thinking, course_run, default_rule_profile, "p1")

    response = user_client.get(blocked_courses_url(inference.id))

    assert response.status_code == status.HTTP_200_OK
    # The seven keys are the standard envelope of edx-platform ADR 0032, which DefaultPagination implements.
    assert set(response.data) == {"next", "previous", "count", "num_pages", "current_page", "start", "results"}
    assert response.data["results"] == [{
        "course_key": "course-v1:Org1+Python100+Fall2026",
        "conflicting_tag_id": critical_thinking.id,
        "conflicting_tag_value": "Critical Thinking",
    }]


def test_blocked_courses_ignores_an_unaffected_course(
    user_client: APIClient,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    organization: Organization,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A course with no relative's criterion is not reported, even when another course is blocked."""
    _, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    blocked = course_run
    unaffected = make_course_run(organization, "Python200", "Fall2026")
    make_leaf_criterion(critical_thinking, blocked, default_rule_profile, "p1")

    response = user_client.get(blocked_courses_url(inference.id))

    assert response.status_code == status.HTTP_200_OK
    keys = [row["course_key"] for row in response.data["results"]]
    assert keys == [str(blocked.course_key)]
    assert str(unaffected.course_key) not in keys


def test_blocked_courses_narrows_to_requested_course_keys(
    user_client: APIClient,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    organization: Organization,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Only the requested courses come back, though a third course is blocked too."""
    cognitive_skills, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    course_y = make_course_run(organization, "Python200", "Fall2026")
    course_z = make_course_run(organization, "Python300", "Fall2026")
    make_leaf_criterion(critical_thinking, course_run, default_rule_profile, "p1")
    make_leaf_criterion(cognitive_skills, course_y, default_rule_profile, "p1")
    make_leaf_criterion(critical_thinking, course_z, default_rule_profile, "p1")

    response = user_client.get(
        blocked_courses_url(inference.id, course_keys=f"{course_run.course_key},{course_y.course_key}"),
    )

    assert response.status_code == status.HTTP_200_OK
    assert {row["course_key"] for row in response.data["results"]} == {
        str(course_run.course_key), str(course_y.course_key),
    }


def test_blocked_courses_is_an_empty_200_when_nothing_is_claimed(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """No relative has a criterion anywhere: an empty collection, not a failure."""
    _, _, inference, _ = make_skill_family(competency_taxonomy)

    response = user_client.get(blocked_courses_url(inference.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 0
    assert response.data["results"] == []


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            (
                ("C300", "A100", "B200"),
                {},
                ["course-v1:Org1+A100+Run1", "course-v1:Org1+B200+Run1", "course-v1:Org1+C300+Run1"],
            ),
            id="unfiltered",
        ),
        pytest.param(
            (
                ("C300", "A100", "D400", "B200"),
                {"course_keys": "course-v1:Org1+D400+Run1,course-v1:Org1+A100+Run1,course-v1:Org1+C300+Run1"},
                ["course-v1:Org1+A100+Run1", "course-v1:Org1+C300+Run1", "course-v1:Org1+D400+Run1"],
            ),
            id="narrowed",
        ),
    ],
)
def test_blocked_courses_paginates_without_repeats_or_gaps(
    user_client: APIClient,
    competency_taxonomy: CompetencyTaxonomy,
    organization: Organization,
    default_rule_profile: CompetencyRuleProfile,
    scenario: tuple[tuple[str, ...], dict[str, str], list[str]],
) -> None:
    """
    Three courses at two per page arrive across two pages, each exactly once, in course key order,
    whether they are every blocked course or the ones ``course_keys`` narrows the result to.
    """
    course_codes, params, expected = scenario
    _, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    for course_code in course_codes:
        course = make_course_run(organization, course_code, "Run1")
        make_leaf_criterion(critical_thinking, course, default_rule_profile, "p1")
    count, pages = collect_pages(user_client, blocked_courses_url(inference.id, page_size="2", **params))

    assert count == 3
    assert [len(page) for page in pages] == [2, 1]
    assert [row["course_key"] for page in pages for row in page] == expected


def test_blocked_courses_default_page_size_is_100(
    user_client: APIClient,
    competency_taxonomy: CompetencyTaxonomy,
    organization: Organization,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Without page_size, a page holds 100 rows and the 101st is on the next page."""
    _, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    for i in range(101):
        course = make_course_run(organization, f"C{i}", "Run1")
        make_leaf_criterion(critical_thinking, course, default_rule_profile, "p1")

    response = user_client.get(blocked_courses_url(inference.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 101
    assert len(response.data["results"]) == 100
    assert response.data["next"] is not None


def test_blocked_courses_page_size_is_capped_at_500() -> None:
    """A page_size above the cap is clamped to 500 rather than honored or rejected."""
    request = Request(APIRequestFactory().get("/", {"page_size": "1000"}))

    assert CompetencyBlockedCoursesPagination().get_page_size(request) == 500


def test_blocked_courses_page_out_of_range_404s(
    user_client: APIClient,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A page past the last one is reported as not found."""
    _, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    make_leaf_criterion(critical_thinking, course_run, default_rule_profile, "p1")

    response = user_client.get(blocked_courses_url(inference.id, page="999"))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_blocked_courses_unknown_tag_404s(user_client: APIClient) -> None:
    """A tag_id that doesn't resolve is reported as not found."""
    response = user_client.get(blocked_courses_url(999999))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_blocked_courses_tag_outside_a_competency_taxonomy_404s(user_client: APIClient) -> None:
    """A real Tag that isn't on a CompetencyTaxonomy is reported as not found."""
    plain_taxonomy = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1")
    plain_tag = Tag.objects.create(taxonomy=plain_taxonomy, value="Not A Competency")

    response = user_client.get(blocked_courses_url(plain_tag.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.parametrize(
    "query",
    [
        pytest.param(urlencode({"course_keys": "not-a-key"}), id="malformed course_keys"),
        pytest.param(urlencode([("course_keys", "a"), ("course_keys", "b")]), id="repeated course_keys"),
    ],
)
def test_blocked_courses_without_view_access_403s(
    user_client: APIClient,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
    query: str,
) -> None:
    """
    A non-staff caller may not read blocked courses for a competency on a disabled taxonomy, even
    with garbage in course_keys: permission is checked before course_keys is counted or parsed.
    """
    _, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    make_leaf_criterion(critical_thinking, course_run, default_rule_profile, "p1")
    competency_taxonomy.enabled = False
    competency_taxonomy.save()

    response = user_client.get(f"{blocked_courses_url(inference.id)}?{query}")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "results" not in response.data


def test_blocked_courses_anonymous_request_is_401_and_discloses_nothing(
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """An unauthenticated caller gets 401 and no rows, even for a competency with a blocked course."""
    _, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    make_leaf_criterion(critical_thinking, course_run, default_rule_profile, "p1")

    response = APIClient().get(blocked_courses_url(inference.id))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "results" not in response.data


def test_blocked_courses_malformed_course_key_400s(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """An unparseable course key is rejected, and the error names it."""
    _, _, inference, _ = make_skill_family(competency_taxonomy)

    response = user_client.get(blocked_courses_url(inference.id, course_keys="not-a-key"))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "not-a-key" in str(response.data["course_keys"])


@pytest.mark.parametrize("course_keys", ["", ",,"])
def test_blocked_courses_blank_course_keys_is_an_empty_page(
    user_client: APIClient,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
    course_keys: str,
) -> None:
    """A course_keys filter that names no course narrows to nothing, even when a course is blocked."""
    _, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    make_leaf_criterion(critical_thinking, course_run, default_rule_profile, "p1")

    response = user_client.get(blocked_courses_url(inference.id, course_keys=course_keys))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 0
    assert response.data["results"] == []


def test_blocked_courses_repeated_course_keys_parameter_400s(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """Supplying course_keys twice is rejected instead of silently using one value."""
    _, _, inference, _ = make_skill_family(competency_taxonomy)
    query = urlencode([("course_keys", "course-v1:Org+A+1"), ("course_keys", "course-v1:Org+B+1")])

    response = user_client.get(f"{blocked_courses_url(inference.id)}?{query}")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "results" not in response.data


def test_blocked_courses_strips_whitespace_around_course_keys(
    user_client: APIClient,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    organization: Organization,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Spaces after the commas don't make a key unparseable."""
    _, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    course_y = make_course_run(organization, "Python200", "Fall2026")
    make_leaf_criterion(critical_thinking, course_run, default_rule_profile, "p1")
    make_leaf_criterion(critical_thinking, course_y, default_rule_profile, "p1")

    response = user_client.get(
        blocked_courses_url(inference.id, course_keys=f"{course_run.course_key}, {course_y.course_key}"),
    )

    assert response.status_code == status.HTTP_200_OK
    assert {row["course_key"] for row in response.data["results"]} == {
        str(course_run.course_key), str(course_y.course_key),
    }


def test_blocked_courses_ignores_an_archived_claim(
    user_client: APIClient,
    competency_taxonomy: CompetencyTaxonomy,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A relative's archived criterion doesn't block the course."""
    _, critical_thinking, inference, _ = make_skill_family(competency_taxonomy)
    criterion = make_leaf_criterion(critical_thinking, course_run, default_rule_profile, "p1")
    CompetencyCriterion.objects.filter(pk=criterion.pk).update(archived=True)

    response = user_client.get(blocked_courses_url(inference.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["results"] == []


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


@pytest.mark.parametrize("hard_delete_first", [False, True], ids=["never-existed", "already-hard-deleted"])
def test_delete_of_a_criterion_that_is_not_there_404s(
    hard_delete_first: bool, user_client: APIClient, criterion: CompetencyCriterion,
) -> None:
    """
    A criterion_id that never existed, or whose row an earlier hard delete removed, 404s from the view's
    own lookup. Repeating a hard delete is not idempotent like repeating an archive.
    """
    criterion_id = 999999
    if hard_delete_first:
        criterion_id = criterion.id
        assert user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion_id)).status_code == 200

    response = user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion_id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


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


@pytest.mark.parametrize("lacks", ["object tagging access", "taxonomy view access"])
def test_delete_without_permission_is_403(
    *,
    lacks: str, user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, tag: Tag,
    group: CompetencyCriteriaGroup, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A caller refused by the object-level tagging rule, or without view access to the taxonomy, gets a 403."""
    block_id = UNAUTHORIZED_MARKER if lacks == "object tagging access" else "p1"
    object_tag = ObjectTag.objects.create(
        object_id=usage_key(course_run, block_id), taxonomy=competency_taxonomy, tag=tag,
    )
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile,
    )
    if lacks == "taxonomy view access":
        competency_taxonomy.enabled = False
        competency_taxonomy.save()

    response = user_client.delete(criterion_delete_url(tag.id, criterion.id))

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert CompetencyCriterion.objects.filter(id=criterion.id).exists()


def test_delete_from_an_unidentified_caller_is_401(api_client: APIClient, criterion: CompetencyCriterion) -> None:
    """A caller the system cannot identify is refused before anything is read, and the criterion remains."""
    response = api_client.delete(criterion_delete_url(criterion.group.tag_id, criterion.id))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert CompetencyCriterion.objects.filter(id=criterion.id).exists()


def test_a_grade_that_lands_during_the_delete_is_a_retryable_409(
    monkeypatch: pytest.MonkeyPatch, user_client: APIClient, user, criterion: CompetencyCriterion, now: datetime,
) -> None:
    """
    A learner's status row committed after the delete's progress check makes the delete raise ProtectedError.

    The view answers a 409 that tells the caller to retry, and the criterion is neither deleted nor archived.
    """

    def progress_arrives_after_the_check(criterion_id: int) -> bool:
        StudentCompetencyCriterionStatus.objects.create(
            user=user, criterion_id=criterion_id, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
        )
        return False

    monkeypatch.setattr(cbe_api, "_criterion_has_learner_progress", progress_arrives_after_the_check)

    response = user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion.id))

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "Retry the DELETE" in str(response.data["detail"])
    criterion.refresh_from_db()
    assert criterion.archived is False


def test_delete_of_a_criterion_gone_by_the_time_of_the_call_is_404(
    run_when_tag_lock_is_taken, user_client: APIClient, criterion: CompetencyCriterion,
) -> None:
    """A criterion removed between the view's lookup and the API's locked re-read is a 404, not a 500."""
    run_when_tag_lock_is_taken(lambda: CompetencyCriterion.objects.filter(pk=criterion.pk).delete())

    response = user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_archiving_a_criterion_whose_stored_payload_went_invalid_is_400_and_changes_nothing(
    user_client: APIClient, user, batch: tuple[CompetencyCriterion, CompetencyCriterion], now: datetime,
) -> None:
    """
    Archiving saves the criterion, which re-validates its rule payload. A payload that stopped being
    valid after it was stored, here written past validation with update(), makes the DELETE a 400
    keyed by the model-level error, and the criterion is neither archived nor deleted.
    """
    criterion = batch[1]
    CompetencyCriterion.objects.filter(pk=criterion.pk).update(rule_payload_override={"op": "gt", "value": 75})
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    response = user_client.delete(criterion_delete_url(criterion.group.tag_id, criterion.id))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert list(response.json()) == ["__all__"]
    criterion.refresh_from_db()
    assert criterion.archived is False


@pytest.mark.parametrize("endpoint", ["delete a criterion", "bulk update criteria", "update a group", "delete a group"])
def test_a_competency_tag_deleted_while_the_request_waits_for_its_lock_is_404(
    endpoint: str,
    run_when_tag_lock_is_taken,
    user_client: APIClient,
    leaf: CompetencyCriteriaGroup,
    batch: tuple[CompetencyCriterion, CompetencyCriterion],
) -> None:
    """
    A competency tag removed by an admin while the request waits for the Tag lock is a 404, not a 500.
    The group's foreign key to the tag cascades, so the row can't go before the request starts without
    taking the criterion with it; the delete runs as the lock is taken, as a commit during the wait would.
    """
    run_when_tag_lock_is_taken(lambda: Tag.objects.filter(pk=leaf.tag_id).delete())

    if endpoint == "delete a criterion":
        response = user_client.delete(criterion_delete_url(leaf.tag_id, batch[0].id))
    elif endpoint == "bulk update criteria":
        body = {"criterion_ids": [criterion.id for criterion in batch], **NEW_RULE}
        response = user_client.patch(bulk_update_url(leaf.id), body, format="json")
    elif endpoint == "update a group":
        response = user_client.patch(
            group_detail_url(leaf.tag_id, leaf.id), {"logic_operator": LogicOperator.AND}, format="json",
        )
    else:
        response = user_client.delete(group_delete_url(leaf.tag_id, leaf.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ==============================================================================================
# CompetencyCriteriaGroupDetailView DELETE (#675)
# ==============================================================================================


def test_group_hard_delete_200s_then_repeat_delete_404s(
    user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """
    Deleting an empty leaf group 200s, hard-deletes it, and reports zero cascade counts; a
    repeat DELETE then 404s.
    """
    leaf = _create_leaf_group(tag, course_run)
    url = group_delete_url(tag.id, leaf.id)

    response = user_client.delete(url)

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        "id": leaf.id, "archived": False, "cascaded_group_count": 0, "cascaded_criteria_count": 0,
    }
    assert not CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()

    repeat = user_client.delete(url)

    assert repeat.status_code == status.HTTP_404_NOT_FOUND


def test_group_cascade_hard_delete_200s_then_child_group_id_404s_on_subsequent_create(
    user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """
    Hard-deleting a course-level group cascade-deletes its child leaf group too, and a later
    create-criterion POST naming that now-gone child group_id 404s rather than resolving.
    """
    leaf = _create_leaf_group(tag, course_run)
    course_level = leaf.parent
    assert course_level is not None

    response = user_client.delete(group_delete_url(tag.id, course_level.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["archived"] is False
    assert response.data["cascaded_group_count"] == 1
    assert response.data["cascaded_criteria_count"] == 0
    assert not CompetencyCriteriaGroup.objects.filter(id=leaf.id).exists()

    create_response = user_client.post(
        criterion_create_url(tag.id),
        {"object_id": usage_key(course_run, "p1"), "group_id": leaf.id},
        format="json",
    )

    assert create_response.status_code == status.HTTP_404_NOT_FOUND


def test_group_archive_200s_with_archived_true(
    user_client: APIClient, *, user, tag: Tag, course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """
    Deleting a group whose criterion a learner status row references 200s, archives the
    subtree, and reports archived=true.
    """
    leaf = _create_leaf_group(tag, course_run)
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    criterion = CompetencyCriterion.objects.create(
        group=leaf, object_tag=object_tag, rule_profile=default_rule_profile,
    )
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    response = user_client.delete(group_delete_url(tag.id, leaf.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        "id": leaf.id, "archived": True, "cascaded_group_count": 0, "cascaded_criteria_count": 1,
    }
    leaf.refresh_from_db()
    assert leaf.archived is True


def test_group_repeat_delete_on_an_archived_group_is_idempotent_200(
    user_client: APIClient, *, user, tag: Tag, course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile, now: datetime,
) -> None:
    """A second DELETE against an already-archived group subtree still 200s with the same body."""
    leaf = _create_leaf_group(tag, course_run)
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, "p1"), taxonomy=tag.taxonomy, tag=tag)
    criterion = CompetencyCriterion.objects.create(
        group=leaf, object_tag=object_tag, rule_profile=default_rule_profile,
    )
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )
    url = group_delete_url(tag.id, leaf.id)
    user_client.delete(url)

    response = user_client.delete(url)

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        "id": leaf.id, "archived": True, "cascaded_group_count": 0, "cascaded_criteria_count": 1,
    }


def test_group_unknown_group_id_404s(user_client: APIClient, tag: Tag) -> None:
    """A group_id with no matching row 404s."""
    response = user_client.delete(group_delete_url(tag.id, 999999))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_group_tag_id_mismatch_404s(
    user_client: APIClient, competency_taxonomy: CompetencyTaxonomy, tag: Tag, course_run: CourseRun,
) -> None:
    """A group that exists, but under a different competency tag than the URL names, 404s."""
    leaf = _create_leaf_group(tag, course_run)
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Other Competency")

    response = user_client.delete(group_delete_url(other_tag.id, leaf.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_group_delete_no_permission_is_403(
    user_client: APIClient, tag: Tag, organization: Organization, object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A caller without tagging permission on the subtree's course gets a 403, and the subtree survives."""
    unauthorized_course_run = make_course_run(organization, f"{UNAUTHORIZED_MARKER}100", "Fall2026")
    leaf = _create_leaf_group(tag, unauthorized_course_run)
    course_level = leaf.parent
    assert course_level is not None
    criterion = CompetencyCriterion.objects.create(
        group=leaf, object_tag=object_tag, rule_profile=default_rule_profile,
    )

    response = user_client.delete(group_delete_url(tag.id, leaf.id))

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert CompetencyCriteriaGroup.objects.filter(id__in=[leaf.id, course_level.id], archived=False).count() == 2
    assert CompetencyCriterion.objects.filter(id=criterion.id, archived=False).exists()
    assert ObjectTag.objects.filter(id=object_tag.id).exists()


def test_group_delete_without_taxonomy_access_is_403_and_changes_nothing(
    user_client: APIClient, leaf: CompetencyCriteriaGroup, batch: tuple[CompetencyCriterion, CompetencyCriterion],
) -> None:
    """
    A caller who may tag the course but lacks ``oel_tagging.change_objecttag_taxonomy`` for the
    competency's taxonomy gets a 403, and the subtree is untouched. This check is the only one that can
    refuse here, because CompetencyReadPermission does not run on DELETE.
    """
    course_level = leaf.parent
    assert course_level is not None
    perm = "oel_tagging.change_objecttag_taxonomy"
    original = rules.permissions.permissions[perm]
    rules.set_perm(perm, rules.always_deny)
    try:
        response = user_client.delete(group_delete_url(leaf.tag_id, leaf.id))
    finally:
        rules.set_perm(perm, original)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert CompetencyCriteriaGroup.objects.filter(id__in=[leaf.id, course_level.id], archived=False).count() == 2
    assert CompetencyCriterion.objects.filter(id__in=[c.id for c in batch], archived=False).count() == 2
    assert ObjectTag.objects.filter(id__in=[c.object_tag_id for c in batch]).count() == 2


def test_archiving_a_group_with_a_criterion_whose_stored_payload_went_invalid_is_400_and_changes_nothing(
    user_client: APIClient, user, leaf: CompetencyCriteriaGroup,
    batch: tuple[CompetencyCriterion, CompetencyCriterion], now: datetime,
) -> None:
    """
    The archive path saves each criterion, which re-validates its rule payload. A payload that stopped
    being valid after it was stored, here written past validation with update(), makes the DELETE a 400,
    and nothing in the subtree is archived, including the criterion that holds the learner status.
    """
    course_level = leaf.parent
    assert course_level is not None
    CompetencyCriterion.objects.filter(pk=batch[1].pk).update(rule_payload_override={"op": "gt", "value": 75})
    StudentCompetencyCriterionStatus.objects.create(
        user=user, criterion=batch[0], status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    response = user_client.delete(group_delete_url(leaf.tag_id, leaf.id))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert list(response.json()) == ["__all__"]
    assert not CompetencyCriteriaGroup.objects.filter(id__in=[leaf.id, course_level.id], archived=True).exists()
    assert not CompetencyCriterion.objects.filter(id__in=[c.id for c in batch], archived=True).exists()


def test_group_delete_of_a_group_gone_by_the_time_of_the_call_is_404(
    user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """A group removed between the view's lookup and the API call is a 404, not a 500."""
    leaf = _create_leaf_group(tag, course_run)

    with mock.patch(
        "openedx_learning.applets.cbe.rest_api.v1.views.delete_or_archive_competency_criteria_group",
        side_effect=CompetencyCriteriaGroup.DoesNotExist,
    ):
        response = user_client.delete(group_delete_url(tag.id, leaf.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_group_delete_maps_a_validation_error_from_the_api_to_400(
    user_client: APIClient, tag: Tag, course_run: CourseRun,
) -> None:
    """A Django ValidationError raised by the API function becomes a 400 keyed by its field."""
    leaf = _create_leaf_group(tag, course_run)

    with mock.patch(
        "openedx_learning.applets.cbe.rest_api.v1.views.delete_or_archive_competency_criteria_group",
        side_effect=ValidationError({"group_id": "x"}),
    ):
        response = user_client.delete(group_delete_url(tag.id, leaf.id))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "group_id" in response.json()


def test_group_delete_whose_error_is_not_a_learner_race_on_the_subtree_is_a_retryable_409(
    user_client: APIClient, user, tag: Tag, course_run: CourseRun, now: datetime,
) -> None:
    """
    A status that reaches an ancestor group during the delete makes the DELETE fail outside the target
    subtree. The view answers a 409 telling the caller to retry, and nothing is deleted or archived.
    """
    leaf = _create_leaf_group(tag, course_run)
    course_level = leaf.parent
    assert course_level is not None
    StudentCompetencyCriteriaGroupStatus.objects.create(
        user=user, group=course_level, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
    )

    with mock.patch.object(cbe_api, "_group_has_learner_progress", return_value=False):
        response = user_client.delete(group_delete_url(tag.id, leaf.id))

    assert response.status_code == status.HTTP_409_CONFLICT
    assert str(response.data["detail"]) == "The delete conflicted with a concurrent change. Retry the DELETE."
    assert CompetencyCriteriaGroup.objects.filter(id__in=[leaf.id, course_level.id], archived=False).count() == 2


@pytest.fixture(name="checked_object_ids")
def _checked_object_ids(_allow_object_level_tagging: None) -> Generator[list[str], None, None]:
    """Record every object_id the object-level tagging predicate is asked about, then defer to it."""
    perm = "oel_tagging.change_objecttag_objectid"
    allowing = rules.permissions.permissions[perm]
    seen: list[str] = []

    def _recording_predicate(user, object_id: str) -> bool:
        seen.append(object_id)
        return allowing.test(user, object_id)

    rules.set_perm(perm, _recording_predicate)
    yield seen
    rules.set_perm(perm, allowing)


def test_group_delete_checks_permission_against_a_course_level_groups_own_course(
    user_client: APIClient, tag: Tag, course_run: CourseRun, checked_object_ids: list[str],
) -> None:
    """A course-level target group is checked against its own course's key."""
    leaf = _create_leaf_group(tag, course_run)
    course_level = leaf.parent
    assert course_level is not None

    response = user_client.delete(group_delete_url(tag.id, course_level.id))

    assert response.status_code == status.HTTP_200_OK
    assert checked_object_ids == [str(course_run.course_key)]


def test_group_delete_checks_permission_against_the_parents_course_for_a_leaf_group(
    user_client: APIClient, tag: Tag, course_run: CourseRun, checked_object_ids: list[str],
) -> None:
    """A leaf target group, which has no course of its own, is checked against its parent's course key."""
    leaf = _create_leaf_group(tag, course_run)

    response = user_client.delete(group_delete_url(tag.id, leaf.id))

    assert response.status_code == status.HTTP_200_OK
    assert checked_object_ids == [str(course_run.course_key)]


def test_group_root_target_is_400_before_any_permission_check_or_write(
    user_client: APIClient, tag: Tag, course_run: CourseRun, checked_object_ids: list[str],
) -> None:
    """
    A root group_id is rejected with a 400 that says root groups cannot be targeted, before the
    permission predicate runs, and nothing under it changes.
    """
    leaf = _create_leaf_group(tag, course_run)
    course_level = leaf.parent
    assert course_level is not None
    root = course_level.parent
    assert root is not None

    response = user_client.delete(group_delete_url(tag.id, root.id))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert [str(message) for message in response.data["group_id"]] == [
        "group_id must not reference a root CompetencyCriteriaGroup."
    ]
    assert not checked_object_ids
    assert set(
        CompetencyCriteriaGroup.objects.filter(archived=False).values_list("id", flat=True)
    ) == {root.id, course_level.id, leaf.id}


@pytest.mark.parametrize("depth", [2, 3])
def test_group_delete_checks_permission_against_the_course_level_ancestor_at_any_depth(
    user_client: APIClient, tag: Tag, course_run: CourseRun, checked_object_ids: list[str], depth: int,
) -> None:
    """A target nested `depth` levels below its course-level group is checked against that course and deleted."""
    course_level = _create_leaf_group(tag, course_run).parent
    assert course_level is not None
    target = course_level
    for _ in range(depth):
        target = CompetencyCriteriaGroup.objects.create(tag=tag, parent=target)

    response = user_client.delete(group_delete_url(tag.id, target.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["archived"] is False
    assert checked_object_ids == [str(course_run.course_key)]
    assert not CompetencyCriteriaGroup.objects.filter(id=target.id).exists()


def test_group_with_no_course_level_group_in_its_lineage_is_400_before_any_permission_check_or_write(
    user_client: APIClient, tag: Tag, checked_object_ids: list[str],
) -> None:
    """A non-root group with no course above it is invalid data: a 400, with no permission check and no write."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    middle = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    target = CompetencyCriteriaGroup.objects.create(tag=tag, parent=middle)

    response = user_client.delete(group_delete_url(tag.id, target.id))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "group_id" in response.data
    assert not checked_object_ids
    assert set(
        CompetencyCriteriaGroup.objects.filter(archived=False).values_list("id", flat=True)
    ) == {root.id, middle.id, target.id}
