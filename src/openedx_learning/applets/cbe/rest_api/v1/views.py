"""
Views for the CBE REST API, v1.
"""
from __future__ import annotations

from django.db.models import QuerySet
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication  # type: ignore[import]
from edx_rest_framework_extensions.auth.session.authentication import (  # type: ignore[import]
    SessionAuthenticationAllowInactiveUser,
)
from rest_framework import mixins
from rest_framework.viewsets import GenericViewSet

from ...api import get_competency_rule_profiles
from ...models import CompetencyRuleProfile
from ..paginators import CompetencyRuleProfilePagination
from .permissions import CompetencyRuleProfilePermissions
from .serializers import CompetencyRuleProfileSerializer


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
