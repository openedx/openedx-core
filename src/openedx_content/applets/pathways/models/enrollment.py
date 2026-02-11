"""Enrollment models for Pathways."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx_django_lib.validators import validate_utc_datetime

from .pathway import Pathway


class PathwayEnrollment(models.Model):
    """
    Tracks a user's enrollment in a pathway.

    .. no_pii:
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pathway_enrollments")
    pathway = models.ForeignKey(Pathway, on_delete=models.CASCADE, related_name="enrollments")
    is_active = models.BooleanField(default=True, help_text=_("Indicates whether the learner is enrolled."))
    created = models.DateTimeField(auto_now_add=True, validators=[validate_utc_datetime])
    modified = models.DateTimeField(auto_now=True, validators=[validate_utc_datetime])

    def __str__(self) -> str:
        """User-friendly string representation of this model."""
        return f"PathwayEnrollment of user={self.user_id} in {self.pathway_id}"

    class Meta:
        """Model options."""

        verbose_name = _("Pathway Enrollment")
        verbose_name_plural = _("Pathway Enrollments")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "pathway"],
                name="oel_pathway_enroll_uniq",
            ),
        ]


class PathwayEnrollmentAllowed(models.Model):
    """
    Pre-registration allowlist for invite-only pathways.

    These entities are created when learners are invited/enrolled before they register an account.

    .. pii: The email field is not retired to allow future learners to enroll.
    .. pii_types: email_address
    .. pii_retirement: retained
    """

    pathway = models.ForeignKey(Pathway, on_delete=models.CASCADE, related_name="enrollment_allowed")
    email = models.EmailField(db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, blank=True, null=True)
    is_active = models.BooleanField(
        default=True, db_index=True, help_text=_("Indicates if the enrollment allowance is active")
    )
    created = models.DateTimeField(auto_now_add=True, validators=[validate_utc_datetime])

    def __str__(self) -> str:
        """User-friendly string representation of this model."""
        return f"PathwayEnrollmentAllowed for {self.email} in {self.pathway_id}"

    class Meta:
        """Model options."""

        verbose_name = _("Pathway Enrollment Allowed")
        verbose_name_plural = _("Pathway Enrollments Allowed")
        constraints = [
            models.UniqueConstraint(
                fields=["pathway", "email"],
                name="oel_pathway_enrollallow_uniq",
            ),
        ]


# TODO: Create receivers to automatically create audit records.
class PathwayEnrollmentAudit(models.Model):
    """
    Audit log for pathway enrollment changes.

    .. no_pii:
    """

    # State transition constants (copied from openedx-platform to maintain consistency)
    UNENROLLED_TO_ALLOWEDTOENROLL = "from unenrolled to allowed to enroll"
    ALLOWEDTOENROLL_TO_ENROLLED = "from allowed to enroll to enrolled"
    ENROLLED_TO_ENROLLED = "from enrolled to enrolled"
    ENROLLED_TO_UNENROLLED = "from enrolled to unenrolled"
    UNENROLLED_TO_ENROLLED = "from unenrolled to enrolled"
    ALLOWEDTOENROLL_TO_UNENROLLED = "from allowed to enroll to unenrolled"
    UNENROLLED_TO_UNENROLLED = "from unenrolled to unenrolled"
    DEFAULT_TRANSITION_STATE = "N/A"

    TRANSITION_STATES = (
        (UNENROLLED_TO_ALLOWEDTOENROLL, UNENROLLED_TO_ALLOWEDTOENROLL),
        (ALLOWEDTOENROLL_TO_ENROLLED, ALLOWEDTOENROLL_TO_ENROLLED),
        (ENROLLED_TO_ENROLLED, ENROLLED_TO_ENROLLED),
        (ENROLLED_TO_UNENROLLED, ENROLLED_TO_UNENROLLED),
        (UNENROLLED_TO_ENROLLED, UNENROLLED_TO_ENROLLED),
        (ALLOWEDTOENROLL_TO_UNENROLLED, ALLOWEDTOENROLL_TO_UNENROLLED),
        (UNENROLLED_TO_UNENROLLED, UNENROLLED_TO_UNENROLLED),
        (DEFAULT_TRANSITION_STATE, DEFAULT_TRANSITION_STATE),
    )

    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="pathway_enrollment_audits"
    )
    enrollment = models.ForeignKey(PathwayEnrollment, on_delete=models.CASCADE, null=True, related_name="audit_log")
    enrollment_allowed = models.ForeignKey(
        PathwayEnrollmentAllowed, on_delete=models.CASCADE, null=True, related_name="audit_log"
    )
    state_transition = models.CharField(max_length=255, choices=TRANSITION_STATES, default=DEFAULT_TRANSITION_STATE)
    reason = models.TextField(blank=True)
    org = models.CharField(max_length=255, blank=True, db_index=True)
    role = models.CharField(max_length=255, blank=True)
    created = models.DateTimeField(auto_now_add=True, validators=[validate_utc_datetime])

    def __str__(self):
        """User-friendly string representation of this model."""
        enrollee = "unknown"
        pathway = "unknown"

        if self.enrollment:
            enrollee = self.enrollment.user
            pathway = self.enrollment.pathway_id
        elif self.enrollment_allowed:
            enrollee = self.enrollment_allowed.user or self.enrollment_allowed.email
            pathway = self.enrollment_allowed.pathway_id

        return f"{self.state_transition} for {enrollee} in {pathway}"

    class Meta:
        """Model options."""

        verbose_name = _("Pathway Enrollment Audit")
        verbose_name_plural = _("Pathway Enrollment Audits")
        ordering = ["-created"]
