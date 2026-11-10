"""
Implementation of the course part of the `openedx_catalog` API: catalog courses and course runs.
"""

import logging
from collections.abc import Iterable
from typing import overload

from opaque_keys.edx.keys import CourseKey
from organizations.api import ensure_organization  # type: ignore[import]
from organizations.api import exceptions as org_exceptions

from ..models import CatalogCourse, CourseRun

log = logging.getLogger(__name__)

# These are the public API methods that anyone can use
__all__ = [
    "get_catalog_course",
    "update_catalog_course",
    "delete_catalog_course",
    "get_course_run",
    "get_course_run_ids",
    "sync_course_run_details",
    "create_course_run_for_modulestore_course_with",
    "delete_course_run",
]


@overload
def get_catalog_course(*, org_code: str, course_code: str) -> CatalogCourse: ...
@overload
def get_catalog_course(*, key_str: str) -> CatalogCourse: ...
@overload
def get_catalog_course(*, pk: CatalogCourse.ID) -> CatalogCourse: ...


def get_catalog_course(
    pk: CatalogCourse.ID | None = None,
    key_str: str = "",
    org_code: str = "",
    course_code: str = "",
) -> CatalogCourse:
    """
    Get a catalog course (set of runs).

    ⚠️ Does not check permissions or visibility rules.

    The CatalogCourse may not have any runs associated with it.
    """
    assert pk or key_str or (org_code and course_code)
    if pk:
        assert not org_code
        assert not key_str
        return CatalogCourse.objects.get(pk=pk)
    if key_str:
        assert key_str.startswith("catalog-course:")
        assert not org_code
        assert not course_code
        _, org_code, course_code = key_str.split(":", 2)
    # We might as well select_related org because we're joining to check the org__short_name field anyways.
    return CatalogCourse.objects.select_related("org").get(org__short_name=org_code, course_code=course_code)


def update_catalog_course(
    catalog_course: CatalogCourse | CatalogCourse.ID,
    *,
    title: str | None = None,  # Specify a string to change the title (display name).
    # The short language code (one of settings.ALL_LANGUAGES), e.g. "en", "es", "zh_HANS"
    language_short: str | None = None,
) -> None:
    """
    Update a `CatalogCourse`.

    ⚠️ Does not check permissions.
    """
    if isinstance(catalog_course, CatalogCourse):
        cc = catalog_course
    else:
        cc = CatalogCourse.objects.get(pk=catalog_course)

    update_fields = []
    if title:
        cc.title = title
        update_fields.append("title")
    if language_short:
        cc.language_short = language_short
        update_fields.append("language")
    if update_fields:
        cc.save(update_fields=update_fields)


def delete_catalog_course(catalog_course: CatalogCourse | CatalogCourse.ID) -> None:
    """
    Delete a `CatalogCourse`. This will fail with a `ProtectedError` if any runs exist.

    ⚠️ Does not check permissions.
    ⚠️ Does not emit any course lifecycle events.
    """
    if isinstance(catalog_course, CatalogCourse):
        cc = catalog_course
    else:
        cc = CatalogCourse.objects.get(pk=catalog_course)
    cc.delete()


def get_course_run(course_key: CourseKey) -> CourseRun:
    """
    Get a single course run.

    ⚠️ Does not check permissions or visibility rules.

    The CourseRun may or may not have content associated with it.

    Tip: to get all runs associated with a CatalogCourse, use
    `get_catalog_course(...).runs`
    """
    return CourseRun.objects.get(course_key__exact=course_key)


def get_course_run_ids(course_keys: Iterable[CourseKey]) -> dict[CourseKey, int]:
    """
    Return a {course_key: CourseRun.id} mapping for the keys that resolve to a CourseRun.

    A key with no matching CourseRun is absent from the result.

    ⚠️ Does not check permissions or visibility rules.
    """
    course_keys = list(course_keys)
    if not course_keys:
        return {}
    # course_key's accessor is typed CourseKey | None regardless of null=False (opaque_keys'
    # own stub), so filter here instead of asserting past mypy.
    return {
        key: run_id
        for key, run_id in CourseRun.objects.filter(course_key__in=course_keys).values_list("course_key", "id")
        if key is not None
    }


