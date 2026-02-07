"""
Tagging app admin
"""
from __future__ import annotations

from django.contrib import admin

from .models import ObjectTag, Tag, Taxonomy

admin.site.register(Taxonomy)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    """
    Admin definition for Tag model
    """
    autocomplete_fields = ["parent"]
    search_fields = ["value", "external_id"]
    list_display = ["__str__", "taxonomy", "external_id"]
    list_filter = ["taxonomy"]

    def has_add_permission(self, request):
        """
        Don't create Tags using the django admin. Use the API or UI.
        """
        return False


@admin.register(ObjectTag)
class ObjectTagAdmin(admin.ModelAdmin):
    """
    Admin definition for ObjectTag model
    """
    fields = ["object_id", "taxonomy", "tag", "_value"]
    autocomplete_fields = ["tag"]
    list_display = ["object_id", "export_id", "value"]
    readonly_fields = ["object_id"]

    def has_add_permission(self, request):
        """
        Don't create ObjectTags using the django admin. Use the API or UI.
        """
        return False
