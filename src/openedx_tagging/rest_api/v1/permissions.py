"""
Tagging permissions
"""
import rules
from rest_framework.permissions import DjangoObjectPermissions

from ...models import Tag, Taxonomy


class TaxonomyObjectPermissions(DjangoObjectPermissions):
    """
    Maps each REST API methods to its corresponding Taxonomy permission.
    """
    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
        "POST": ["%(app_label)s.add_%(model_name)s"],
        "PUT": ["%(app_label)s.change_%(model_name)s"],
        "PATCH": ["%(app_label)s.change_%(model_name)s"],
        "DELETE": ["%(app_label)s.delete_%(model_name)s"],
    }


class ObjectTagObjectPermissions(DjangoObjectPermissions):
    """
    Maps each REST API methods to its corresponding ObjectTag permission.
    """
    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
        "POST": ["%(app_label)s.add_%(model_name)s"],
        "PUT": ["%(app_label)s.change_%(model_name)s"],
        "PATCH": ["%(app_label)s.change_%(model_name)s"],
        "DELETE": ["%(app_label)s.delete_%(model_name)s"],
    }


class TaxonomyTagsObjectPermissions(DjangoObjectPermissions):
    """
    Maps each REST API methods to its corresponding Taxonomy permission.
    """
    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
        "POST": ["%(app_label)s.add_%(model_name)s"],
        "PUT": ["%(app_label)s.change_%(model_name)s"],
        "PATCH": ["%(app_label)s.change_%(model_name)s"],
        "DELETE": ["%(app_label)s.delete_%(model_name)s"],
    }

    # This is to handle the special case for GET list of Taxonomy Tags
    def has_object_permission(self, request, view, obj):
        """
        Returns True if the user on the given request is allowed the given view for the given object.
        """
        obj = obj.taxonomy if isinstance(obj, Tag) else obj
        return rules.has_perm("oel_tagging.list_tag", request.user, obj)

    def _queryset(self, view):
        """
        Returns the queryset to use when checking model and object permissions.

        The base class method calls view.get_queryset(), but that method performs database queries, so we override it.
        """
        return Taxonomy.objects
