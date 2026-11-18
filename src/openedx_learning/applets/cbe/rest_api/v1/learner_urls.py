"""
CBE API v1 URLs for learners.
"""

from django.urls import re_path

from . import views

urlpatterns = [
    # A broad pattern rather than the platform's COURSE_KEY_PATTERN, so every malformed value reaches the
    # view and gets its 400, and old slash-separated course keys still match.
    re_path(
        r"^courses/(?P<course_key>.+)/criteria/$",
        views.CourseCompetencyCriteriaView.as_view(),
        name="course-criteria",
    ),
]
