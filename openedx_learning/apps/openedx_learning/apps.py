"""
Django metadata for the Components Django application.
"""
from django.apps import AppConfig


class ModularLearningConfig(AppConfig):
    """
    Configuration for the Components Django application.
    """

    name = "openedx_learning.apps.modular_learning"
    verbose_name = "Learning Core > Modular Learning"
    default_auto_field = "django.db.models.BigAutoField"
    label = "oel_modular_learning"

    def ready(self) -> None:
        """
        Register Component and ComponentVersion.
        """
        pass
