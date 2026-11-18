"""
Courses API: the mapping between course runs and their Learning Packages.

See :ref:`openedx-content-adr-0012`. Code that needs a course run's Learning
Package must look it up through this API, never by ``package_ref``.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from django.db import IntegrityError
from django.db.models import QuerySet
from django.db.transaction import atomic
from opaque_keys.edx.keys import CourseKey

from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CourseRun

from ..publishing import api as publishing_api
from ..publishing.models import LearningPackage
from .models import CourseContent

# The public API that will be re-exported by openedx_content.api
# is listed in the __all__ entries below. Internal helper functions that are
# private to this module should start with an underscore. If a function does not
# start with an underscore AND it is not in __all__, that function is considered
# to be callable only by other applets in the openedx_content package.
__all__ = [
    "create_course_content",
    "delete_course_content",
    "get_course_content",
    "get_course_runs_for_learning_package",
    "get_learning_package_for_course_run",
    "get_or_create_course_content",
]


def _get_course_run(course_run: CourseRun | CourseKey) -> CourseRun:
    if isinstance(course_run, CourseRun):
        return course_run
    return catalog_api.get_course_run(course_run)


def _course_run_filter(course_run: CourseRun | CourseKey) -> dict:
    """Get filter kwargs to find the CourseContent for a course run, without loading the CourseRun."""
    if isinstance(course_run, CourseRun):
        return {"course_run": course_run}
    return {"course_run__course_key__exact": course_run}


def _generate_package_ref(course_key: CourseKey) -> str:
    """
    Generate a ``package_ref`` for a new course Learning Package.

    It includes the course key so that it is human readable, plus a random
    suffix so that nothing can assume it is 1:1 with the course key (see ADR
    0012, decision 4).
    """
    return f"{course_key}:{secrets.token_hex(4)}"


def create_course_content(
    course_run: CourseRun | CourseKey,
    /,
    learning_package: LearningPackage | LearningPackage.ID | None = None,
    *,
    created: datetime | None = None,
) -> CourseContent:
    """
    Associate a course run with a Learning Package that holds its content.

    If ``learning_package`` is ``None``, a new Learning Package is created for
    the course run. Raises ``IntegrityError`` if the course run already has a
    Learning Package. Raises ``CourseRun.DoesNotExist`` if no such course run
    exists.
    """
    course_run = _get_course_run(course_run)
    assert course_run.course_key is not None  # for type checking; the column is not nullable
    with atomic():
        if learning_package is None:
            learning_package = publishing_api.create_learning_package(
                package_ref=_generate_package_ref(course_run.course_key),
                title=course_run.title,
                created=created,
            )
        elif not isinstance(learning_package, LearningPackage):
            learning_package = publishing_api.get_learning_package(learning_package)
        return CourseContent.objects.create(course_run=course_run, learning_package=learning_package)


def get_or_create_course_content(
    course_run: CourseRun | CourseKey,
    /,
    *,
    created: datetime | None = None,
) -> tuple[CourseContent, bool]:
    """
    Get the ``CourseContent`` for a course run, creating it (along with a new
    Learning Package) if the course run doesn't have one yet.

    Returns a ``(course_content, created)`` tuple, like Django's
    ``get_or_create()``. This is safe to call concurrently: if another caller
    creates the ``CourseContent`` first, the Learning Package created by this
    call is rolled back and the other caller's ``CourseContent`` is returned.

    ⚠️ Only call this from code that is deliberately setting up a course run to
    store its content in ``openedx_content``. Never call it from code that is
    just reading content, or merely looking up a course run's content could
    create an empty Learning Package for it. Use ``get_course_content()`` or
    ``get_learning_package_for_course_run()`` to read.

    Raises ``CourseRun.DoesNotExist`` if no such course run exists.
    """
    try:
        return get_course_content(course_run), False
    except CourseContent.DoesNotExist:
        pass
    try:
        # create_course_content() uses atomic(), so if this fails, its new Learning Package is rolled back too.
        return create_course_content(course_run, created=created), True
    except IntegrityError:
        # Another caller created this course run's CourseContent concurrently.
        return get_course_content(course_run), False


def get_course_content(course_run: CourseRun | CourseKey, /) -> CourseContent:
    """
    Get the ``CourseContent`` for a course run.

    Raises ``CourseContent.DoesNotExist`` if the course run has no content in
    ``openedx_content``.
    """
    return CourseContent.objects.select_related("course_run", "learning_package").get(**_course_run_filter(course_run))


def get_learning_package_for_course_run(course_run: CourseRun | CourseKey, /) -> LearningPackage:
    """
    Get the Learning Package that holds the content of the given course run.

    Raises ``LearningPackage.DoesNotExist`` if the course run has no content in
    ``openedx_content``.
    """
    filter_kwargs = {f"course_contents__{k}": v for k, v in _course_run_filter(course_run).items()}
    return LearningPackage.objects.get(**filter_kwargs)


def get_course_runs_for_learning_package(
    learning_package: LearningPackage | LearningPackage.ID,
    /,
) -> QuerySet[CourseRun]:
    """
    Get the course run(s) whose content is held in the given Learning Package.

    By convention there is at most one, but callers must handle receiving more
    than one (see ADR 0012).
    """
    if isinstance(learning_package, LearningPackage):
        learning_package = learning_package.id
    return CourseRun.objects.filter(content__learning_package_id=learning_package).order_by("course_key")


def delete_course_content(course_run: CourseRun | CourseKey, /) -> None:
    """
    Remove the association between a course run and its Learning Package.

    The Learning Package itself (and all of its content) is left in place. This
    is a no-op if the course run has no ``CourseContent``.
    """
    CourseContent.objects.filter(**_course_run_filter(course_run)).delete()
