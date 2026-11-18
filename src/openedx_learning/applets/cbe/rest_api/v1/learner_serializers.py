"""
Serializers for the CBE REST API, v1, for learners.
"""
from __future__ import annotations

from rest_framework import serializers


class _ReadOnlySerializer(serializers.Serializer):
    """
    Base for serializers that only render an object.

    ``create()`` and ``update()`` raise TypeError, not NotImplementedError, so that pylint does not report
    them as abstract in every subclass.
    """

    def create(self, validated_data):
        """Not supported: this serializer is read-only."""
        raise TypeError("This serializer is read-only.")

    def update(self, instance, validated_data):
        """Not supported: this serializer is read-only."""
        raise TypeError("This serializer is read-only.")


class LearnerTaxonomySerializer(_ReadOnlySerializer):
    """
    Read-only representation of a ``LearnerTaxonomy``.

    UNSTABLE: see :func:`openedx_learning.applets.cbe.api.get_course_competency_criteria`.
    """

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)


class LearnerCompetencySerializer(_ReadOnlySerializer):
    """
    Read-only representation of a ``LearnerCompetency``.

    UNSTABLE: see :func:`openedx_learning.applets.cbe.api.get_course_competency_criteria`.
    """

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    external_id = serializers.CharField(read_only=True)
    lineage = serializers.ListField(child=serializers.CharField(), read_only=True)
    taxonomy_id = serializers.IntegerField(read_only=True)


class LearnerCriteriaGroupSerializer(_ReadOnlySerializer):
    """
    Read-only representation of a ``LearnerCriteriaGroup``.

    UNSTABLE: see :func:`openedx_learning.applets.cbe.api.get_course_competency_criteria`.
    """

    id = serializers.IntegerField(read_only=True)
    competency_id = serializers.IntegerField(read_only=True)
    parent_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    logic_operator = serializers.CharField(read_only=True)
    ordering = serializers.IntegerField(read_only=True)


class LearnerCriterionSerializer(_ReadOnlySerializer):
    """
    Read-only representation of a ``LearnerCriterion``.

    ``rule_payload`` is emitted exactly as stored.

    UNSTABLE: see :func:`openedx_learning.applets.cbe.api.get_course_competency_criteria`.
    """

    id = serializers.IntegerField(read_only=True)
    group_id = serializers.IntegerField(read_only=True)
    usage_key = serializers.CharField(read_only=True)
    rule_type = serializers.CharField(read_only=True)
    rule_payload = serializers.JSONField(read_only=True)


class LearnerCourseCriteriaSerializer(_ReadOnlySerializer):
    """
    Read-only representation of a ``LearnerCourseCriteria``: its four lists, each rendered by its own serializer.

    UNSTABLE: see :func:`openedx_learning.applets.cbe.api.get_course_competency_criteria`.
    """

    taxonomies = LearnerTaxonomySerializer(many=True, read_only=True)
    competencies = LearnerCompetencySerializer(many=True, read_only=True)
    groups = LearnerCriteriaGroupSerializer(many=True, read_only=True)
    criteria = LearnerCriterionSerializer(many=True, read_only=True)
