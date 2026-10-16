"""
Permissions for the CBE REST API, v1.
"""
import rules
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

    Distinct from the write-side oel_tagging.can_tag_object check used by
    CompetencyCriterionCreateView: a read has no target object to tag, so there's nothing for
    that predicate to check here. has_object_permission only, no has_permission override: the
    base class's always-True default is correct since the real check is object-level, run
    manually by the view via check_object_permissions().
    """

    def has_object_permission(self, request, view, obj) -> bool:
        """Return True if the requesting user may view the taxonomy that ``obj`` (a Tag) belongs to."""
        return rules.has_perm("oel_tagging.view_tag", request.user, obj)
