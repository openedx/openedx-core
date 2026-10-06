"""
CBE API v1 URLs.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("rule_profiles", views.CompetencyRuleProfileView, basename="rule_profile")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "competencies/<int:tag_id>/criteria/",
        views.CompetencyCriterionCreateView.as_view(),
        name="criterion-create",
    ),
    path(
        "competencies/<int:tag_id>/criteria/<int:criterion_id>/",
        views.CompetencyCriterionDeleteView.as_view(),
        name="criterion-delete",
    ),
]
