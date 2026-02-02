"""
Signal handlers for assessment criteria.
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import models
from django.dispatch import receiver

from openedx_events.learning.signals import PERSISTENT_SUBSECTION_GRADE_CHANGED

from openedx_tagging.core.tagging.models import ObjectTag

from .api import set_student_assessment_criteria_status, set_student_competency_status
from .models import AssessmentCriteria, GroupLogicOperator, RuleType
from .models.student_status import StudentStatus

log = logging.getLogger(__name__)


_OPS = {
    "gt": lambda actual, expected: actual > expected,
    "gte": lambda actual, expected: actual >= expected,
    "lt": lambda actual, expected: actual < expected,
    "lte": lambda actual, expected: actual <= expected,
    "eq": lambda actual, expected: actual == expected,
}


def _percent_from_grade(grade) -> float | None:
    if grade.weighted_graded_possible and grade.weighted_graded_possible > 0:
        return (grade.weighted_graded_earned / grade.weighted_graded_possible) * 100.0
    return None


def _evaluate_grade_rule(rule_payload: dict, percent: float | None) -> bool | None:
    if percent is None:
        return None
    op = rule_payload.get("op")
    value = rule_payload.get("value")
    scale = rule_payload.get("scale", "percent")
    if op not in _OPS:
        log.warning("Unsupported grade rule op: %s", op)
        return None
    if value is None:
        log.warning("Missing grade rule value.")
        return None

    try:
        expected = float(value)
    except (TypeError, ValueError):
        log.warning("Invalid grade rule value: %s", value)
        return None

    if scale == "percent":
        expected_percent = expected
    elif scale == "fraction":
        expected_percent = expected * 100.0
    else:
        log.warning("Unsupported grade rule scale: %s", scale)
        return None

    return _OPS[op](percent, expected_percent)


def _derive_status(grade, rule_payload: dict) -> StudentStatus:
    if grade.first_attempted is None:
        return StudentStatus.NOT_ATTEMPTED
    passed = _evaluate_grade_rule(rule_payload, _percent_from_grade(grade))
    if passed is True:
        return StudentStatus.DEMONSTRATED
    return StudentStatus.ATTEMPTED_NOT_DEMONSTRATED


def _compute_group_status(group, user) -> StudentStatus:
    criteria_qs = AssessmentCriteria.objects.filter(group=group)
    statuses = list(
        criteria_qs.values_list(
            "student_statuses__status",
            flat=True,
        ).filter(student_statuses__user=user)
    )
    log.info("Group %s statuses for user %s: %s", group.id, user.id, statuses)
    if not statuses:
        return StudentStatus.NOT_ATTEMPTED

    logic_operator = group.logic_operator or GroupLogicOperator.AND
    if logic_operator == GroupLogicOperator.OR:
        if StudentStatus.DEMONSTRATED in statuses:
            return StudentStatus.DEMONSTRATED
        if StudentStatus.ATTEMPTED_NOT_DEMONSTRATED in statuses:
            return StudentStatus.ATTEMPTED_NOT_DEMONSTRATED
        return StudentStatus.NOT_ATTEMPTED

    if all(status == StudentStatus.DEMONSTRATED for status in statuses):
        return StudentStatus.DEMONSTRATED
    if any(status == StudentStatus.ATTEMPTED_NOT_DEMONSTRATED for status in statuses):
        return StudentStatus.ATTEMPTED_NOT_DEMONSTRATED
    return StudentStatus.NOT_ATTEMPTED


@receiver(PERSISTENT_SUBSECTION_GRADE_CHANGED)
def handle_persistent_subsection_grade_changed(sender, grade, **kwargs):  # pylint: disable=unused-argument
    """
    Update assessment criteria and competency status when a subsection grade changes.
    """
    percent = _percent_from_grade(grade)
    log.info(
        "Subsection grade event: user_id=%s course=%s usage_key=%s graded=%s/%s percent=%s",
        grade.user_id,
        grade.course.course_key,
        grade.usage_key,
        grade.weighted_graded_earned,
        grade.weighted_graded_possible,
        None if percent is None else round(percent, 2),
    )
    user = get_user_model().objects.filter(id=grade.user_id).first()
    if not user:
        log.warning("User not found for grade event: %s", grade.user_id)
        return

    object_id = str(grade.usage_key)
    object_tags = ObjectTag.objects.filter(object_id=object_id)
    log.info("Object tags found for %s: %s", object_id, object_tags.count())
    if not object_tags.exists():
        log.info("No object tags found for %s; skipping.", object_id)
        return

    course_id = str(grade.course.course_key)
    criteria_qs = AssessmentCriteria.objects.filter(object_tag__in=object_tags).filter(
        models.Q(course_id__isnull=True) | models.Q(course_id="") | models.Q(course_id=course_id)
    )
    log.info("Assessment criteria found for course %s: %s", course_id, criteria_qs.count())
    if not criteria_qs.exists():
        log.info("No assessment criteria found for %s; skipping.", course_id)
        return

    updated_groups = set()
    for criteria in criteria_qs.select_related("group", "competency_tag"):
        if criteria.rule_type != RuleType.GRADE:
            log.info("Skipping non-grade criteria %s (rule_type=%s)", criteria.id, criteria.rule_type)
            continue
        if not isinstance(criteria.rule_payload, dict):
            log.warning("Invalid rule_payload for criteria %s", criteria.id)
            continue
        status = _derive_status(grade, criteria.rule_payload)
        log.info("Criteria %s rule=%s status=%s", criteria.id, criteria.rule_payload, status)
        set_student_assessment_criteria_status(
            assessment_criteria=criteria,
            user=user,
            status=status,
        )
        updated_groups.add(criteria.group)

    for group in updated_groups:
        group_status = _compute_group_status(group, user)
        log.info(
            "Group %s logic=%s computed status=%s",
            group.id,
            group.logic_operator or GroupLogicOperator.AND,
            group_status,
        )
        set_student_competency_status(
            competency_tag=group.competency_tag,
            user=user,
            status=group_status,
        )
