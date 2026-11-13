"""
Tests of the Courses API (CourseContent)
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError
from django.db.models import ProtectedError
from opaque_keys.edx.keys import CourseKey

from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CourseRun
from openedx_content import api
from openedx_content.models_api import CourseContent, LearningPackage

pytestmark = pytest.mark.django_db

COURSE_KEY = CourseKey.from_string("course-v1:OpenedX+CC101+2026")
OTHER_COURSE_KEY = CourseKey.from_string("course-v1:OpenedX+CC101+2027")


@pytest.fixture(name="course_run")
def _course_run() -> CourseRun:
    return catalog_api.create_course_run_for_modulestore_course_with(COURSE_KEY, title="Course Content 101")


@pytest.fixture(name="other_course_run")
def _other_course_run() -> CourseRun:
    return catalog_api.create_course_run_for_modulestore_course_with(OTHER_COURSE_KEY, title="Course Content 101 (b)")


def test_create_course_content_new_learning_package(course_run: CourseRun) -> None:
    """Creating CourseContent without a Learning Package creates a new one."""
    course_content = api.create_course_content(COURSE_KEY)

    assert course_content.course_run == course_run
    lp = course_content.learning_package
    assert lp.title == "Course Content 101"
    # The package_ref includes the course key, but is not identical to it:
    assert lp.package_ref.startswith(f"{COURSE_KEY}:")
    assert lp.package_ref != str(COURSE_KEY)


def test_package_refs_are_unique(course_run: CourseRun) -> None:
    """Re-creating a course run's Learning Package does not collide with its previous one."""
    lp1 = api.create_course_content(course_run).learning_package
    api.delete_course_content(course_run)
    lp2 = api.create_course_content(course_run).learning_package
    assert lp1.id != lp2.id
    assert lp1.package_ref != lp2.package_ref


def test_create_course_content_existing_learning_package(course_run: CourseRun) -> None:
    """Creating CourseContent with an existing Learning Package (by object or ID)"""
    lp = api.create_learning_package(package_ref="existing-lp", title="Existing LP")
    assert api.create_course_content(course_run, lp).learning_package == lp
    api.delete_course_content(course_run)
    assert api.create_course_content(course_run, lp.id).learning_package == lp
    # No extra Learning Packages were created:
    assert LearningPackage.objects.count() == 1


def test_create_course_content_twice(course_run: CourseRun) -> None:
    """A course run can have at most one CourseContent."""
    api.create_course_content(course_run)
    with pytest.raises(IntegrityError):
        api.create_course_content(course_run)


def test_create_course_content_no_course_run() -> None:
    """A CourseRun must exist"""
    with pytest.raises(CourseRun.DoesNotExist):
        api.create_course_content(COURSE_KEY)
    assert LearningPackage.objects.count() == 0


def test_get(course_run: CourseRun) -> None:
    """Test the getter APIs, by course key and by CourseRun"""
    with pytest.raises(CourseContent.DoesNotExist):
        api.get_course_content(COURSE_KEY)
    with pytest.raises(LearningPackage.DoesNotExist):
        api.get_learning_package_for_course_run(COURSE_KEY)

    course_content = api.create_course_content(course_run)
    lp = course_content.learning_package

    for key_or_run in (COURSE_KEY, course_run):
        assert api.get_course_content(key_or_run) == course_content
        assert api.get_learning_package_for_course_run(key_or_run) == lp
    assert list(api.get_course_runs_for_learning_package(lp)) == [course_run]
    assert list(api.get_course_runs_for_learning_package(lp.id)) == [course_run]


def test_multiple_course_runs_share_learning_package(course_run: CourseRun, other_course_run: CourseRun) -> None:
    """
    Several course runs may share one Learning Package.

    ADR 0012 requires this test, so that nothing (including the schema) comes to
    depend on the relationship being 1:1.
    """
    lp = api.create_course_content(course_run).learning_package
    api.create_course_content(other_course_run, lp)

    assert api.get_learning_package_for_course_run(COURSE_KEY) == lp
    assert api.get_learning_package_for_course_run(OTHER_COURSE_KEY) == lp
    assert list(api.get_course_runs_for_learning_package(lp)) == [course_run, other_course_run]


def test_delete_course_content(course_run: CourseRun) -> None:
    """Deleting CourseContent leaves the Learning Package in place."""
    lp = api.create_course_content(course_run).learning_package
    api.delete_course_content(COURSE_KEY)
    with pytest.raises(CourseContent.DoesNotExist):
        api.get_course_content(COURSE_KEY)
    assert LearningPackage.objects.filter(id=lp.id).exists()
    # Deleting again is a no-op:
    api.delete_course_content(COURSE_KEY)


def test_learning_package_in_use_cannot_be_deleted(course_run: CourseRun) -> None:
    """A Learning Package can't be deleted while a course run uses it."""
    lp = api.create_course_content(course_run).learning_package
    with pytest.raises(ProtectedError):
        lp.delete()


def test_deleting_course_run_deletes_course_content(course_run: CourseRun) -> None:
    """Deleting a CourseRun deletes its CourseContent, but not its Learning Package."""
    lp = api.create_course_content(course_run).learning_package
    catalog_api.delete_course_run(COURSE_KEY)
    assert not CourseContent.objects.exists()
    assert LearningPackage.objects.filter(id=lp.id).exists()
