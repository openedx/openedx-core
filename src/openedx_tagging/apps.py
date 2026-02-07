"""
tagging Django application initialization.
"""

from django.apps import AppConfig


class TaggingConfig(AppConfig):
    """
    Configuration for the tagging Django application.
    """

    name = "openedx_tagging"
    verbose_name = "Open edX Core > Tagging"
    default_auto_field = "django.db.models.BigAutoField"

    # Historical note: "oel" comes from "Open edX Learning", the original
    # name of this apps's repository.
    label = "oel_tagging"
