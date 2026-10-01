"""
CBE API v1 URLs.
"""

from django.urls.conf import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("rule_profiles", views.CompetencyRuleProfileView, basename="rule_profile")

urlpatterns = [
    path("", include(router.urls)),
]
