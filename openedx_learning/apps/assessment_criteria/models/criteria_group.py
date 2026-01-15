"""
Assessment criteria group model.
"""
from django.db import models


class GroupLogicOperator(models.TextChoices):
    AND = "AND", "AND"
    OR = "OR", "OR"


class AssessmentCriteriaGroup(models.Model):
    """
    Group of assessment criteria, optionally nested.
    """
    course_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    competency_tag = models.ForeignKey(
        "oel_tagging.Tag",
        on_delete=models.PROTECT,
        related_name="assessment_criteria_groups",
    )
    name = models.CharField(max_length=255)
    ordering = models.PositiveIntegerField()
    logic_operator = models.CharField(
        max_length=3,
        choices=GroupLogicOperator.choices,
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["parent", "ordering"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.id})"
