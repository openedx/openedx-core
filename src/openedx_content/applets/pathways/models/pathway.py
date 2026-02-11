"""Pathway model."""

from django.db import models
from django.utils.translation import gettext_lazy as _

# from organizations.models import Organization
from openedx_django_lib.fields import case_insensitive_char_field
from openedx_django_lib.validators import validate_utc_datetime

from ..keys import PathwayKeyField


class Pathway(models.Model):
    """
    A pathway is an ordered sequence of steps that a learner progresses through.

    For now, steps can only reference courses, but in the future they could also reference pathways
    or learning contexts, such as sections, subsections, or units.

    This model consists of only the core fields needed to define a pathway.

    .. no_pii:
    """

    key = PathwayKeyField(
        max_length=255,
        unique=True,
        db_index=True,
        db_column="_key",
        help_text=_("Unique identifier: path-v1:{org}+{path_id}"),
    )

    # TODO: Make this a ForeignKey to the Organization model.
    # org = models.ForeignKey(
    #     Organization,
    #     to_field="short_name",
    #     on_delete=models.PROTECT,
    #     null=False,
    #     editable=False,
    # )
    org = models.CharField(
        max_length=255,
        null=False,
        editable=False,
        help_text=_("A temporary placeholder for the organization short name."),
    )

    display_name = case_insensitive_char_field(max_length=255, blank=False)

    description = models.TextField(blank=True, max_length=10_000)

    sequential = models.BooleanField(
        default=True,
        help_text=_("If True, learners must complete steps in order. If False, steps can be completed in any order."),
    )

    is_active = models.BooleanField(
        default=True,
        help_text=_("If False, this pathway is treated as archived and should not be offered to new learners."),
    )

    invite_only = models.BooleanField(
        default=True,
        help_text=_(
            "If enabled, users can only enroll if they are on the allowlist. "
            "This is True by default to prevent accidentally exposing learning paths to all users. "
            "Only enrolled users can see this learning path."
        ),
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "It can include any additional information about the pathway, such as its duration, difficulty level,"
            "learning outcomes, etc. This field is not used by the core pathway logic. Instead, it aims to provide "
            "flexibility for operators to store additional information and process it in plugins."
        ),
    )

    created = models.DateTimeField(auto_now_add=True, validators=[validate_utc_datetime])
    modified = models.DateTimeField(auto_now=True, validators=[validate_utc_datetime])

    def __str__(self) -> str:
        """User-friendly string representation of this model."""
        return f"{self.key}"

    class Meta:
        """Model options."""

        verbose_name = _("Pathway")
        verbose_name_plural = _("Pathways")
