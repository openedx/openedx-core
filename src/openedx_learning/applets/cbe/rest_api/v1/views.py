"""
Views for the CBE REST API, v1.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication  # type: ignore[import]
from edx_rest_framework_extensions.auth.session.authentication import (  # type: ignore[import]
    SessionAuthenticationAllowInactiveUser,
)
from rest_framework import generics, mixins, status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from openedx_tagging.models import Tag
from openedx_tagging.rules import ObjectTagPermissionItem

from ...api import (
    CompetencyCriteriaGroupArchivedError,
    CompetencyCriterionArchivedError,
    associate_competency_criterion,
    bulk_update_competency_criteria,
    get_competency_criteria_tree,
    get_competency_rule_profiles,
    resolve_competency_tag,
    update_competency_criteria_group,
)
from ...models import CompetencyCriteriaGroup, CompetencyCriterion, CompetencyRuleProfile
from ..paginators import CompetencyRuleProfilePagination
from .permissions import CompetencyReadPermission, CompetencyRuleProfilePermissions
from .serializers import (
    CompetencyCriteriaGroupSerializer,
    CompetencyCriteriaGroupUpdateSerializer,
    CompetencyCriteriaQueryParamsSerializer,
    CompetencyCriterionBulkUpdateSerializer,
    CompetencyCriterionReadSerializer,
    CompetencyCriterionSerializer,
    CompetencyRuleProfileSerializer,
)


class CompetencyRuleProfileView(mixins.ListModelMixin, GenericViewSet):
    """
    Read the rule profiles this instance defines.

    ``GET api/cbe/v1/rule_profiles/`` lists them to any signed-in caller, paginated by ``page``
    and ``page_size``; ``can_view_competency_rule_profile`` holds the gate.

    UNSTABLE: the rule profile family is incomplete, so the create, update, and archive
    endpoints still to come may change this shape without a deprecation cycle.

    This is a collection even while the seeded system default is the only profile an instance
    holds, and it is paginated from the first release: wrapping a bare array in an envelope later
    would change the top-level JSON type. A viewset rather than a ListAPIView, so the deferred
    create and detail routes can be added as further mixins without touching the URL module.
    """

    serializer_class = CompetencyRuleProfileSerializer
    permission_classes = [CompetencyRuleProfilePermissions]
    pagination_class = CompetencyRuleProfilePagination
    # Set here rather than through openedx_tagging's view_auth_classes decorator, which lives in
    # another app's REST internals rather than in an API this library publishes.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)

    def get_queryset(self) -> QuerySet[CompetencyRuleProfile]:
        """Return the live rule profiles, filtered and ordered by the applet's public API."""
        return get_competency_rule_profiles()


class CompetencyCriterionCreateView(generics.CreateAPIView):
    """
    POST-only. Create a CompetencyCriterion associating an object_id with a competency tag.

    Overrides ``create()`` rather than relying on ``ModelSerializer.save()``, because creation
    is a multi-step domain operation (tag/course resolution, permission check, duplicate check,
    group derivation, criterion creation) that :func:`associate_competency_criterion` already
    owns end-to-end -- the serializer's job here is validating/shaping the request body and
    representing the result, not constructing the instance itself.

    ``permission_classes`` is ``IsAuthenticated`` only, not a ``DjangoObjectPermissions``
    subclass: every existing such class in this codebase only ever contributes the
    authentication gate, since the ``rules`` predicate behind it returns True whenever called
    with no object (as every class-level check does). Copying that shape here would mean
    registering a new, unused ``openedx_learning.add_competencycriterion`` permission for no
    behavioral gain, so the real, object-scoped authorization is a manual ``has_perm()`` call
    below instead.
    """

    serializer_class = CompetencyCriterionSerializer
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        """Validate the request body, check permission, then delegate to the public API."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            tag = resolve_competency_tag(self.kwargs["tag_id"])
        except Tag.DoesNotExist as exc:
            raise NotFound(str(exc)) from exc
        perm_obj = ObjectTagPermissionItem(taxonomy=tag.taxonomy, object_id=data["object_id"])
        if not request.user.has_perm("oel_tagging.can_tag_object", perm_obj):
            raise PermissionDenied()
        try:
            criterion = associate_competency_criterion(
                tag_id=tag.id,
                object_id=data["object_id"],
                group_id=data.get("group_id"),
                logic_operator=data.get("logic_operator"),
                rule_profile_id=data.get("rule_profile_id"),
                rule_type_override=data.get("rule_type_override"),
                rule_payload_override=data.get("rule_payload_override"),
            )
        except (Tag.DoesNotExist, CompetencyCriteriaGroup.DoesNotExist) as exc:
            raise NotFound(str(exc)) from exc
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages) from exc
        return Response(self.get_serializer(criterion).data, status=status.HTTP_201_CREATED)


class CompetencyCriteriaConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = _("The request conflicts with the current state of the named criteria.")
    default_code = "conflict"


class CompetencyCriterionBulkUpdateView(generics.GenericAPIView):
    """
    PATCH-only. Apply one rule to a named batch of CompetencyCriteria in one leaf group, atomically.

    The body names the criteria to update, all of which must belong to the group in the URL, and
    exactly one rule source: either a live ``rule_profile_id`` to point them at, or both
    ``rule_type_override`` and ``rule_payload_override``. The response is every named criterion as
    stored, in request order, including any the update left unchanged.

    A thin adapter: :func:`bulk_update_competency_criteria` owns every semantic refusal, the
    permission check included, so an in-process caller gets the same refusals. A batch naming an
    archived criterion, or addressed to a group that is archived or sits under an archived group,
    is refused whole with 409.
    """

    serializer_class = CompetencyCriterionBulkUpdateSerializer
    http_method_names = ["patch"]
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = [IsAuthenticated]

    def patch(self, request, group_id: int):
        """Parse the request body, then delegate to the public API."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            criteria = bulk_update_competency_criteria(
                data["criterion_ids"],
                group_id,
                rule_profile_id=data.get("rule_profile_id"),
                rule_type_override=data.get("rule_type_override"),
                rule_payload_override=data.get("rule_payload_override"),
                user=request.user,
            )
        except (CompetencyCriteriaGroup.DoesNotExist, CompetencyCriterion.DoesNotExist) as exc:
            raise NotFound(str(exc)) from exc
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages) from exc
        except CompetencyCriterionArchivedError as exc:
            raise CompetencyCriteriaConflict(str(exc)) from exc
        return Response(CompetencyCriterionSerializer(criteria, many=True).data, status=status.HTTP_200_OK)


