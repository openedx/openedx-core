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

from .models import CatalogCourse, CourseRun

if TYPE_CHECKING:

    class CatalogCourseWithRunCount(CatalogCourse):
        run_count: int


class CatalogCourseAdmin(admin.ModelAdmin):
    """
    The CatalogCourse model admin.
    """

    list_filter = ["org__short_name", "language"]
    list_display = [
        "display_name",
        "org_display",
        "course_code",
        "runs_summary",
        "url_slug",
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
        first_few_runs = obj.runs.order_by("-run")[:3]
        runs_summary = ", ".join(run.run for run in first_few_runs)
        if obj.run_count > 4:
            runs_summary += f", ... ({obj.run_count})"
        return format_html('<a href="{}">{}</a>', url, runs_summary)


admin.site.register(CatalogCourse, CatalogCourseAdmin)


class CourseRunAdmin(admin.ModelAdmin):
    """
    The CourseRun model admin.
    """

    list_display = ["display_name", "created_date", "catalog_course", "org_code", "course_code", "run", "warnings"]
    readonly_fields = ("course_id",)
    # There may be thousands of catalog courses, so don't use <select>
    raw_id_fields = ["catalog_course"]

    def get_readonly_fields(self, request, obj: CourseRun | None = None):
        if obj:  # editing an existing object
            return self.readonly_fields + ("run",)
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
