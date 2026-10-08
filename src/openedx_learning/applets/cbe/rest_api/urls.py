"""
CBE API URLs.
"""

from django.urls import include, path

from .v1 import urls as v1_urls

# Namespaces the whole CBE API, so routes reverse as "cbe:<name>". Declared here rather than in
# v1/ because the namespace spans every version, and rather than on the app-root module because
# that one aggregates every applet.
app_name = "cbe"

urlpatterns = [path("v1/", include(v1_urls))]
