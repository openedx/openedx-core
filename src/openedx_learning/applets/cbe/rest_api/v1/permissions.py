"""
Permissions for the CBE REST API, v1.
"""
from rest_framework import permissions
from rest_framework.permissions import DjangoObjectPermissions


class CompetencyRuleProfilePermissions(DjangoObjectPermissions):
    """
    Maps each REST API method to its corresponding CompetencyRuleProfile permission.

    Only the read methods are mapped, so DRF answers a write attempt with 405 rather than
    checking a permission that no write endpoint would honor anyway.
    """

    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
    }


class CompetencyReadPermission(permissions.BasePermission):
    """
    A user may read a competency's criteria tree if they can view the tag's taxonomy.

    Authentication comes from ``IsAuthenticated`` on the view, so this class only does the
    object-level check, which the view runs through ``check_object_permissions()``. That check
    asks ``oel_tagging.view_taxonomy`` because openedx-platform overrides that rule with its
    org-aware one and sets ``oel_tagging.view_tag`` to always allow.
    """

    def has_object_permission(self, request, view, obj) -> bool:
        """Return True if the requesting user may view the taxonomy that ``obj`` (a Tag) belongs to."""
        # has_perm(..., None) would fall back to a global rule, so a tag without a taxonomy is refused.
        return obj.taxonomy is not None and request.user.has_perm("oel_tagging.view_taxonomy", obj.taxonomy)
