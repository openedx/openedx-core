"""
Assessment criteria Django application initialization.
"""
from django.apps import AppConfig


class AssessmentCriteriaConfig(AppConfig):
    """
    Configuration for the assessment criteria Django application.
    """
    name = "openedx_learning.apps.assessment_criteria"
    verbose_name = "Learning Core > Assessment Criteria"
    default_auto_field = "django.db.models.BigAutoField"
    label = "oel_assessment_criteria"

    def ready(self):
        # Register signal handlers.
        from . import events  # pylint: disable=unused-import
