"""
Unit Django application initialization.
"""

from django.apps import AppConfig


class UnitsConfig(AppConfig):
    """
    Configuration for the units Django application.
    """

    name = "openedx_content.backcompat.units"
    default_auto_field = "django.db.models.BigAutoField"
    label = "oel_units"
