"""
CBE API URLs for learners.
"""

from django.urls import include, path

from .v1 import learner_urls as v1_learner_urls

# A namespace distinct from the authoring "cbe", because a project that includes both URL modules cannot
# register two includes under one namespace (urls.W005).
app_name = "cbe_learner"

urlpatterns = [path("v1/", include(v1_learner_urls))]
