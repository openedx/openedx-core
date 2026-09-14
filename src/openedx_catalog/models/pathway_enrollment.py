"""
PathwayEnrollment model
"""

import logging
from typing import NewType

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx_django_lib.fields import TypedBigAutoField
from openedx_django_lib.validators import validate_utc_datetime

from .catalog_pathway import CatalogPathway

log = logging.getLogger(__name__)


class PathwayEnrollment(models.Model):
    """
    Ties a learner to a `CatalogPathway`.

    Enrollment is against the *catalog* half of a Pathway, never against a version of its content. Progress is evaluated
    against whichever content version is published at the time of evaluation, not against a version frozen at enrollment
    time, so that authoring changes reach learners who are already enrolled. That is why this model pins no version. See
    the openedx_learning ADR 0007, decision 5.

    Unenrolling sets ``is_active`` to False rather than deleting the row, so that "unenrolled" can be told apart from
    "never enrolled" and the original enrollment date survives. Re-enrolling reactivates the same row.

    .. no_pii:
    """

    PathwayEnrollmentID = NewType("PathwayEnrollmentID", int)
    type ID = PathwayEnrollmentID

    class IDField(TypedBigAutoField[ID]):  # Boilerplate for fully-typed ID field.
        pass

    id = IDField(
        primary_key=True,
        verbose_name=_("Primary Key"),
        help_text=_("The internal database ID for this enrollment. Should not be exposed to users nor in APIs."),
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=False,
        related_name="pathway_enrollments",
    )
    catalog_pathway = models.ForeignKey(
        CatalogPathway,
        on_delete=models.CASCADE,
        null=False,
        related_name="enrollments",
    )
    created = models.DateTimeField(
        auto_now_add=True,
        validators=[validate_utc_datetime],
        editable=False,
    )
    modified = models.DateTimeField(
        auto_now=True,
        validators=[validate_utc_datetime],
        editable=False,
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("False once the learner has unenrolled. The row is kept so re-enrolling reuses it."),
    )

    def __str__(self) -> str:
        return f"{self.user} in {self.catalog_pathway}"

    class Meta:
        verbose_name = _("Pathway Enrollment")
        verbose_name_plural = _("Pathway Enrollments")
        ordering = ("-created",)
        constraints = [
            # There is only ever one row per (learner, pathway) pair; unenrolling flips `is_active` rather than
            # deleting or adding a row.
            models.UniqueConstraint(
                fields=["user", "catalog_pathway"],
                name="oex_catalog_pathwayenrollment_uniq_user_pathway",
            ),
        ]
