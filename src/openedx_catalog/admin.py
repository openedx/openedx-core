"""
Django Admin pages for openedx_catalog.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from django.contrib import admin
from django.db.models import Count, QuerySet
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import CatalogCourse, CatalogPathway, CourseRun, PathwayCategory, PathwayEnrollment

if TYPE_CHECKING:

    class CatalogCourseWithRunCount(CatalogCourse):
        run_count: int

    class PathwayCategoryWithPathwayCount(PathwayCategory):
        pathway_count: int


class CatalogCourseAdmin(admin.ModelAdmin):
    """
    The CatalogCourse model admin.
    """

    list_filter = ["org__short_name", "language"]
    list_display = [
        "title",
        "org_display",
        "course_code",
        "runs_summary",
        "key_str",
        "created_date",
        "language",
    ]

    def get_readonly_fields(self, request, obj: CatalogCourse | None = None) -> tuple[str, ...]:
        if obj:  # editing an existing object
            return ("org", "course_code")
        return tuple()

    def get_queryset(self, request) -> QuerySet[CatalogCourseWithRunCount]:
        """Add the 'run_count' to the list_display queryset"""
        qs = super().get_queryset(request)
        qs = qs.annotate(run_count=Count("runs"))
        return qs

    @admin.display(description="Organization", ordering="org__short_name")
    def org_display(self, obj: CatalogCourse) -> str:
        """Display the organization, only showing the short_name if different from full name"""
        if obj.org.name == obj.org.short_name:
            return obj.org.short_name
        return str(obj.org)

    @admin.display(description=_("Created"), ordering="created")
    def created_date(self, obj: CatalogCourse) -> datetime.date:
        """Display the created date without the timestamp"""
        return obj.created.date()

    @admin.display(description=_("Runs"))
    def runs_summary(self, obj: CatalogCourseWithRunCount) -> str:
        """Summarize the runs"""
        if obj.run_count == 0:
            return "-"
        url = reverse("admin:openedx_catalog_courserun_changelist") + f"?catalog_course={obj.pk}"
        first_few_runs = obj.runs.order_by("-run_code")[:3]
        runs_summary = ", ".join(run.run_code for run in first_few_runs)
        if obj.run_count > 4:
            runs_summary += f", ... ({obj.run_count})"
        return format_html('<a href="{}">{}</a>', url, runs_summary)


admin.site.register(CatalogCourse, CatalogCourseAdmin)


class CourseRunAdmin(admin.ModelAdmin):
    """
    The CourseRun model admin.
    """

    list_display = ["title", "created_date", "catalog_course", "org_code", "course_code", "run_code", "warnings"]
    readonly_fields = ("course_key",)
    # There may be thousands of catalog courses, so don't use <select>
    raw_id_fields = ["catalog_course"]

    def get_readonly_fields(self, request, obj: CourseRun | None = None):
        if obj:  # editing an existing object
            return self.readonly_fields + ("run_code",)
        return self.readonly_fields

    @admin.display(description=_("Created"), ordering="created")
    def created_date(self, obj: CourseRun) -> datetime.date:
        """Display the created date without the timestamp"""
        return obj.created.date()

    def warnings(self, obj: CourseRun) -> str | None:
        """Display warnings of any detected issues"""
        if obj.course_code != obj.catalog_course.course_code:
            return "🚨 Critical: mismatched course code"
        if obj.org_code != obj.catalog_course.org.short_name:
            if obj.org_code.lower() == obj.catalog_course.org.short_name.lower():
                return "⚠️ Warning: Incorrect org code capitalization"
            return "🚨 Critical: mismatched org code"
        # It would be nice to indicate if there's associated course content or not, but openedx-core isn't aware of
        # modulestore so we have no way to check that here.
        return None


admin.site.register(CourseRun, CourseRunAdmin)


class PathwayCategoryAdmin(admin.ModelAdmin):
    """
    The PathwayCategory model admin.

    Renaming a category changes what learners see. It does not change the authoring-side terminology, which is always
    "Pathway".
    """

    list_display = ["name", "category_code", "pathways_summary"]
    search_fields = ["name", "category_code"]

    def get_readonly_fields(self, request, obj: PathwayCategory | None = None) -> tuple[str, ...]:
        if obj:  # editing an existing object; the code is what other systems key off
            return ("category_code",)
        return tuple()

    def get_queryset(self, request) -> QuerySet[PathwayCategoryWithPathwayCount]:
        """Add the 'pathway_count' to the list_display queryset"""
        qs = super().get_queryset(request)
        qs = qs.annotate(pathway_count=Count("pathways"))
        return qs

    @admin.display(description=_("Pathways"), ordering="pathway_count")
    def pathways_summary(self, obj: PathwayCategoryWithPathwayCount) -> str:
        """Link to the catalog pathways using this category"""
        if obj.pathway_count == 0:
            return "-"
        url = reverse("admin:openedx_catalog_catalogpathway_changelist") + f"?category={obj.pk}"
        return format_html('<a href="{}">{}</a>', url, obj.pathway_count)


admin.site.register(PathwayCategory, PathwayCategoryAdmin)


class CatalogPathwayAdmin(admin.ModelAdmin):
    """
    The CatalogPathway model admin.

    This edits only the catalog half of a Pathway. The Items a learner must complete live on the content side, in the
    openedx_learning app, and are versioned there.
    """

    list_filter = ["org__short_name", "category"]
    list_display = [
        "title",
        "category",
        "org_display",
        "pathway_code",
        "key_str",
        "content_entity",
        "created_date",
        "modified",
    ]
    list_select_related = ["org", "category", "content_entity"]
    search_fields = ["title", "pathway_code"]

    def get_readonly_fields(self, request, obj: CatalogPathway | None = None) -> tuple[str, ...]:
        # The definition is linked through openedx_learning.api, which is the only place that can check that the entity
        # really is a Pathway. Show it, but don't offer a <select> over every PublishableEntity in the system.
        if obj:  # editing an existing object
            return ("content_entity", "org", "pathway_code")
        return ("content_entity",)

    @admin.display(description="Organization", ordering="org__short_name")
    def org_display(self, obj: CatalogPathway) -> str:
        """Display the organization, only showing the short_name if different from full name"""
        if obj.org.name == obj.org.short_name:
            return obj.org.short_name
        return str(obj.org)

    @admin.display(description=_("Created"), ordering="created")
    def created_date(self, obj: CatalogPathway) -> datetime.date:
        """Display the created date without the timestamp"""
        return obj.created.date()


admin.site.register(CatalogPathway, CatalogPathwayAdmin)


class PathwayEnrollmentAdmin(admin.ModelAdmin):
    """
    The PathwayEnrollment model admin.
    """

    list_display = ["user", "catalog_pathway", "is_active", "created_date", "modified"]
    list_filter = ["is_active", "catalog_pathway__category"]
    # There may be very many users and a fair number of pathways, so don't use <select>
    raw_id_fields = ["user", "catalog_pathway"]

    @admin.display(description=_("Enrolled"), ordering="created")
    def created_date(self, obj: PathwayEnrollment) -> datetime.date:
        """Display the enrollment date without the timestamp"""
        return obj.created.date()


admin.site.register(PathwayEnrollment, PathwayEnrollmentAdmin)
