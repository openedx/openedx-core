"""
Tagging API URLs.
"""

from django.urls import include, path

from .rest_api import urls

app_name = "oel_tagging"
urlpatterns = [path("", include(urls))]
