"""
Assessment criteria API.

Use these helpers instead of manipulating models directly, so future logic can
stay centralized here.
"""
from __future__ import annotations

from django.db import models

from .models import (
    AssessmentCriteria,
    AssessmentCriteriaGroup,
    StudentAssessmentCriteriaStatus,
    StudentCompetencyStatus,
)
from .models.student_status import StudentStatus


AssessmentCriteriaGroupDoesNotExist = AssessmentCriteriaGroup.DoesNotExist
AssessmentCriteriaDoesNotExist = AssessmentCriteria.DoesNotExist
StudentAssessmentCriteriaStatusDoesNotExist = StudentAssessmentCriteriaStatus.DoesNotExist
StudentCompetencyStatusDoesNotExist = StudentCompetencyStatus.DoesNotExist


def create_assessment_criteria_group(
    *,
    parent: AssessmentCriteriaGroup | None,
    competency_tag,
    name: str,
    ordering: int,
    logic_operator: str | None = None,
) -> AssessmentCriteriaGroup:
    """
    Create and return an AssessmentCriteriaGroup.
    """
    group = AssessmentCriteriaGroup(
        parent=parent,
        competency_tag=competency_tag,
        name=name,
        ordering=ordering,
        logic_operator=logic_operator,
    )
    group.full_clean()
    group.save()
    return group


def get_assessment_criteria_group(group_id: int) -> AssessmentCriteriaGroup | None:
    """
    Return a group by id, or None if not found.
    """
    return AssessmentCriteriaGroup.objects.filter(id=group_id).first()


def list_assessment_criteria_groups(
    *,
    parent: AssessmentCriteriaGroup | None = None,
) -> models.QuerySet[AssessmentCriteriaGroup]:
    """
    Return groups, optionally filtered by parent.
    """
    qs = AssessmentCriteriaGroup.objects.all()
    if parent is not None:
        qs = qs.filter(parent=parent)
    return qs.order_by("ordering", "id")


def update_assessment_criteria_group(
    group: AssessmentCriteriaGroup,
    *,
    parent: AssessmentCriteriaGroup | None | models.NOT_PROVIDED = models.NOT_PROVIDED,
    competency_tag=models.NOT_PROVIDED,
    name: str | models.NOT_PROVIDED = models.NOT_PROVIDED,
    ordering: int | models.NOT_PROVIDED = models.NOT_PROVIDED,
    logic_operator: str | None | models.NOT_PROVIDED = models.NOT_PROVIDED,
) -> AssessmentCriteriaGroup:
    """
    Update and return an AssessmentCriteriaGroup.
    """
    if parent is not models.NOT_PROVIDED:
        group.parent = parent
    if competency_tag is not models.NOT_PROVIDED:
        group.competency_tag = competency_tag
    if name is not models.NOT_PROVIDED:
        group.name = name
    if ordering is not models.NOT_PROVIDED:
        group.ordering = ordering
    if logic_operator is not models.NOT_PROVIDED:
        group.logic_operator = logic_operator
    group.full_clean()
    group.save()
    return group


def delete_assessment_criteria_group(group: AssessmentCriteriaGroup) -> None:
    """
    Delete the provided AssessmentCriteriaGroup.
    """
    group.delete()


def create_assessment_criteria(
    *,
    group: AssessmentCriteriaGroup,
    object_tag,
    competency_tag,
    rule_type: str,
    rule: str,
    retake_rule: str,
) -> AssessmentCriteria:
    """
    Create and return an AssessmentCriteria.
    """
    criteria = AssessmentCriteria(
        group=group,
        object_tag=object_tag,
        competency_tag=competency_tag,
        rule_type=rule_type,
        rule=rule,
        retake_rule=retake_rule,
    )
    criteria.full_clean()
    criteria.save()
    return criteria


def get_assessment_criteria(criteria_id: int) -> AssessmentCriteria | None:
    """
    Return assessment criteria by id, or None if not found.
    """
    return AssessmentCriteria.objects.filter(id=criteria_id).first()


def list_assessment_criteria(
    *,
    group: AssessmentCriteriaGroup | None = None,
) -> models.QuerySet[AssessmentCriteria]:
    """
    Return criteria, optionally filtered by group.
    """
    qs = AssessmentCriteria.objects.all()
    if group is not None:
        qs = qs.filter(group=group)
    return qs.order_by("id")


def update_assessment_criteria(
    criteria: AssessmentCriteria,
    *,
    group: AssessmentCriteriaGroup | models.NOT_PROVIDED = models.NOT_PROVIDED,
    object_tag=models.NOT_PROVIDED,
    competency_tag=models.NOT_PROVIDED,
    rule_type: str | models.NOT_PROVIDED = models.NOT_PROVIDED,
    rule: str | models.NOT_PROVIDED = models.NOT_PROVIDED,
    retake_rule: str | models.NOT_PROVIDED = models.NOT_PROVIDED,
) -> AssessmentCriteria:
    """
    Update and return AssessmentCriteria.
    """
    if group is not models.NOT_PROVIDED:
        criteria.group = group
    if object_tag is not models.NOT_PROVIDED:
        criteria.object_tag = object_tag
    if competency_tag is not models.NOT_PROVIDED:
        criteria.competency_tag = competency_tag
    if rule_type is not models.NOT_PROVIDED:
        criteria.rule_type = rule_type
    if rule is not models.NOT_PROVIDED:
        criteria.rule = rule
    if retake_rule is not models.NOT_PROVIDED:
        criteria.retake_rule = retake_rule
    criteria.full_clean()
    criteria.save()
    return criteria


def delete_assessment_criteria(criteria: AssessmentCriteria) -> None:
    """
    Delete the provided AssessmentCriteria.
    """
    criteria.delete()


def set_student_assessment_criteria_status(
    *,
    assessment_criteria: AssessmentCriteria,
    user,
    status: StudentStatus,
) -> StudentAssessmentCriteriaStatus:
    """
    Create or update student assessment criteria status.
    """
    entry, _created = StudentAssessmentCriteriaStatus.objects.update_or_create(
        assessment_criteria=assessment_criteria,
        user=user,
        defaults={"status": status},
    )
    return entry


def set_student_competency_status(
    *,
    competency_tag,
    user,
    status: StudentStatus,
) -> StudentCompetencyStatus:
    """
    Create or update student competency status.
    """
    entry, _created = StudentCompetencyStatus.objects.update_or_create(
        competency_tag=competency_tag,
        user=user,
        defaults={"status": status},
    )
    return entry


def list_student_assessment_criteria_statuses(
    *,
    assessment_criteria: AssessmentCriteria | None = None,
    user=None,
) -> models.QuerySet[StudentAssessmentCriteriaStatus]:
    """
    Return student assessment criteria statuses with optional filters.
    """
    qs = StudentAssessmentCriteriaStatus.objects.all()
    if assessment_criteria is not None:
        qs = qs.filter(assessment_criteria=assessment_criteria)
    if user is not None:
        qs = qs.filter(user=user)
    return qs.order_by("-timestamp", "id")


def list_student_competency_statuses(
    *,
    competency_tag=None,
    user=None,
) -> models.QuerySet[StudentCompetencyStatus]:
    """
    Return student competency statuses with optional filters.
    """
    qs = StudentCompetencyStatus.objects.all()
    if competency_tag is not None:
        qs = qs.filter(competency_tag=competency_tag)
    if user is not None:
        qs = qs.filter(user=user)
    return qs.order_by("-timestamp", "id")
