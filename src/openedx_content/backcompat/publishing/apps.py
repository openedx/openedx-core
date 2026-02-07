"""
Publishing Django application initialization.
"""
from django.apps import AppConfig


class PublishingConfig(AppConfig):
    """
    Configuration for the publishing Django application.
    """

    name = "openedx_content.backcompat.publishing"
    verbose_name = "Learning Core > Authoring > Publishing"
    default_auto_field = "django.db.models.BigAutoField"
    label = "oel_publishing"
