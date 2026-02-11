"""PathwayStep model."""

from django.db import models
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.django.models import LearningContextKeyField

from .pathway import Pathway


class PathwayStep(models.Model):
    """
    A single step in a pathway.

    For now, steps reference courses via a LearningContextKey.
    In the future, we want to switch to the CourseRuns (https://github.com/openedx/openedx-learning/issues/469).


    Steps are ordered within a pathway. The ``step_type`` is stored explicitly
    for query efficiency and validation -- it is NOT derived from the key at
    query time.

    .. no_pii:
    """

    class StepType(models.TextChoices):
        COURSE = "course", _("Course")
        PATHWAY = "pathway", _("Pathway")

    pathway = models.ForeignKey(Pathway, on_delete=models.CASCADE, related_name="steps")
    context_key = LearningContextKeyField(
        max_length=255, help_text=_("Opaque key of the learning context (e.g. 'course-v1:OpenedX+DemoX+DemoCourse').")
    )

    step_type = models.CharField(
        max_length=32,
        choices=StepType.choices,
        help_text=_("Type of learning context this step references."),
    )

    order = models.PositiveIntegerField(help_text=_("Position of this step within the pathway (0-indexed)."))

    def __str__(self) -> str:
        """User-friendly string representation of this model."""
        return f"{self.pathway.key} #{self.order}: {self.context_key}"

    @classmethod
    def get_step_type_for_key(cls, key) -> str:
        """Determine the StepType from a LearningContextKey's namespace."""

        namespace = getattr(key, "CANONICAL_NAMESPACE", str(key).split(":")[0])
        type_mapping = {
            "course-v1": cls.StepType.COURSE,
            "path-v1": cls.StepType.PATHWAY,
        }
        return type_mapping.get(namespace, cls.StepType.COURSE)

    class Meta:
        """Model options."""
        verbose_name = _("Pathway Step")
        verbose_name_plural = _("Pathway Steps")
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["pathway", "order"],
                name="oel_pathway_step_uniq_order",
            ),
            models.UniqueConstraint(
                fields=["pathway", "context_key"],
                name="oel_pathway_step_uniq_ctx",
            ),
        ]
