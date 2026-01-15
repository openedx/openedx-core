"""
Assessment criteria API URLs.
"""
from django.urls import include, path

from .rest_api import urls

app_name = "oel_assessment_criteria"
urlpatterns = [path("", include(urls))]
