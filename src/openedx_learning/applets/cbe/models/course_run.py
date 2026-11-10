"""
CompetencyCourseRun: CBE's own per-course row for a catalog CourseRun.
"""
from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx_catalog.models_api import CourseRun

__all__ = [
    "CompetencyCourseRun",
]


class CompetencyCourseRun(models.Model):
    """
    A course run that has competency criteria, created with its first criterion.

    Criterion creation locks this row, rather than the catalog's CourseRun, to serialize the
    per-course dominance check without blocking other apps' writes that reference the course run.

    .. no_pii:
    """

    course_run = models.OneToOneField(
        CourseRun,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="competency_course_run",
        help_text=_("The catalog course run this row extends."),
    )
