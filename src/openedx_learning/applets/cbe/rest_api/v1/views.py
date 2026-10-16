"""
Views for the CBE REST API, v1.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import QuerySet
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
    CompetencyCriterionArchivedError,
    associate_competency_criterion,
    bulk_update_competency_criteria,
    get_competency_rule_profiles,
    resolve_competency_tag,
)
from ...models import CompetencyCriteriaGroup, CompetencyCriterion, CompetencyRuleProfile
from ..paginators import CompetencyRuleProfilePagination
from .permissions import CompetencyRuleProfilePermissions
from .serializers import (
    CompetencyCriterionBulkUpdateSerializer,
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
    archived criterion is refused whole with 409.
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
