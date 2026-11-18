"""
CBE API v1 URLs for learners.
"""

from django.urls import re_path

from . import learner_views

urlpatterns = [
    # The pattern accepts any non-empty value, so a malformed key gets the view's 400 instead of the resolver's 404.
    re_path(
        r"^courses/(?P<course_key>[\s\S]+)/criteria/$",
        learner_views.LearnerCourseCriteriaView.as_view(),
        name="course-criteria",
    ),
]