class CompetencyCriteriaTreeView(generics.GenericAPIView):
    """
    GET-only. Return a competency's CompetencyCriteriaGroup tree and CompetencyCriterion leaves.

    UNSTABLE: the criteria-authoring family is incomplete, so this response shape may change
    without a deprecation cycle.

    Only groups whose whole ancestor chain is non-archived are returned, with their non-archived
    criteria; see ``get_competency_criteria_tree``. Returned as two flat lists (groups, criteria)
    for client-side reconstruction: each group carries its parent_id, and each criterion its
    group_id. An empty tree (the tag exists but nothing has been authored under it yet) is a
    valid 200 with two empty arrays; a 404 is reserved for tag resolution failure only.

    The optional ``course_keys`` query parameter (comma-separated course keys) scopes the tree by
    course run. A course run's groups are its course-level group and every group nested below it.
    Omitting the parameter returns every reachable group. Supplying it returns the groups above
    any course plus the groups of the named runs, ordered with the groups above any course first
    and then by the order requested; keys that match no run are ignored, and an empty value
    returns only the groups above any course. The parameter may be supplied once.

    ``criteria_count`` is the number of criteria returned. ``total_criteria_count`` is the number
    under every reachable group regardless of ``course_keys``, so a client scoped down to zero
    criteria can still tell the competency is not empty.
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = [IsAuthenticated, CompetencyReadPermission]

    def get(self, request, tag_id):
        """Return the competency's criteria tree; 404 if ``tag_id`` is not a competency tag."""
        try:
            tag = resolve_competency_tag(tag_id)
        except Tag.DoesNotExist as exc:
            raise NotFound(str(exc)) from exc
        self.check_object_permissions(request, tag)
        if len(request.query_params.getlist("course_keys")) > 1:
            raise DRFValidationError({"course_keys": _("Supply course_keys once, as a comma-separated list.")})
        query_params = CompetencyCriteriaQueryParamsSerializer(data=request.query_params.dict())
        query_params.is_valid(raise_exception=True)
        course_keys = query_params.validated_data.get("course_keys")
        tree = get_competency_criteria_tree(tag, course_keys)
        return Response({
            "groups": CompetencyCriteriaGroupSerializer(tree.groups, many=True).data,
            "criteria": CompetencyCriterionReadSerializer(tree.criteria, many=True).data,
            "criteria_count": tree.criteria_count,
            "total_criteria_count": tree.total_criteria_count,
        })


class CompetencyCriteriaGroupDetailView(generics.GenericAPIView):
    """
    PATCH-only. Change how one CompetencyCriteriaGroup combines the criteria and groups beneath it.

    **Example Request**
        PATCH api/cbe/v1/competencies/<tag_id>/criteria-groups/<group_id>/
        ``{"logic_operator": "AND"}``

    The response is the group as stored, in the criteria-tree endpoint's shape. ``logic_operator``
    is the only editable field. The body may also repeat any other field the group's representation
    carries, so a client can send back what it read, as long as each one matches what is stored.

    A thin adapter: :func:`update_competency_criteria_group` makes every other refusal, including
    the edit permission check, so an in-process caller is refused in the same cases. The body's
    fixed fields are compared here, before that function runs, because only a REST body carries
    them. Comparing them reveals what's stored, so a caller who can't read the competency's tree is
    refused with 403 before the group is looked up.
    """

    serializer_class = CompetencyCriteriaGroupUpdateSerializer
    http_method_names = ["patch"]
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = [IsAuthenticated, CompetencyReadPermission]

    def patch(self, request, tag_id: int, group_id: int):
        """Check the body against the stored group, then delegate to the public API."""
        # Same 404 the create and tree endpoints give a tag that isn't a competency, ahead of the body checks.
        try:
            tag = resolve_competency_tag(tag_id)
        except Tag.DoesNotExist as exc:
            raise NotFound(str(exc)) from exc
        # The group lookup and body check below reveal what's stored, so only a tree reader may reach them.
        self.check_object_permissions(request, tag)
        # Scoped by tag_id, so addressing a group under a competency it doesn't belong to is a 404.
        group = get_object_or_404(
            CompetencyCriteriaGroup.objects.select_related("course"), pk=group_id, tag_id=tag_id,
        )
        serializer = self.get_serializer(group, data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            group = update_competency_criteria_group(
                group.id, logic_operator=serializer.validated_data["logic_operator"], user=request.user,
            )
        except CompetencyCriteriaGroup.DoesNotExist as exc:
            raise NotFound(str(exc)) from exc
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages) from exc
        except CompetencyCriteriaGroupArchivedError as exc:
            raise CompetencyCriteriaConflict(str(exc)) from exc
        return Response(CompetencyCriteriaGroupSerializer(group).data, status=status.HTTP_200_OK)
