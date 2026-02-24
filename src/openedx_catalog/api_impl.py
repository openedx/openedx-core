"""
Implementation of the `openedx_catalog` API.
"""

import logging
from typing import overload

from opaque_keys.edx.keys import CourseKey
from organizations.api import ensure_organization  # type: ignore[import]
from organizations.api import exceptions as org_exceptions

from .models import CatalogCourse, CourseRun

log = logging.getLogger(__name__)

# These are the public API methods that anyone can use
__all__ = [
    "get_catalog_course",
    "update_catalog_course",
    "get_course_run",
    "sync_course_run_details",
    "create_course_run_for_modulestore_course_with",
]


@overload
def get_catalog_course(*, org_code: str, course_code: str) -> CatalogCourse: ...
@overload
def get_catalog_course(*, url_slug: str) -> CatalogCourse: ...
@overload
def get_catalog_course(*, pk: int) -> CatalogCourse: ...


def get_catalog_course(
    pk: int | None = None,
    url_slug: str = "",
    org_code: str = "",
    course_code: str = "",
) -> CatalogCourse:
    """
    Get a catalog course (set of runs).

    ⚠️ Does not check permissions or visibility rules.

    The CatalogCourse may not have any runs associated with it.
    """
    if pk:
        assert not org_code
        assert not url_slug
        return CatalogCourse.objects.get(pk=pk)
    if url_slug:
        assert not org_code
        assert not course_code
        org_code, course_code = url_slug.split(":", 1)
    # We might as well select_related org because we're joining to check the org__short_name field anyways.
    return CatalogCourse.objects.select_related("org").get(org__short_name=org_code, course_code=course_code)


def update_catalog_course(
    catalog_course: CatalogCourse | int,
    *,
    display_name: str | None = None,  # Specify a string to change the display name.
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
    if display_name:
        cc.display_name = display_name
        update_fields.append("display_name")
    if language_short:
        cc.language_short = language_short
        update_fields.append("language")
    if update_fields:
        cc.save(update_fields=update_fields)


def get_course_run(course_id: CourseKey) -> CourseRun:
    """
    Get a single course run.

    ⚠️ Does not check permissions or visibility rules.

    The CourseRun may or may not have content associated with it.

    Tip: to get all runs associated with a CatalogCourse, use
    `get_catalog_course(...).runs`
    """
    return CourseRun.objects.get(course_id__exact=course_id)


def sync_course_run_details(
    course_id: CourseKey,
    *,
    display_name: str | None,  # Specify a string to change the display name.
) -> None:
    """
    Update a `CourseRun` with details from a more authoritative model (e.g.
    `CourseOverview`). Currently the only field that can be updated is
    `display_name`.

    The name of this function reflects the fact that the `CourseRun` model is
    not currently a source of truth. So it's not a "rename the course" API, but
    rather a "some other part of the system already renamed the course" API,
    during a transition period until `CourseRun` is the main source of truth.

    Once `CourseRun` is the main source of truth, this will be replaced with a
    `update_course_run` API that will become the main way to rename a course.

    ⚠️ Does not check permissions.
    """
    run = CourseRun.objects.get(course_id=course_id)
    if display_name:
        run.display_name = display_name
        run.save(update_fields=["display_name"])


def create_course_run_for_modulestore_course_with(
    course_id: CourseKey,
    *,
    display_name: str,
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
    """
    # Note: this code shares a lot with the code in
    # openedx-platform/openedx/core/djangoapps/content/course_overviews/migrations/0030_backfill_...
    # but migrations should generally represent a point-in-time transformation, not call an API method that may continue
    # to be developed. So even though it's not DRY, the code is repeated here.

    org_code = course_id.org
    course_code = course_id.course
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
                f'The organization short code "{org_code}" exists in modulestore ({str(course_id)}) but '
                "not the Organizations table, and auto-creating organizations is disabled. You can resolve this by "
                "creating the Organization manually (e.g. from the Django admin) or turning on auto-creation. "
                "You can set active=False to prevent this Organization from being used other than for historical data. "
            ) from exc
        if org_data["short_name"] != org_code:
            # On most installations, the 'short_name' database column is case insensitive (unfortunately)
            log.warning(
                'The course with ID "%s" does not match its Organization.short_name "%s"',
                str(course_id),
                org_data["short_name"],
            )

        # Actually create the CatalogCourse. We use get_or_create just to be extra robust against race conditions, since
        # we don't care if another worker/thread/etc has beaten us to creating this.
        cc, _cc_created = CatalogCourse.objects.get_or_create(
            org_id=org_data["id"],
            course_code=course_code,
            defaults={
                "display_name": display_name,
                "language_short": language_short,
            },
        )

    new_run, created = CourseRun.objects.get_or_create(
        catalog_course=cc,
        run=course_id.run,
        course_id=course_id,
        defaults={"display_name": display_name},
    )

    if not created:
        log.warning('Expected to create CourseRun for "%s" but it already existed.', str(course_id))

    return new_run
