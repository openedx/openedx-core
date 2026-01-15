"""
Assessment criteria API serializers.
"""
from rest_framework import serializers

from ...models import (
    AssessmentCriteria,
    AssessmentCriteriaGroup,
    StudentAssessmentCriteriaStatus,
    StudentCompetencyStatus,
)


class AssessmentCriteriaGroupSerializer(serializers.ModelSerializer):
    """
    Serializer for AssessmentCriteriaGroup.
    """

    class Meta:
        model = AssessmentCriteriaGroup
        fields = [
            "id",
            "course_id",
            "parent",
            "competency_tag",
            "name",
            "ordering",
            "logic_operator",
        ]


class AssessmentCriteriaSerializer(serializers.ModelSerializer):
    """
    Serializer for AssessmentCriteria.
    """

    class Meta:
        model = AssessmentCriteria
        fields = [
            "id",
            "course_id",
            "group",
            "object_tag",
            "competency_tag",
            "rule_type",
            "rule",
            "retake_rule",
        ]


class StudentAssessmentCriteriaStatusSerializer(serializers.ModelSerializer):
    """
    Serializer for StudentAssessmentCriteriaStatus.
    """

    class Meta:
        model = StudentAssessmentCriteriaStatus
        fields = [
            "id",
            "assessment_criteria",
            "user",
            "status",
            "timestamp",
        ]


class StudentCompetencyStatusSerializer(serializers.ModelSerializer):
    """
    Serializer for StudentCompetencyStatus.
    """

    class Meta:
        model = StudentCompetencyStatus
        fields = [
            "id",
            "competency_tag",
            "user",
            "status",
            "timestamp",
        ]
