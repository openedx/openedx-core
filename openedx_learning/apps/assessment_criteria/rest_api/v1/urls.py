"""
Assessment criteria API v1 URLs.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("criteria-groups", views.AssessmentCriteriaGroupView, basename="assessment-criteria-group")
router.register("criteria", views.AssessmentCriteriaView, basename="assessment-criteria")
router.register(
    "student-criteria-statuses",
    views.StudentAssessmentCriteriaStatusView,
    basename="student-assessment-criteria-status",
)
router.register(
    "student-competency-statuses",
    views.StudentCompetencyStatusView,
    basename="student-competency-status",
)

urlpatterns = [path("", include(router.urls))]
