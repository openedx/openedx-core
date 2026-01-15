from django.contrib import admin

from .models import (
    AssessmentCriteria,
    AssessmentCriteriaGroup,
    StudentAssessmentCriteriaStatus,
    StudentCompetencyStatus,
)


@admin.register(AssessmentCriteriaGroup)
class AssessmentCriteriaGroupAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "parent", "ordering", "logic_operator", "competency_tag")
    list_filter = ("logic_operator",)
    search_fields = ("name",)


@admin.register(AssessmentCriteria)
class AssessmentCriteriaAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "rule_type", "rule", "retake_rule", "competency_tag", "object_tag")
    list_filter = ("rule_type", "retake_rule")
    search_fields = ("rule",)


@admin.register(StudentAssessmentCriteriaStatus)
class StudentAssessmentCriteriaStatusAdmin(admin.ModelAdmin):
    list_display = ("id", "assessment_criteria", "user", "status", "timestamp")
    list_filter = ("status",)
    search_fields = ("user__username", "user__email")


@admin.register(StudentCompetencyStatus)
class StudentCompetencyStatusAdmin(admin.ModelAdmin):
    list_display = ("id", "competency_tag", "user", "status", "timestamp")
    list_filter = ("status",)
    search_fields = ("user__username", "user__email")
