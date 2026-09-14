"""
Django Admin pages for CBE models.
"""
from django.contrib import admin

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin

from .models import (
    CompetencyMasteryStatus,
    CompetencyTaxonomy,
    StudentCompetencyCriteriaGroupStatus,
    StudentCompetencyCriteriaStatus,
    StudentCompetencyStatus,
)

__all__ = [
    "CompetencyTaxonomyAdmin",
    "CompetencyMasteryStatusAdmin",
    "StudentCompetencyCriteriaStatusAdmin",
    "StudentCompetencyCriteriaGroupStatusAdmin",
    "StudentCompetencyStatusAdmin",
]


class CompetencyTaxonomyAdmin(admin.ModelAdmin):
    """
    The CompetencyTaxonomy model admin.
    """
    list_display = ["name", "export_id", "enabled"]
    list_filter = ["enabled"]


class CompetencyMasteryStatusAdmin(ReadOnlyModelAdmin):
    """
    The CompetencyMasteryStatus model admin.
    """
    list_display = ["id", "status"]


class StudentCompetencyCriteriaStatusAdmin(ReadOnlyModelAdmin):
    """
    The StudentCompetencyCriteriaStatus model admin.

    Deliberately read-only: an editable page would be the staff-correction
    path, which ADR-0004 Decision 6 requires to take a row lock and recompute
    every ancestor status, and none of that machinery exists yet.
    """
    list_display = ["user", "criterion", "status", "created", "modified"]
    list_filter = ["status"]
    list_select_related = ["user", "criterion", "status"]


class StudentCompetencyCriteriaGroupStatusAdmin(ReadOnlyModelAdmin):
    """
    The StudentCompetencyCriteriaGroupStatus model admin.

    Deliberately read-only: an editable page would be the staff-correction
    path, which ADR-0004 Decision 6 requires to take a row lock and recompute
    every ancestor status, and none of that machinery exists yet.
    """
    list_display = ["user", "group", "status", "created", "modified"]
    list_filter = ["status"]
    list_select_related = ["user", "group", "status"]


class StudentCompetencyStatusAdmin(ReadOnlyModelAdmin):
    """
    The StudentCompetencyStatus model admin.

    Deliberately read-only: an editable page would be the staff-correction
    path, which ADR-0004 Decision 6 requires to take a row lock and recompute
    every ancestor status, and none of that machinery exists yet.
    """
    list_display = ["user", "tag", "status", "created", "modified"]
    list_filter = ["status"]
    list_select_related = ["user", "tag", "status"]


admin.site.register(CompetencyTaxonomy, CompetencyTaxonomyAdmin)
admin.site.register(CompetencyMasteryStatus, CompetencyMasteryStatusAdmin)
admin.site.register(StudentCompetencyCriteriaStatus, StudentCompetencyCriteriaStatusAdmin)
admin.site.register(StudentCompetencyCriteriaGroupStatus, StudentCompetencyCriteriaGroupStatusAdmin)
admin.site.register(StudentCompetencyStatus, StudentCompetencyStatusAdmin)
