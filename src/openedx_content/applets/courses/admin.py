"""
Django admin for ``courses`` models
"""

from django.contrib import admin

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin

from .models import CourseContent


@admin.register(CourseContent)
class CourseContentAdmin(ReadOnlyModelAdmin):
    """
    Django admin for CourseContent model
    """

    list_display = ("course_run", "learning_package")
    search_fields = ("course_run__course_key", "learning_package__package_ref", "learning_package__title")
    list_select_related = ("course_run", "learning_package")
