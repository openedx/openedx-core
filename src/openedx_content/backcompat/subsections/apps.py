"""
Subsection Django application initialization.
"""

from django.apps import AppConfig


class SubsectionsConfig(AppConfig):
    """
    Configuration for the subsections Django application.
    """

    name = "openedx_content.backcompat.subsections"
    default_auto_field = "django.db.models.BigAutoField"
    label = "oel_subsections"