def sync_course_run_details(
    course_key: CourseKey,
    *,
    title: str | None,  # Specify a string to change the title (display name).
) -> None:
    """
    Update a `CourseRun` with details from a more authoritative model (e.g.
    `CourseOverview`). Currently the only field that can be updated is
    `title` (display name).

    The name of this function reflects the fact that the `CourseRun` model is
    not currently a source of truth. So it's not a "rename the course" API, but
    rather a "some other part of the system already renamed the course" API,
    during a transition period until `CourseRun` is the main source of truth.

    Once `CourseRun` is the main source of truth, this will be replaced with a
    `update_course_run` API that will become the main way to rename a course.

    ⚠️ Does not check permissions.
    ⚠️ Does not emit any course lifecycle events.
    """
    run = CourseRun.objects.get(course_key=course_key)
    if title:
        run.title = title
        run.save(update_fields=["title"])


def create_course_run_for_modulestore_course_with(
    course_key: CourseKey,
    *,
    title: str,
    # The short language code (in openedx-platform, this is one of settings.ALL_LANGUAGES), e.g. "en", "es", "zh_HANS"
    language_short: str | None = None,
) -> CourseRun:
    """
    Create a `CourseRun` (and, if necessary, its corresponding `CatalogCourse`).
    This API is meant to be used for data synchonrization purposes (keeping the
    new catalog models in sync with modulestore), and is not a generic "create a
    course run" API.

    If the `CourseRun` already exists, this will log a warning.

    The `created` timestamp of the `CourseRun` will be set to now, so this is
    not meant for historical data (use a data migration).

    ⚠️ Does not check permissions.
    ⚠️ Does not emit any course lifecycle events.
    """
    # Note: this code shares a lot with the code in
    # openedx-platform/openedx/core/djangoapps/content/course_overviews/migrations/0030_backfill_...
    # but migrations should generally represent a point-in-time transformation, not call an API method that may continue
    # to be developed. So even though it's not DRY, the code is repeated here.

    org_code = course_key.org
    course_code = course_key.course
    try:
        cc = CatalogCourse.objects.get(org__short_name=org_code, course_code=course_code)
    except CatalogCourse.DoesNotExist:
        cc = None

    if not cc:
        # Create the catalog course.

        # First, ensure that the Organization exists.
        try:
            org_data = ensure_organization(org_code)
        except org_exceptions.InvalidOrganizationException as exc:
            # Note: IFF the org exists among the modulestore courses but not in the Organizations database table,
            # and if auto-create is disabled (it's enabled by default), this will raise InvalidOrganizationException. It
            # would be up to the operator to decide how they want to resolve that.
            raise ValueError(
                f'The organization short code "{org_code}" exists in modulestore ({str(course_key)}) but '
                "not the Organizations table, and auto-creating organizations is disabled. You can resolve this by "
                "creating the Organization manually (e.g. from the Django admin) or turning on auto-creation. "
                "You can set active=False to prevent this Organization from being used other than for historical data. "
            ) from exc
        if org_data["short_name"] != org_code:
            # On most installations, the 'short_name' database column is case insensitive (unfortunately)
            log.warning(
                'The course with ID "%s" does not match its Organization.short_name "%s"',
                str(course_key),
                org_data["short_name"],
            )

        # Actually create the CatalogCourse. We use get_or_create just to be extra robust against race conditions, since
        # we don't care if another worker/thread/etc has beaten us to creating this.
        cc, _cc_created = CatalogCourse.objects.get_or_create(
            org_id=org_data["id"],
            course_code=course_code,
            defaults={
                "title": title,
                **({"language_short": language_short} if language_short else {}),
            },
        )

    new_run, created = CourseRun.objects.get_or_create(
        catalog_course=cc,
        run_code=course_key.run,
        course_key=course_key,
        defaults={"title": title},
    )

    if not created:
        log.warning('Expected to create CourseRun for "%s" but it already existed.', str(course_key))

    return new_run


def delete_course_run(course_key: CourseKey) -> None:
    """
    Delete a `CourseRun`.

    For now, this method is only useful for keeping the `CourseRun` data in sync
    with other models like `CourseOverview` that are used as a source of truth.
    Calling this method will not yet affect most parts of the system, so you
    should only use this if the course run is a "placeholder" course that has no
    content yet, or the course has already been deleted in the other parts of
    the platform (e.g. modulestore). In the future, we will invert this
    dependency, and calling this _would_ cascade to delete `CourseOverview`, and
    perhaps other records as well.

    (This method does not delete content, if any content is associated with the
    run, and that is not expected to change. In the future, a separate API
    method may implement "delete course + content + (optionally) enrollments +
    student state + etc.".)

    This may fail with a `ProtectedError` or other `IntegrityError` subclass if
    there are still active references to the course run.

    ⚠️ Does not check permissions.
    ⚠️ Does not emit any course lifecycle events.
    """
    CourseRun.objects.get(course_key=course_key).delete()
