"""
Implementation of the `openedx_catalog` API.
"""

import logging
from typing import overload

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from opaque_keys.edx.keys import CourseKey
from organizations.api import ensure_organization  # type: ignore[import]
from organizations.api import exceptions as org_exceptions

from openedx_content.models_api import PublishableEntity

from .models import CatalogCourse, CatalogPathway, CourseRun, PathwayCategory, PathwayEnrollment
from .models.pathway_category import get_default_pathway_category

log = logging.getLogger(__name__)

# These are the public API methods that anyone can use
__all__ = [
    "get_catalog_course",
    "update_catalog_course",
    "delete_catalog_course",
    "get_course_run",
    "sync_course_run_details",
    "create_course_run_for_modulestore_course_with",
    "delete_course_run",
    "get_default_pathway_category",
    "get_pathway_category",
    "get_catalog_pathway",
    "create_catalog_pathway",
    "update_catalog_pathway",
    "set_catalog_pathway_content",
    "delete_catalog_pathway",
    "enroll_in_pathway",
    "unenroll_from_pathway",
    "is_enrolled_in_pathway",
    "get_pathway_enrollments",
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


# Pathways (catalog side).
#
# A Pathway is split into a catalog half (these models) and a versioned content half in
# `openedx_learning.applets.pathways`. See the openedx_learning ADR 0007. The functions below only touch the catalog
# half; creating and versioning the *definition* of a Pathway is done through `openedx_learning.api`, which also links
# the definition to its `CatalogPathway` via `set_catalog_pathway_content()`.


# `get_default_pathway_category` is part of this API too, and is re-exported via `__all__`. It's defined next to the
# model because the `CatalogPathway.category` field default needs it as well.


def get_pathway_category(category_code: str) -> PathwayCategory:
    """
    Get a `PathwayCategory` by its stable code.

    ⚠️ Does not check permissions.
    """
    return PathwayCategory.objects.get(category_code=category_code)


@overload
def get_catalog_pathway(*, org_code: str, pathway_code: str) -> CatalogPathway: ...
@overload
def get_catalog_pathway(*, key_str: str) -> CatalogPathway: ...
@overload
def get_catalog_pathway(*, pk: CatalogPathway.ID) -> CatalogPathway: ...


def get_catalog_pathway(
    pk: CatalogPathway.ID | None = None,
    key_str: str = "",
    org_code: str = "",
    pathway_code: str = "",
) -> CatalogPathway:
    """
    Get a catalog pathway.

    ⚠️ Does not check permissions or visibility rules.

    The `CatalogPathway` may not have a definition yet: `content_entity` is `None` until `openedx_learning.api` links
    one. To resolve it to the actual Pathway, use `openedx_learning.api.get_pathway_for_catalog_pathway()`.
    """
    assert pk or key_str or (org_code and pathway_code)
    if pk:
        assert not org_code
        assert not key_str
        return CatalogPathway.objects.get(pk=pk)
    if key_str:
        assert key_str.startswith("catalog-pathway:")
        assert not org_code
        assert not pathway_code
        _, org_code, pathway_code = key_str.split(":", 2)
    # We might as well select_related org because we're joining to check the org__short_name field anyways.
    return CatalogPathway.objects.select_related("org").get(org__short_name=org_code, pathway_code=pathway_code)


def create_catalog_pathway(
    *,
    org_code: str,
    pathway_code: str,
    title: str = "",
    category: PathwayCategory | None = None,
    description: str = "",
) -> CatalogPathway:
    """
    Create a `CatalogPathway`.

    The `Organization` identified by `org_code` must already exist. Pass `category=None` to use the default category.

    This creates only the catalog half of a Pathway. Use `openedx_learning.api` to create the versioned definition and
    link it to this entry.

    ⚠️ Does not check permissions.
    """
    pathway = CatalogPathway(
        pathway_code=pathway_code,
        title=title,
        description=description,
        # Only pass the category if given, so that the field default (which queries for the shipped category) runs
        # only when it's actually needed.
        **({"category": category} if category is not None else {}),
    )
    pathway.org_code = org_code  # Resolves the Organization by short_name; raises Organization.DoesNotExist.
    pathway.save()
    return pathway


def update_catalog_pathway(
    catalog_pathway: CatalogPathway | CatalogPathway.ID,
    *,
    title: str | None = None,
    category: PathwayCategory | None = None,
    description: str | None = None,
) -> None:
    """
    Update a `CatalogPathway`. Pass `None` for a field to leave it unchanged.

    None of these edits create a new content version: catalog copy and the Pathway definition change at different rates
    and are edited by different people, which is the whole point of the split.

    ⚠️ Does not check permissions.
    """
    if isinstance(catalog_pathway, CatalogPathway):
        cp = catalog_pathway
    else:
        cp = CatalogPathway.objects.get(pk=catalog_pathway)

    update_fields = []
    for field_name, value in (
        ("title", title),
        ("category", category),
        ("description", description),
    ):
        if value is not None:
            setattr(cp, field_name, value)
            update_fields.append(field_name)
    if update_fields:
        cp.save(update_fields=update_fields + ["modified"])


def set_catalog_pathway_content(
    catalog_pathway: CatalogPathway | CatalogPathway.ID,
    content_entity: PublishableEntity | PublishableEntity.ID | None,
) -> None:
    """
    Point a `CatalogPathway` at the `PublishableEntity` holding its versioned definition, or pass `None` to unlink it.

    A definition can serve only one catalog entry, so this raises an `IntegrityError` if another `CatalogPathway`
    already points at the same entity.

    `openedx_catalog` cannot tell a Pathway entity apart from any other `PublishableEntity`, so no such check happens
    here. Prefer `openedx_learning.api` (`create_pathway()`, `link_catalog_pathway()`), which only ever passes entities
    it created as Pathways.

    ⚠️ Does not check permissions.
    """
    if isinstance(catalog_pathway, CatalogPathway):
        cp = catalog_pathway
    else:
        cp = CatalogPathway.objects.get(pk=catalog_pathway)

    if content_entity is None or isinstance(content_entity, PublishableEntity):
        cp.content_entity = content_entity
    else:
        cp.content_entity_id = content_entity
    cp.save(update_fields=["content_entity", "modified"])


def delete_catalog_pathway(catalog_pathway: CatalogPathway | CatalogPathway.ID) -> None:
    """
    Delete a `CatalogPathway`, along with its enrollments.

    The versioned definition it pointed at, if any, is left in place in its learning package; it is simply no longer
    linked from the catalog.

    ⚠️ Does not check permissions.
    """
    if isinstance(catalog_pathway, CatalogPathway):
        cp = catalog_pathway
    else:
        cp = CatalogPathway.objects.get(pk=catalog_pathway)
    cp.delete()


def enroll_in_pathway(user_id: int, catalog_pathway: CatalogPathway | CatalogPathway.ID) -> PathwayEnrollment:
    """
    Enroll a learner in a `CatalogPathway`, or return their existing active enrollment.

    If the learner had previously unenrolled, their existing row is reactivated rather than replaced, so the original
    enrollment date is kept.

    Enrollment does not pin a content version: progress is always evaluated against whatever is published at the time,
    so that authoring changes reach learners who are already enrolled.

    ⚠️ Does not check permissions.
    """
    pathway_id = catalog_pathway.id if isinstance(catalog_pathway, CatalogPathway) else catalog_pathway
    with transaction.atomic():
        # Lock the row so a concurrent unenroll can't slip in between reading `is_active` and writing it back.
        enrollment, created = PathwayEnrollment.objects.select_for_update().get_or_create(
            user_id=user_id, catalog_pathway_id=pathway_id
        )
        if not created and not enrollment.is_active:
            enrollment.is_active = True
            enrollment.save(update_fields=["is_active", "modified"])
    return enrollment


def unenroll_from_pathway(user_id: int, catalog_pathway: CatalogPathway | CatalogPathway.ID) -> None:
    """
    Unenroll a learner from a `CatalogPathway`. A no-op if they aren't enrolled.

    The enrollment row is deactivated, not deleted.

    ⚠️ Does not check permissions.
    """
    pathway_id = catalog_pathway.id if isinstance(catalog_pathway, CatalogPathway) else catalog_pathway
    PathwayEnrollment.objects.filter(user_id=user_id, catalog_pathway_id=pathway_id, is_active=True).update(
        is_active=False, modified=timezone.now()
    )


def is_enrolled_in_pathway(user_id: int, catalog_pathway: CatalogPathway | CatalogPathway.ID) -> bool:
    """
    Check whether this learner is actively enrolled in this `CatalogPathway`.

    ⚠️ Does not check permissions.
    """
    pathway_id = catalog_pathway.id if isinstance(catalog_pathway, CatalogPathway) else catalog_pathway
    return PathwayEnrollment.objects.filter(user_id=user_id, catalog_pathway_id=pathway_id, is_active=True).exists()


def get_pathway_enrollments(user_id: int, *, include_inactive: bool = False) -> QuerySet[PathwayEnrollment]:
    """
    Get a learner's pathway enrollments, most recent first.

    Only active enrollments are returned unless ``include_inactive`` is set.

    ⚠️ Does not check permissions or visibility rules.
    """
    enrollments = PathwayEnrollment.objects.filter(user_id=user_id).select_related("catalog_pathway")
    if not include_inactive:
        enrollments = enrollments.filter(is_active=True)
    return enrollments
