"""
Django Admin pages for Pathways models.

These pages are read-oriented on purpose. Pathway content is versioned, and versions are immutable, so editing rows in
place here would corrupt the record of what the definition was at a given moment. Create new versions through
``openedx_learning.api`` instead.
"""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin

from .models import Pathway, PathwayItem, PathwayItemCourseRun, PathwayItemVersion, PathwayVersion, PathwayVersionItem

__all__ = [
    "PathwayAdmin",
    "PathwayVersionAdmin",
    "PathwayItemAdmin",
    "PathwayItemVersionAdmin",
]


class PathwayAdmin(ReadOnlyModelAdmin):
    """
    The Pathway model admin.
    """

    list_display = ["catalog_pathway", "learning_package", "created"]
    list_filter = ["catalog_pathway__category"]
    list_select_related = ["catalog_pathway__org", "learning_package", "publishable_entity"]
    search_fields = ["catalog_pathway__title", "catalog_pathway__pathway_code"]


admin.site.register(Pathway, PathwayAdmin)


class PathwayVersionItemInline(admin.TabularInline):
    """The ordered list of Items in a Pathway version."""

    model = PathwayVersionItem
    fields = ["order_num", "pathway_item"]
    readonly_fields = ["order_num", "pathway_item"]
    can_delete = False
    extra = 0

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class PathwayVersionAdmin(ReadOnlyModelAdmin):
    """
    The PathwayVersion model admin.
    """

    list_display = ["__str__", "pathway", "item_count"]
    list_filter = ["pathway__catalog_pathway__category"]
    list_select_related = ["pathway__catalog_pathway__org", "publishable_entity_version__entity"]
    inlines = [PathwayVersionItemInline]

    @admin.display(description=_("Items"))
    def item_count(self, obj: PathwayVersion) -> int:
        """How many Items this version of the Pathway holds"""
        return obj.item_rows.count()  # type: ignore


admin.site.register(PathwayVersion, PathwayVersionAdmin)


class PathwayItemAdmin(ReadOnlyModelAdmin):
    """
    The PathwayItem model admin.
    """

    list_display = ["item_code", "pathway", "created"]
    list_filter = ["pathway__catalog_pathway__category"]
    list_select_related = ["pathway__catalog_pathway__org", "publishable_entity"]
    search_fields = ["item_code", "pathway__catalog_pathway__title", "pathway__catalog_pathway__pathway_code"]


admin.site.register(PathwayItem, PathwayItemAdmin)


class PathwayItemCourseRunInline(admin.TabularInline):
    """The course runs that fulfill a version of a Pathway Item, in priority order."""

    model = PathwayItemCourseRun
    fields = ["order_num", "course_run", "enrollment_track"]
    readonly_fields = ["order_num", "course_run", "enrollment_track"]
    can_delete = False
    extra = 0

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class PathwayItemVersionAdmin(ReadOnlyModelAdmin):
    """
    The PathwayItemVersion model admin.
    """

    list_display = ["__str__", "pathway_item", "course_run_count"]
    inlines = [PathwayItemCourseRunInline]

    @admin.display(description=_("Fulfilling course runs"))
    def course_run_count(self, obj: PathwayItemVersion) -> int:
        """How many course runs can fulfill this version of the Item"""
        return obj.course_run_rows.count()  # type: ignore


admin.site.register(PathwayItemVersion, PathwayItemVersionAdmin)
