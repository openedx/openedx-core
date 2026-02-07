"""
Django metadata for the Components Django application.
"""
from django.apps import AppConfig


class ComponentsConfig(AppConfig):
    """
    Configuration for the Components Django application.
    """

    name = "openedx_content.backcompat.components"
    verbose_name = "Learning Core > Authoring > Components"
    default_auto_field = "django.db.models.BigAutoField"
    label = "oel_components"
