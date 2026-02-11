"""Django admin for Pathways."""

from django.contrib import admin

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin

from .models import Pathway, PathwayEnrollment, PathwayEnrollmentAllowed, PathwayEnrollmentAudit, PathwayStep


class PathwayStepInline(admin.TabularInline):
    """Inline table for pathway steps within a pathway."""

    model = PathwayStep
    fields = ["order", "step_type", "context_key"]
    ordering = ["order"]
    extra = 0


@admin.register(Pathway)
class PathwayAdmin(admin.ModelAdmin):
    """Admin for Pathway model."""

    list_display = ["key", "display_name", "org", "is_active", "sequential", "created"]
    list_filter = ["is_active", "sequential", "invite_only", "org"]
    search_fields = ["key", "display_name"]
    inlines = [PathwayStepInline]


class PathwayEnrollmentAuditInline(admin.TabularInline):
    """Inline admin for PathwayEnrollmentAudit records."""

    model = PathwayEnrollmentAudit
    fk_name = "enrollment"
    extra = 0
    exclude = ["enrollment_allowed"]
    readonly_fields = [
        "state_transition",
        "enrolled_by",
        "reason",
        "org",
        "role",
        "created",
    ]

    def has_add_permission(self, request, obj=None):
        """Disable manual creation of audit records."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Disable deletion of audit records."""
        return False


@admin.register(PathwayEnrollment)
class PathwayEnrollmentAdmin(admin.ModelAdmin):
    """Admin for PathwayEnrollment model."""

    raw_id_fields = ("user",)
    autocomplete_fields = ["pathway"]
    list_display = ["id", "user", "pathway", "is_active", "created"]
    list_filter = ["pathway__key", "created", "is_active"]
    search_fields = ["id", "user__username", "pathway__key", "pathway__display_name"]
    inlines = [PathwayEnrollmentAuditInline]


class PathwayEnrollmentAllowedAuditInline(admin.TabularInline):
    """Inline admin for PathwayEnrollmentAudit records related to enrollment allowed."""

    model = PathwayEnrollmentAudit
    fk_name = "enrollment_allowed"
    extra = 0
    exclude = ["enrollment"]
    readonly_fields = [
        "state_transition",
        "enrolled_by",
        "reason",
        "org",
        "role",
        "created",
    ]

    def has_add_permission(self, request, obj=None):
        """Disable manual creation of audit records."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Disable deletion of audit records."""
        return False


@admin.register(PathwayEnrollmentAllowed)
class PathwayEnrollmentAllowedAdmin(admin.ModelAdmin):
    """Admin for PathwayEnrollmentAllowed model."""

    autocomplete_fields = ["pathway"]
    list_display = ["id", "email", "get_user", "pathway", "created"]
    list_filter = ["pathway", "created"]
    search_fields = ["email", "user__username", "user__email", "pathway__key"]
    readonly_fields = ["user", "created"]
    inlines = [PathwayEnrollmentAllowedAuditInline]

    def get_user(self, obj):
        """Get the associated user, if any."""
        return obj.user.username if obj.user else "-"

    get_user.short_description = "User"  # type: ignore[attr-defined]


@admin.register(PathwayEnrollmentAudit)
class PathwayEnrollmentAuditAdmin(ReadOnlyModelAdmin):
    """Admin configuration for PathwayEnrollmentAudit model."""

    list_display = ["id", "state_transition", "enrolled_by", "get_enrollee", "get_pathway", "created", "org", "role"]
    list_filter = ["state_transition", "created", "org", "role"]
    search_fields = [
        "enrolled_by__username",
        "enrolled_by__email",
        "enrollment__user__username",
        "enrollment__user__email",
        "enrollment_allowed__email",
        "enrollment__pathway__key",
        "enrollment_allowed__pathway__key",
        "reason",
    ]

    def get_enrollee(self, obj):
        """Get the enrollee (user or email)."""
        if obj.enrollment:
            return obj.enrollment.user.username
        elif obj.enrollment_allowed:
            return obj.enrollment_allowed.user.username if obj.enrollment_allowed.user else obj.enrollment_allowed.email
        return "-"

    get_enrollee.short_description = "Enrollee"  # type: ignore[attr-defined]

    def get_pathway(self, obj):
        """Get the pathway title."""
        if obj.enrollment:
            return obj.enrollment.pathway_id
        elif obj.enrollment_allowed:
            return obj.enrollment_allowed.pathway_id
        return "-"

    get_pathway.short_description = "Pathway"  # type: ignore[attr-defined]
