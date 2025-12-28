"""
Django Admin pages for Collection models.
"""
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Collection


class CollectionAdmin(admin.ModelAdmin):
    """
    The Collection model admin.

    Allows users to easily disable/enable (aka soft delete and restore) or bulk delete Collections.
    """
    readonly_fields = ["key", "learning_package"]
    list_filter = ["enabled"]
    list_display = ["key", "title", "enabled", "modified"]
    fieldsets = [
        (
            "",
            {
                "fields": ["key", "learning_package"],
            }
        ),
        (
            _("Edit only in Studio"),
            {
                "fields": ["title", "enabled", "description", "created_by"],
                "description": _("⚠  Changes made here should be done in Studio Django Admin, not the LMS."),
            }
        ),
    ]

    def has_add_permission(self, request, *args, **kwargs):
        """
        Disallow adding new collections via Django Admin.
        """
        return False  # pragma: no cover


admin.site.register(Collection, CollectionAdmin)
