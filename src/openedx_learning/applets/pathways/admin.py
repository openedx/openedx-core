"""
Django Admin pages for Pathways models.

These pages are read-oriented on purpose. Pathway content is versioned, and versions are immutable, so editing rows in
place here would corrupt the record of what the definition was at a given moment. Create new versions through
``openedx_learning.api`` instead.
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from openedx_catalog.models_api import CatalogPathway

from .models import Pathway, PathwayItem, PathwayItemCourseRun, PathwayItemVersion, PathwayVersion, PathwayVersionItem

__all__ = [
    "PathwayAdmin",
    "PathwayVersionAdmin",
    "PathwayItemAdmin",
    "PathwayItemVersionAdmin",
]


class ReadOnlyAdminMixin:
    """
    Mixin that makes a versioned model visible in the admin but not editable.
    """

    # pylint: disable=unused-argument
    # The arguments are part of the ModelAdmin/InlineModelAdmin signatures Django calls.

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


class PathwayAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    The Pathway model admin.
    """

    list_display = ["pathway_code", "learning_package", "catalog_pathway", "created"]
    list_filter = ["learning_package"]
    search_fields = ["pathway_code"]

    def get_queryset(self, request) -> QuerySet[Pathway]:
        """Pull in the catalog entry that points at each Pathway, so the list doesn't query per row"""
        return super().get_queryset(request).select_related("publishable_entity__catalog_pathway")

    @admin.display(description=_("Catalog Pathway"))
    def catalog_pathway(self, obj: Pathway) -> CatalogPathway | None:
        """The learner-facing catalog entry this Pathway defines, if one points at it"""
        return getattr(obj.publishable_entity, "catalog_pathway", None)


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


class PathwayVersionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    The PathwayVersion model admin.
    """

    list_display = ["__str__", "pathway", "item_count"]
    list_filter = ["pathway__learning_package"]
    inlines = [PathwayVersionItemInline]

    @admin.display(description=_("Items"))
    def item_count(self, obj: PathwayVersion) -> int:
        """How many Items this version of the Pathway holds"""
        return obj.item_rows.count()  # type: ignore


admin.site.register(PathwayVersion, PathwayVersionAdmin)


class PathwayItemAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    The PathwayItem model admin.
    """

    list_display = ["item_code", "learning_package", "created"]
    list_filter = ["learning_package"]
    search_fields = ["item_code"]


admin.site.register(PathwayItem, PathwayItemAdmin)


class PathwayItemCourseRunInline(admin.TabularInline):
    """The course runs that fulfill a version of a Pathway Item."""

    model = PathwayItemCourseRun
    fields = ["order_num", "course_run", "is_default", "enrollment_track"]
    readonly_fields = ["order_num", "course_run", "is_default", "enrollment_track"]
    can_delete = False
    extra = 0

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class PathwayItemVersionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
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
