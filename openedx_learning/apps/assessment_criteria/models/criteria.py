"""
Assessment criteria model.
"""
from django.db import models


class RuleType(models.TextChoices):
    GRADE = "Grade", "Grade"
    MASTERY_LEVEL = "MasteryLevel", "MasteryLevel"


class RetakeRule(models.TextChoices):
    SIMPLE_AVERAGE = "SimpleAverage", "SimpleAverage"
    WEIGHTED_AVERAGE = "WeightedAverage", "WeightedAverage"
    DECAYING_AVERAGE = "DecayingAverage", "DecayingAverage"
    MOST_RECENT = "MostRecent", "MostRecent"
    HIGHEST = "Highest", "Highest"


class AssessmentCriteria(models.Model):
    """
    Single assessment rule within a group.
    """
    group = models.ForeignKey(
        "oel_assessment_criteria.AssessmentCriteriaGroup",
        on_delete=models.CASCADE,
        related_name="criteria",
    )
    object_tag = models.ForeignKey(
        "oel_tagging.ObjectTag",
        on_delete=models.PROTECT,
        related_name="assessment_criteria",
    )
    competency_tag = models.ForeignKey(
        "oel_tagging.Tag",
        on_delete=models.PROTECT,
        related_name="assessment_criteria",
    )
    rule_type = models.CharField(max_length=20, choices=RuleType.choices)
    rule = models.CharField(max_length=255)
    retake_rule = models.CharField(max_length=20, choices=RetakeRule.choices)

    class Meta:
        indexes = [
            models.Index(fields=["group"]),
            models.Index(fields=["competency_tag"]),
        ]

    def __str__(self):
        return f"{self.rule_type}:{self.rule} ({self.id})"
