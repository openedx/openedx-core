"""
Student status models for assessment criteria.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

class StudentStatus(models.TextChoices):
    """
    Shared status for student progress tables.
    """

    DEMONSTRATED = "demonstrated", "Demonstrated"
    ATTEMPTED_NOT_DEMONSTRATED = "attempted_not_demonstrated", "Attempted, Not Demonstrated"
    NOT_ATTEMPTED = "not_attempted", "Not Attempted"


class StudentAssessmentCriteriaStatus(models.Model):
    """
    Student status for an individual assessment criteria.
    """
    assessment_criteria = models.ForeignKey(
        "oel_assessment_criteria.AssessmentCriteria",
        on_delete=models.CASCADE,
        related_name="student_statuses",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assessment_criteria_statuses",
    )
    status = models.CharField(max_length=32, choices=StudentStatus.choices)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["assessment_criteria", "user"]),
        ]


class StudentCompetencyStatus(models.Model):
    """
    Student status for a competency (tag).
    """
    competency_tag = models.ForeignKey(
        "oel_tagging.Tag",
        on_delete=models.PROTECT,
        related_name="student_competency_statuses",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="competency_statuses",
    )
    status = models.CharField(max_length=32, choices=StudentStatus.choices)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["competency_tag", "user"]),
        ]
