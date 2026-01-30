"""
Django metadata for the Collections Django application.
"""
from django.apps import AppConfig


class CollectionsConfig(AppConfig):
    """
    Configuration for the Collections Django application.
    """

    name = "openedx_content.backcompat.collections"
    default_auto_field = "django.db.models.BigAutoField"
    label = "oel_collections"
