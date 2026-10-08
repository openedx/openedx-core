"""
Permissions for the CBE REST API, v1.
"""
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
