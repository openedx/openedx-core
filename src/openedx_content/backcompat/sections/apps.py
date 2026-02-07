"""
Sections Django application initialization.
"""

from django.apps import AppConfig


class SectionsConfig(AppConfig):
    """
    Configuration for the Sections Django application.
    """

    name = "openedx_content.backcompat.sections"
    verbose_name = "Learning Core > Authoring > Sections"
    default_auto_field = "django.db.models.BigAutoField"
    label = "oel_sections"
