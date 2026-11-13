"""
Models that relate course runs to the Learning Packages that hold their content.

See :ref:`openedx-content-adr-0012`.
"""

from __future__ import annotations

from django.db import models

from openedx_catalog.models_api import CourseRun

from ..publishing.models import LearningPackage

__all__ = [
    "CourseContent",
]


class CourseContent(models.Model):
    """
    Maps a :class:`CourseRun` to the :class:`LearningPackage` that holds its
    content.

    Each course run has zero or one ``CourseContent`` rows. By convention, each
    course run gets its own Learning Package, but this is deliberately *not*
    enforced: several ``CourseContent`` rows may point to the same
    :class:`LearningPackage`. Code must not assume that the relationship is
    1:1, and code that needs the course run(s) for a Learning Package must
    handle receiving more than one.

    This is not the canonical "Course" model, and should not be used as a
    foreign key target; use :class:`CourseRun` for that.

    .. no_pii:
    """

    course_run = models.OneToOneField(
        CourseRun,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="content",
    )

    learning_package = models.ForeignKey(
        LearningPackage,
        # Don't allow deleting a Learning Package while a course run is still using it.
        on_delete=models.PROTECT,
        null=False,
        # Not unique: see the class docstring.
        related_name="course_contents",
    )

    class Meta:
        verbose_name = "Course Content"
        verbose_name_plural = "Course Content"

    def __str__(self) -> str:
        return f"CourseContent: {self.course_run.course_key} → {self.learning_package.package_ref}"
