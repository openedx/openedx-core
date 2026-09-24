"""
Public API for Pathways.

This module manages the *content* half of a Pathway - its Items and, for now, the course runs that fulfill them. The
catalog half (display name, category, description, enrollment) is managed through ``openedx_catalog.api``.

Each Pathway implements exactly one Catalog Pathway, and lives in a Learning Package of its own. It owns its Items: each
Item belongs to exactly one Pathway, and can only be created once that Pathway exists. Course runs, on the other hand,
are shared: the same run may fulfill Items in several Pathways. Building a Pathway therefore goes:

1. `create_pathway_and_version()` creates the Pathway for a Catalog Pathway, with an empty first version;
2. `create_pathway_item_and_version()` creates each of its Items;
3. `create_next_pathway_version()` lists those Items in the Pathway, in order.

To find the Pathway of a Catalog Pathway, use `get_pathway_for_catalog_pathway()`. To list Pathways, list their Catalog
Pathways through ``openedx_catalog.api``, which is where the org, category and other catalog data live.

Two things this API deliberately does not do:

* It defines no grading. Whether a learner passed a course run is determined by that course's own grading policy, and is
  read from the course. Nothing here stores a copy.
* It does not evaluate fulfillment for a learner. See the applet README for the three moments at which that is meant to
  happen, and where that code belongs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.db.transaction import atomic

from openedx_catalog.models_api import CatalogPathway, CourseRun
from openedx_content import api as content_api
from openedx_content.models_api import LearningPackage, PublishableEntity

from .models import Pathway, PathwayItem, PathwayItemCourseRun, PathwayItemVersion, PathwayVersion, PathwayVersionItem

# `create_pathway`, `create_pathway_version`, `create_pathway_item` and `create_pathway_item_version` are deliberately
# left out: they are low-level building blocks for import/restore flows. See their docstrings.
__all__ = [
    "FulfillingCourseRun",
    "PathwayItemListEntry",
    "CourseRunListEntry",
    "create_pathway_and_version",
    "create_next_pathway_version",
    "get_pathway",
    "get_pathway_for_catalog_pathway",
    "create_pathway_item_and_version",
    "create_next_pathway_item_version",
    "get_pathway_item",
    "get_pathway_item_by_code",
    "get_items_in_pathway",
    "get_course_runs_for_item",
    "get_pathway_items_fulfilled_by_course_run",
    "get_pathways_containing_course_run",
]


@dataclass(frozen=True)
class FulfillingCourseRun:
    """
    One course run in the author-defined list that fulfills a Pathway Item.

    Position in that list is a priority, and priority says nothing about fulfillment: passing any one of an Item's runs
    fulfills it. It only decides which run a learner is enrolled in or shown, and ``enrollment_track`` is the track to
    enroll them in if this run uses several.
    """

    course_run: CourseRun
    enrollment_track: str = ""


def _reset_cached_draft(entity: PublishableEntity) -> None:
    """
    Drop a cached ``draft`` relation after creating a new version.

    Without this, a caller that holds the model instance it passed in would keep reading the previous draft. Mirrors
    what the containers API does.
    """
    if PublishableEntity.draft.is_cached(entity):  # type: ignore # pylint: disable=no-member
        PublishableEntity.draft.related.delete_cached_value(entity)  # type: ignore # pylint: disable=no-member


def _created_or_now(created: datetime | None) -> datetime:
    """
    Resolve an optional ``created`` argument, defaulting to the current time.

    Resolve it once per call and pass the result down, so that every row a single call creates shares one timestamp.
    """
    return created if created is not None else datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class PathwayItemListEntry:
    """
    One Item's place in a Pathway, resolved to a specific version of that Item.
    """

    pathway_item_version: PathwayItemVersion
    order_num: int

    @property
    def pathway_item(self) -> PathwayItem:
        return self.pathway_item_version.pathway_item


@dataclass(frozen=True)
class CourseRunListEntry:
    """
    One course run that fulfills a version of a Pathway Item. ``order_num`` is its priority, lowest first.
    """

    course_run: CourseRun
    order_num: int
    enrollment_track: str


def _validate_fulfilling_course_runs(course_runs: list[FulfillingCourseRun]) -> None:
    """
    Reject a run listed twice, which would make its priority ambiguous.

    The database enforces this too, but raising here gives callers a `ValidationError` rather than an `IntegrityError`.
    """
    seen: set[CourseRun.ID] = set()
    for entry in course_runs:
        if entry.course_run.id in seen:
            raise ValidationError(f'Course run "{entry.course_run}" is listed more than once for this Pathway Item.')
        seen.add(entry.course_run.id)


def _get_or_create_own_package(catalog_pathway: CatalogPathway, created: datetime) -> LearningPackage:
    """
    Get the Learning Package of the Pathway that implements ``catalog_pathway``, creating it if it doesn't exist yet.

    The package is named after the Catalog Pathway's key, which is unique across the instance, so that it's easy to
    recognize. The name isn't what keeps Pathways and packages one-to-one, though; the database constraints on
    `Pathway` are. So an existing package with that name is reused, and creating the Pathway in it fails unless no
    Pathway lives there yet - as when a deleted Pathway left its package behind.
    """
    package_ref = catalog_pathway.key_str
    try:
        return content_api.get_learning_package_by_ref(package_ref)
    except LearningPackage.DoesNotExist:
        return content_api.create_learning_package(
            package_ref=package_ref,
            title=catalog_pathway.title,
            created=created,
        )


def create_pathway(
    learning_package_id: LearningPackage.ID,
    /,
    catalog_pathway: CatalogPathway,
    created: datetime,
    created_by: int | None,
    *,
    can_stand_alone: bool = True,
) -> Pathway:
    """
    Create a new `Pathway` implementing ``catalog_pathway``, with no versions yet.

    This is a low-level building block for import/restore flows, which recreate an entity and then replay its version
    history with the original version numbers and timestamps. It is not part of the public API, because a Pathway with
    no versions is incomplete; use `create_pathway_and_version()` instead.

    The package must not hold another Pathway, and ``catalog_pathway`` must not be implemented by one; the database
    enforces both. The ``entity_ref`` is conventionally ``"pathway"``, since the package holds only this one, although
    callers should not assume that this will always be true.
    """
    with atomic():
        publishable_entity = content_api.create_publishable_entity(
            learning_package_id,
            "pathway",
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        pathway = Pathway.objects.create(
            publishable_entity=publishable_entity,
            catalog_pathway=catalog_pathway,
            learning_package_id=learning_package_id,
        )
    return pathway


def create_pathway_version(
    pathway_id: Pathway.ID,
    /,
    version_num: int,
    *,
    title: str,
    items: Iterable[PathwayItem] = (),
    created: datetime,
    created_by: int | None = None,
) -> PathwayVersion:
    """
    Create a new version of a `Pathway`, with an explicit version number.

    This is a low-level building block for import/restore flows, and is not part of the public API. Use
    `create_next_pathway_version()` instead, which numbers the version and carries unchanged fields forward.

    Args:
        pathway_id: The Pathway to create a version of.
        version_num: The version number, starting at 1.
        title: The Pathway's authoring-side title.
        items: The Pathway's Items, in the order learners see them. Each must belong to this Pathway.
        created: The creation date.
        created_by: The ID of the user who created this version.

    The Items are referenced unpinned and are declared as dependencies of this version, so that publishing a change to
    an Item registers as a change to the Pathway that contains it.

    Raises `ValidationError` if any of the Items belongs to a different Pathway.
    """
    item_list = list(items)
    foreign_items = [item for item in item_list if item.pathway_id != pathway_id]
    if foreign_items:
        codes = ", ".join(f'"{item.item_code}"' for item in foreign_items)
        raise ValidationError(f"Pathway Items {codes} belong to a different Pathway and can't be listed in this one.")

    with atomic():
        publishable_entity_version = content_api.create_publishable_entity_version(
            pathway_id,
            version_num=version_num,
            title=title,
            created=created,
            created_by=created_by,
            dependencies=[item.id for item in item_list],
        )
        pathway_version = PathwayVersion.objects.create(
            publishable_entity_version=publishable_entity_version,
            pathway_id=pathway_id,
        )
        PathwayVersionItem.objects.bulk_create(
            PathwayVersionItem(
                pathway_version=pathway_version,
                pathway_item=item,
                order_num=order_num,
            )
            for order_num, item in enumerate(item_list)
        )
    return pathway_version


def create_pathway_and_version(
    *,
    catalog_pathway: CatalogPathway,
    title: str,
    created: datetime | None = None,
    created_by: int | None = None,
    can_stand_alone: bool = True,
) -> tuple[Pathway, PathwayVersion]:
    """
    Create the `Pathway` implementing ``catalog_pathway``, with a first `PathwayVersion` that lists no Items yet.

    The Pathway gets a Learning Package of its own, named after the key of ``catalog_pathway``, so that publishing the
    package publishes exactly this Pathway and its Items.

    Items belong to their Pathway, so they can only be created once it exists: create them with
    `create_pathway_item_and_version()`, then list them with `create_next_pathway_version()`.

    Args:
        catalog_pathway: The learner-facing Catalog Pathway this Pathway implements, for its whole life.
        title: The Pathway's authoring-side title.
        created: Defaults to now.
        created_by: The ID of the user creating the Pathway.

    Raises `IntegrityError` if ``catalog_pathway`` is already implemented by a Pathway.
    """
    created = _created_or_now(created)
    with atomic():
        learning_package = _get_or_create_own_package(catalog_pathway, created)
        pathway = create_pathway(
            learning_package.id,
            catalog_pathway,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        pathway_version = create_pathway_version(
            pathway.id,
            1,
            title=title,
            created=created,
            created_by=created_by,
        )
    return pathway, pathway_version


def create_next_pathway_version(
    pathway: Pathway | Pathway.ID,
    /,
    *,
    title: str | None = None,
    items: Iterable[PathwayItem] | None = None,
    created: datetime | None = None,
    created_by: int | None = None,
) -> PathwayVersion:
    """
    Create the next version of a `Pathway`.

    Pass `None` for ``title`` or ``items`` to keep it as-is. ``created`` defaults to now.

    A new version is what records a change to the *definition*: an Item added, removed, or reordered, or the Pathway's
    own metadata changed. Editing the catalog copy never comes through here, and which Catalog Pathway the Pathway
    implements can't change at all.

    Raises `ValidationError` if any of the Items belongs to a different Pathway.
    """
    created = _created_or_now(created)
    with atomic():
        if isinstance(pathway, int):
            pathway = get_pathway(pathway)
        last_version = pathway.versioning.latest
        if last_version is None:
            raise PathwayVersion.DoesNotExist(f"{pathway} has no versions yet; use create_pathway_version().")
        assert isinstance(last_version, PathwayVersion)

        if items is None:
            items = [row.pathway_item for row in last_version.item_rows.all()]  # type: ignore

        next_version = create_pathway_version(
            pathway.id,
            last_version.version_num + 1,
            title=title if title is not None else last_version.title,
            items=items,
            created=created,
            created_by=created_by,
        )

    _reset_cached_draft(pathway.publishable_entity)
    return next_version


def get_pathway(pathway_id: Pathway.ID, /) -> Pathway:
    """
    Get a `Pathway` by its ID.
    """
    return Pathway.objects.get(pk=pathway_id)


def get_pathway_for_catalog_pathway(catalog_pathway: CatalogPathway | CatalogPathway.ID, /) -> Pathway | None:
    """
    Get the `Pathway` implementing ``catalog_pathway``, or `None` if there isn't one yet.

    The link isn't versioned, so this doesn't depend on what has been published: a Pathway implements its Catalog
    Pathway from the moment it's created, even while it has no published version, and even if it has been
    soft-deleted. Check ``pathway.versioning`` for that.
    """
    catalog_pathway_id = catalog_pathway.id if isinstance(catalog_pathway, CatalogPathway) else catalog_pathway
    try:
        return Pathway.objects.get(catalog_pathway_id=catalog_pathway_id)
    except Pathway.DoesNotExist:
        return None


def create_pathway_item(
    pathway: Pathway | Pathway.ID,
    /,
    item_code: str,
    created: datetime,
    created_by: int | None,
    *,
    can_stand_alone: bool = False,
) -> PathwayItem:
    """
    Create a new `PathwayItem` of ``pathway``, with no versions yet.

    This is a low-level building block for import/restore flows, and is not part of the public API, because an Item
    with no versions is incomplete. Use `create_pathway_item_and_version()` instead.

    The Item is created in the Pathway's Learning Package. Its ``entity_ref`` is conventionally derived as
    ``"pathway-item:{item_code}"``, although callers should not assume that this will always be true.

    ``can_stand_alone`` defaults to `False` because an Item only makes sense as part of its Pathway.
    """
    if isinstance(pathway, int):
        pathway = get_pathway(pathway)
    entity_ref = f"pathway-item:{item_code}"
    with atomic():
        publishable_entity = content_api.create_publishable_entity(
            pathway.learning_package_id,
            entity_ref,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        pathway_item = PathwayItem.objects.create(
            publishable_entity=publishable_entity,
            pathway=pathway,
            item_code=item_code,
        )
    return pathway_item


def create_pathway_item_version(
    pathway_item_id: PathwayItem.ID,
    /,
    version_num: int,
    *,
    title: str,
    course_runs: Iterable[FulfillingCourseRun | CourseRun] = (),
    created: datetime,
    created_by: int | None = None,
) -> PathwayItemVersion:
    """
    Create a new version of a `PathwayItem`, with an explicit version number.

    This is a low-level building block for import/restore flows, and is not part of the public API. Use
    `create_next_pathway_item_version()` instead, which numbers the version and carries unchanged fields forward.

    Args:
        pathway_item_id: The Item to create a version of.
        version_num: The version number, starting at 1.
        title: The Item's title, as shown to learners and authors.
        course_runs: The runs that fulfill this Item, highest priority first. Passing a bare `CourseRun` is shorthand
            for one with no enrollment track. Priority decides which run a learner is enrolled in or shown; every run
            in the list fulfills the Item regardless of its position.
        created: The creation date.
        created_by: The ID of the user who created this version.

    Raises `ValidationError` if a run is listed twice.
    """
    entries = [run if isinstance(run, FulfillingCourseRun) else FulfillingCourseRun(course_run=run)
               for run in course_runs]
    _validate_fulfilling_course_runs(entries)

    with atomic():
        publishable_entity_version = content_api.create_publishable_entity_version(
            pathway_item_id,
            version_num=version_num,
            title=title,
            created=created,
            created_by=created_by,
        )
        item_version = PathwayItemVersion.objects.create(
            publishable_entity_version=publishable_entity_version,
            pathway_item_id=pathway_item_id,
        )
        PathwayItemCourseRun.objects.bulk_create(
            PathwayItemCourseRun(
                pathway_item_version=item_version,
                course_run=entry.course_run,
                order_num=order_num,
                enrollment_track=entry.enrollment_track,
            )
            for order_num, entry in enumerate(entries)
        )
    return item_version


def create_pathway_item_and_version(
    pathway: Pathway | Pathway.ID,
    /,
    item_code: str,
    *,
    title: str,
    course_runs: Iterable[FulfillingCourseRun | CourseRun] = (),
    created: datetime | None = None,
    created_by: int | None = None,
    can_stand_alone: bool = False,
) -> tuple[PathwayItem, PathwayItemVersion]:
    """
    Create a new `PathwayItem` of ``pathway`` and its first `PathwayItemVersion` together.

    The Item belongs to ``pathway`` for good, but creating it doesn't list it in the Pathway yet; do that with
    `create_next_pathway_version()`. ``created`` defaults to now. See `create_pathway_item_version()` for
    ``course_runs``.
    """
    created = _created_or_now(created)
    with atomic():
        pathway_item = create_pathway_item(
            pathway,
            item_code,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        item_version = create_pathway_item_version(
            pathway_item.id,
            1,
            title=title,
            course_runs=course_runs,
            created=created,
            created_by=created_by,
        )
    return pathway_item, item_version


def create_next_pathway_item_version(
    pathway_item: PathwayItem | PathwayItem.ID,
    /,
    *,
    title: str | None = None,
    course_runs: Iterable[FulfillingCourseRun | CourseRun] | None = None,
    created: datetime | None = None,
    created_by: int | None = None,
) -> PathwayItemVersion:
    """
    Create the next version of a `PathwayItem`.

    Pass `None` for ``title`` or ``course_runs`` to keep it as-is. ``created`` defaults to now.

    Note that narrowing an Item - dropping a course run that used to fulfill it - is not meant to take a credential away
    from a learner who has already earned one. Retroactive evaluation only ever grants.
    """
    created = _created_or_now(created)
    with atomic():
        if isinstance(pathway_item, int):
            pathway_item = get_pathway_item(pathway_item)
        last_version = pathway_item.versioning.latest
        if last_version is None:
            raise PathwayItemVersion.DoesNotExist(
                f'Pathway Item "{pathway_item.item_code}" has no versions yet; use create_pathway_item_version().'
            )
        assert isinstance(last_version, PathwayItemVersion)

        if course_runs is None:
            course_runs = [
                FulfillingCourseRun(course_run=row.course_run, enrollment_track=row.enrollment_track)
                for row in last_version.course_run_rows.select_related("course_run")  # type: ignore
            ]

        next_version = create_pathway_item_version(
            pathway_item.id,
            last_version.version_num + 1,
            title=title if title is not None else last_version.title,
            course_runs=course_runs,
            created=created,
            created_by=created_by,
        )

    _reset_cached_draft(pathway_item.publishable_entity)
    return next_version


def get_pathway_item(pathway_item_id: PathwayItem.ID, /) -> PathwayItem:
    """
    Get a `PathwayItem` by its ID.
    """
    return PathwayItem.objects.get(pk=pathway_item_id)


def get_pathway_item_by_code(pathway: Pathway | Pathway.ID, /, item_code: str) -> PathwayItem:
    """
    Get a `PathwayItem` by its code, within the Pathway it belongs to.
    """
    pathway_id = pathway.id if isinstance(pathway, Pathway) else pathway
    return PathwayItem.objects.get(pathway_id=pathway_id, item_code=item_code)


def get_items_in_pathway(pathway: Pathway, *, published: bool) -> list[PathwayItemListEntry]:
    """
    Get the Items in the draft or published version of a `Pathway`, in order.

    Items are referenced unpinned, so each Item is resolved to its own draft or published version, matching the
    ``published`` argument. Items that have no version in that state - never published, or soft-deleted - are skipped,
    so the returned list can be shorter than the Pathway's list of Items.

    Raises `PathwayVersion.DoesNotExist` if the Pathway itself has no version in the requested state.
    """
    pathway_version = pathway.versioning.published if published else pathway.versioning.draft
    if pathway_version is None:
        raise PathwayVersion.DoesNotExist(
            f'{pathway} has no {"published" if published else "draft"} version.'
        )
    assert isinstance(pathway_version, PathwayVersion)

    entries = []
    rows = pathway_version.item_rows.select_related(  # type: ignore
        "pathway_item__publishable_entity__draft__version",
        "pathway_item__publishable_entity__published__version",
    )
    for row in rows:
        item_version = row.pathway_item.versioning.published if published else row.pathway_item.versioning.draft
        if item_version is None:
            continue
        assert isinstance(item_version, PathwayItemVersion)
        entries.append(PathwayItemListEntry(pathway_item_version=item_version, order_num=row.order_num))
    return entries


def get_course_runs_for_item(pathway_item: PathwayItem, *, published: bool) -> list[CourseRunListEntry]:
    """
    Get the course runs that fulfill the draft or published version of an Item, highest priority first.

    Passing any one of these runs fulfills the Item, whatever its priority. Returns an empty list if the Item has no
    version in the requested state.
    """
    item_version = pathway_item.versioning.published if published else pathway_item.versioning.draft
    if item_version is None:
        return []
    assert isinstance(item_version, PathwayItemVersion)
    return [
        CourseRunListEntry(
            course_run=row.course_run,
            order_num=row.order_num,
            enrollment_track=row.enrollment_track,
        )
        for row in item_version.course_run_rows.select_related("course_run")  # type: ignore
    ]


def get_pathway_items_fulfilled_by_course_run(
    course_run: CourseRun | CourseRun.ID,
    *,
    published: bool,
) -> QuerySet[PathwayItem]:
    """
    Get the Items that passing this course run would fulfill.

    This is the "which Items does this run count towards" query that a course passing-status signal needs in order to
    know what to re-evaluate. It answers for the whole site; narrowing to one learner's Pathways is the caller's job.
    """
    course_run_id = course_run.id if isinstance(course_run, CourseRun) else course_run
    state = "published" if published else "draft"
    return PathwayItem.objects.filter(
        **{
            f"publishable_entity__{state}__version__pathwayitemversion__course_run_rows__course_run_id": course_run_id,
        }
    ).distinct()


def get_pathways_containing_course_run(
    course_run: CourseRun | CourseRun.ID,
    *,
    published: bool,
) -> QuerySet[Pathway]:
    """
    Get the Pathways whose draft or published definition this course run counts towards.

    Items are never shared between Pathways, but course runs are, so one run may count towards several Pathways through
    different Items. This is the fan-out behind a course passing-status signal: narrow the result to the Pathways the
    learner is enrolled in, then re-evaluate their Items with `get_pathway_items_fulfilled_by_course_run()`.

    Both the Pathway and its Items are resolved in the requested state, so an Item that is no longer listed in the
    Pathway's version, or whose version no longer lists the run, doesn't count, even though the Item still belongs to
    the Pathway.
    """
    course_run_id = course_run.id if isinstance(course_run, CourseRun) else course_run
    state = "published" if published else "draft"
    return Pathway.objects.filter(
        **{
            f"publishable_entity__{state}__version__pathwayversion__item_rows__pathway_item__"
            f"publishable_entity__{state}__version__pathwayitemversion__course_run_rows__course_run_id": course_run_id,
        }
    ).distinct()
