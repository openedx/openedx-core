"""
Learning API URLs for hosts that serve learners, such as the LMS.

A host includes this module next to ``openedx_learning.urls``, or instead of it where it serves no
authoring. ``openedx_learning.urls`` stays authoring-only, so a host that serves authors does not expose
learner routes by including it. The module path is a contract with hosts: renaming it breaks their
URL configuration.
"""

from django.urls import include, path

from .applets.cbe.rest_api import learner_urls as cbe_learner_urls

urlpatterns = [path("cbe/", include(cbe_learner_urls))]
