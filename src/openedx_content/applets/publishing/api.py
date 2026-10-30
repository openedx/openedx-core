"""
Publishing API

Please look at the models.py file for more information about the kinds of data
are stored in this app.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from functools import partial
from typing import ContextManager, NamedTuple, Optional

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import connection
from django.db.models import F, OuterRef, Prefetch, Q, QuerySet, Subquery
from django.db.transaction import atomic, on_commit

from openedx_django_lib.fields import create_hash_digest

from . import signals
from .contextmanagers import DraftChangeLogContext
from .models import (
    Draft,
    DraftChangeLog,
    DraftChangeLogRecord,
    DraftSideEffect,
    LearningPackage,
    PublishableContentModelRegistry,
    PublishableEntity,
    PublishableEntityMixin,
    PublishableEntityVersion,
    PublishableEntityVersionDependency,
    PublishableEntityVersionMixin,
    PublishLog,
    PublishLogRecord,
    PublishSideEffect,
)
from .models.publish_log import Published

# The public API that will be re-exported by openedx_content.api
# is listed in the __all__ entries below. Internal helper functions that are
# private to this module should start with an underscore. If a function does not
# start with an underscore AND it is not in __all__, that function is considered
# to be callable only by other applets in the openedx_content package.
__all__ = [
    "get_learning_package",
    "get_learning_package_by_ref",
    "create_learning_package",
    "update_learning_package",
    "learning_package_exists",
    "create_publishable_entity",
    "create_publishable_entity_version",
    "get_publishable_entity",
    "get_publishable_entity_by_ref",
    "get_publishable_entities",
    "get_last_publish",
    "get_all_drafts",
    "get_entities_with_unpublished_changes",
    "get_entities_with_unpublished_deletes",
    "publish_all_drafts",
    "publish_from_drafts",
    "get_draft_version",
    "get_published_version",
    "get_entity_draft_history",
    "get_entity_publish_history",
    "get_entity_publish_history_entries",
    "get_entity_version_contributors",
    "set_draft_version",
    "soft_delete_draft",
    "reset_drafts_to_published",
    "register_publishable_models",
    "filter_publishable_entities",
    "bulk_draft_changes_for",
]


def get_learning_package(learning_package_id: LearningPackage.ID, /) -> LearningPackage:
    """
    Get LearningPackage by ID.
    """
    return LearningPackage.objects.get(id=learning_package_id)


def get_learning_package_by_ref(package_ref: str) -> LearningPackage:
    """
    Get LearningPackage by its package_ref.

    Can throw a NotFoundError
    """
    return LearningPackage.objects.get(package_ref=package_ref)


def create_learning_package(
    package_ref: str, title: str, description: str = "", created: datetime | None = None
) -> LearningPackage:
    """
    Create a new LearningPackage.

    The ``package_ref`` must be unique.

    Errors that can be raised:

    * django.core.exceptions.ValidationError
    """
    if not created:
        created = datetime.now(tz=timezone.utc)

    package = LearningPackage(
        package_ref=package_ref,
        title=title,
        description=description,
        created=created,
        updated=created,
    )
    package.full_clean()
    package.save()
    new_id = package.id

    def send_event():
        signals.LEARNING_PACKAGE_CREATED.send_event(
            learning_package=signals.LearningPackageEventData(id=new_id, title=title),
        )

    on_commit(send_event)

    return package


def update_learning_package(
    learning_package_id: LearningPackage.ID,
    /,
    package_ref: str | None = None,
    title: str | None = None,
    description: str | None = None,
    updated: datetime | None = None,
) -> LearningPackage:
    """
    Make an update to LearningPackage metadata.

    Note that LearningPackage itself is not versioned (only stuff inside it is).
    """
    lp = LearningPackage.objects.get(id=learning_package_id)

    # If no changes were requested, there's nothing to update, so just return
    # the LearningPackage as-is.
    if all(field is None for field in [package_ref, title, description, updated]):
        return lp

    if package_ref is not None:
        lp.package_ref = package_ref
    if title is not None:
        lp.title = title
    if description is not None:
        lp.description = description

    # updated is a bit different–we auto-generate it if it's not explicitly
    # passed in.
    if updated is None:
        updated = datetime.now(tz=timezone.utc)
    lp.updated = updated

    lp.save()

    # Emit LEARNING_PACKAGE_UPDATED once the transaction commits. Note: we only
    # reach this point if at least one of key/title/description/updated was
    # passed in (the early-return above handles the no-op case), so the update
    # really did touch the row.
    lp_id = lp.id
    lp_title = lp.title

    def send_event():
        signals.LEARNING_PACKAGE_UPDATED.send_event(
            learning_package=signals.LearningPackageEventData(id=lp_id, title=lp_title),
        )

    on_commit(send_event)

    return lp


def learning_package_exists(package_ref: str) -> bool:
    """
    Check whether a LearningPackage with a particular package_ref exists.
    """
    return LearningPackage.objects.filter(package_ref=package_ref).exists()


def create_publishable_entity(
    learning_package_id: LearningPackage.ID,
    /,
    entity_ref: str,
    created: datetime,
    # User ID who created this
    created_by: int | None,
    *,
    can_stand_alone: bool = True,
) -> PublishableEntity:
    """
    Create a PublishableEntity.

    You'd typically want to call this right before creating your own content
    model that points to it.
    """
    return PublishableEntity.objects.create(
        learning_package_id=learning_package_id,
        entity_ref=entity_ref,
        created=created,
        created_by_id=created_by,
        can_stand_alone=can_stand_alone,
    )


def create_publishable_entity_version(
    entity_id: PublishableEntity.ID,
    /,
    version_num: int,
    title: str,
    created: datetime,
    created_by: int | None,
    *,
    dependencies: list[PublishableEntity.ID] | None = None,
) -> PublishableEntityVersion:
    """
    Create a PublishableEntityVersion.

    You'd typically want to call this right before creating your own content
    version model that points to it.
    """
    with atomic(savepoint=False):
        version = PublishableEntityVersion.objects.create(
            entity_id=entity_id,
            version_num=version_num,
            title=title,
            created=created,
            created_by_id=created_by,
        )
        if dependencies:
            # Validate that dependencies are from the same learning package as
            # the entity. The entity's learning package is found with a
            # subquery, so this is one query rather than two.
            entity_learning_package = PublishableEntity.objects.filter(id=entity_id).values("learning_package_id")
            if (
                PublishableEntity.objects.filter(id__in=dependencies)
                .exclude(learning_package_id__in=entity_learning_package)
                .exists()
            ):
                raise ValidationError("Dependencies must be from the same learning package")
            # Store dependencies:
            set_version_dependencies(version.id, dependencies)

        set_draft_version(
            entity_id,
            version.id,
            set_at=created,
            set_by=created_by,
        )
    return version


def set_version_dependencies(
    version_id: int,  # PublishableEntityVersion.id,
    /,
    dependencies: list[PublishableEntity.ID],
) -> None:
    """
    Set the dependencies of a publishable entity version.

    In general, callers should not modify dependencies after creation (i.e. use
    the optional param in create_publishable_entity_version() instead of using
    this function). **This function is not atomic.** If you're doing backfills,
    you must wrap calls to this function with a transaction.atomic() call.

    The idea behind dependencies is that a PublishableEntity's Versions may
    be declared to reference unpinned PublishableEntities. Changes to those
    referenced PublishableEntities still affect the draft or published state of
    the referent PublishableEntity, even if the referent entity's version is not
    incremented.

    For example, we only create a new UnitVersion when there are changes to the
    metadata of the Unit itself. So this would happen when the name of the Unit
    is changed, or if a child Component is added, removed, or reordered. No new
    UnitVersion is created when a child Component of that Unit is modified or
    published. Yet we still consider a Unit to be affected when one of its child
    Components is modified or published. Therefore, we say that the child
    Components are dependencies of the UnitVersion.

    Dependencies vs. Container Rows/Children

    Dependencies overlap with the concept of container child rows, but the two
    are not exactly the same. For instance:

    * Dependencies have no sense of ordering.
    * If a row is declared to be pinned to a specific version of a child, then
      it is NOT a dependency. For example, if U1.v1 is declared to have a pinned
      reference to ComponentVersion C1.v1, then future changes to C1 do not
      affect U1.v1 because U1.v1 will just ignore those new ComponentVersions.

    Guidelines:

    1. Only declare one level of dependencies, e.g. immediate parent-child
       relationships. The publishing app will calculate transitive dependencies
       like "all descendants" based on this. This is important for saving space,
       because the DAG of trasitive dependency relationships can explode out to
       tens of thousands of nodes per version.
    2. Declare dependencies from the bottom-up when possible. In other words, if
       you're building an entire Subsection, set the Component dependencies for
       the Units before you set the Unit dependencies for the Subsection. This
       code will still work if you build from the top-down, but we'll end up
       doing many redundant re-calculations, since every change to a lower layer
       will cause recalculation to the higher levels that depend on it.
    3. Do not create circular dependencies.
    """
    PublishableEntityVersionDependency.objects.bulk_create(
        [
            PublishableEntityVersionDependency(
                referring_version_id=version_id,
                referenced_entity_id=dep_entity_id,
            )
            for dep_entity_id in set(dependencies)  # dependencies have no ordering
        ],
    )


def get_publishable_entity(publishable_entity_id: PublishableEntity.ID, /) -> PublishableEntity:
    return PublishableEntity.objects.get(pk=publishable_entity_id)


def get_publishable_entity_by_ref(learning_package_id: LearningPackage.ID, /, entity_ref: str) -> PublishableEntity:
    return PublishableEntity.objects.get(
        learning_package_id=learning_package_id,
        entity_ref=entity_ref,
    )


def get_last_publish(learning_package_id: LearningPackage.ID, /) -> PublishLog | None:
    return PublishLog.objects \
                     .filter(learning_package_id=learning_package_id) \
                     .order_by('-id') \
                     .first()


def get_all_drafts(learning_package_id: LearningPackage.ID, /) -> QuerySet[Draft]:
    return Draft.objects.filter(
        entity__learning_package_id=learning_package_id,
        version__isnull=False,
    )


def get_publishable_entities(learning_package_id: LearningPackage.ID, /) -> QuerySet[PublishableEntity]:
    """
    Get all entities in a learning package.
    """
    return (
        PublishableEntity.objects
        .filter(learning_package_id=learning_package_id)
        .select_related(
            "draft__version",
            "published__version",
        )
    )


def get_entities_with_unpublished_changes(
    learning_package_id: LearningPackage.ID,
    /,
    include_deleted_drafts: bool = False
) -> QuerySet[PublishableEntity]:
    """
    Fetch entities that have unpublished changes.

    By default, this excludes soft-deleted drafts but can be included using
    include_deleted_drafts option.
    """
    entities_qs = (
        PublishableEntity.objects
        .filter(learning_package_id=learning_package_id)
        .exclude(draft__version=F('published__version'))
    )

    if include_deleted_drafts:
        # This means that we should also return PublishableEntities where the draft
        # has been soft-deleted, but that deletion has not been published yet. Just
        # excluding records where the Draft and Published versions don't match won't
        # be enough here, because that will return soft-deletes that have already
        # been published (since NULL != NULL in SQL).
        #
        # So we explicitly exclude already-published soft-deletes:
        return entities_qs.exclude(
            Q(draft__version__isnull=True) & Q(published__version__isnull=True)
        )

    # Simple case: exclude all entities that have been soft-deleted.
    return entities_qs.exclude(draft__version__isnull=True)


def get_entities_with_unpublished_deletes(learning_package_id: LearningPackage.ID, /) -> QuerySet[PublishableEntity]:
    """
    Something will become "deleted" if it has a null Draft version but a
    not-null Published version. (If both are null, it means it's already been
    deleted in a previous publish, or it was never published.)
    """
    return PublishableEntity.objects \
                            .filter(
                                learning_package_id=learning_package_id,
                                draft__version__isnull=True,
                            ).exclude(published__version__isnull=True)


def publish_all_drafts(
    learning_package_id: LearningPackage.ID,
    /,
    message="",
    published_at: datetime | None = None,
    published_by: int | None = None
) -> PublishLog:
    """
    Publish everything that is a Draft and is not already published.
    """
    # Order by primary key so that the resulting PublishLogRecords (and the
    # changes in the ENTITIES_PUBLISHED event) come out in a consistent order,
    # regardless of the database backend.
    draft_qset = (
        Draft.objects
             .filter(entity__learning_package_id=learning_package_id)
             .with_unpublished_changes()
             .order_by("pk")
    )
    return publish_from_drafts(
        learning_package_id, draft_qset, message, published_at, published_by
    )


def _get_dependencies_with_unpublished_changes(
    direct_drafts: list[Draft]
) -> list[list[Draft]]:
    """
    Return all dependencies to publish as a list of Draft lists, one per layer.

    This should only return the Drafts that have actual changes, not pure side-
    effects. The side-effect calculations will happen separately.

    The Drafts returned have ``entity__published__version`` pre-loaded.
    """
    # First we have to do a full crawl of *all* dependencies, regardless of
    # whether they have unpublished changes or not. This is because we might
    # have a dependency-of-a-dependency that has changed somewhere down the
    # line. Example: The direct_drafts include a Subsection. Even if the Unit
    # versions are still the same, there might be a changed Component that needs
    # to be published.
    #
    # Each layer is one query. We materialize the version IDs of each layer and
    # use them to find the next layer, rather than nesting each layer's query
    # inside the next one as a subquery.
    all_dependency_layers: list[list[Draft]] = []
    version_ids = [draft.version_id for draft in direct_drafts if draft.version_id is not None]
    while version_ids:
        dependency_drafts = list(
            Draft.objects
                 .filter(entity__affects__in=version_ids)
                 .select_related("entity__published__version")
                 .distinct()
                 .order_by("pk")
        )
        if not dependency_drafts:
            break
        all_dependency_layers.append(dependency_drafts)
        version_ids = [draft.version_id for draft in dependency_drafts if draft.version_id is not None]

    # We fetched every dependency above (so that we could keep crawling), but we
    # only publish the ones that have actually changed.
    return [
        [draft for draft in layer if draft.has_unpublished_changes()]
        for layer in all_dependency_layers
    ]


def publish_from_drafts(
    learning_package_id: LearningPackage.ID,
    /,
    draft_qset: QuerySet[Draft],
    message: str = "",
    published_at: datetime | None = None,
    published_by: int | None = None,  # User.id
    *,
    publish_dependencies: bool = True,
) -> PublishLog:
    """
    Publish the rows in the ``draft_model_qsets`` args passed in.

    By default, this will also publish all dependencies (e.g. unpinned children)
    of the Drafts that are passed in.
    """
    if DraftChangeLogContext.get_active_draft_change_log(learning_package_id) is not None:
        raise ValidationError(
            f"Cannot publish learning package {learning_package_id} while in bulk_draft_changes_for()."
        )
    if published_at is None:
        published_at = datetime.now(tz=timezone.utc)

    with atomic():
        # Materialize the directly-requested Drafts once. We need the full rows
        # to build the PublishLogRecords anyway, and this also gives us the set
        # of direct Draft IDs (and their version IDs, for dependency expansion)
        # without separate queries.
        direct_drafts = list(draft_qset.select_related("entity__published__version"))
        direct_draft_ids = {draft.pk for draft in direct_drafts}

        if publish_dependencies:
            dependency_draft_layers = _get_dependencies_with_unpublished_changes(direct_drafts)
        else:
            dependency_draft_layers = []
            # Validation: check that all dependencies have been published already.
            if Draft.objects.filter(
                entity__affects__in=[draft.version_id for draft in direct_drafts if draft.version_id is not None],
                entity__published=None,
            ).exists():
                raise ValidationError("Cannot publish entities that have unpublished dependencies.")

        # One PublishLog for this entire publish operation.
        publish_log = PublishLog(
            learning_package_id=learning_package_id,
            message=message,
            published_at=published_at,
            published_by_id=published_by,
        )
        publish_log.full_clean()
        publish_log.save(force_insert=True)

        # We process the Drafts one layer at a time: first the ones that were
        # directly requested, then each layer of their dependencies.
        all_draft_groups: list[list[Draft]] = [
            direct_drafts,
            *dependency_draft_layers,  # one list per layer of dependencies
        ]

        # Walk all the Drafts to publish, in order, and build up the list of
        # PublishLogRecords to write. We do all the writes in bulk afterwards
        # rather than doing a handful of queries per Draft–this is the
        # difference between a few queries and thousands when publishing a
        # large library.
        #
        # Insertion order matters here: the PublishLogRecords get ascending IDs
        # in the order we create them, and that ordering is what consumers of
        # the ENTITIES_PUBLISHED event see.
        drafts_to_publish: dict[int, Draft] = {}  # entity_id -> Draft, in publish order
        publish_log_records: list[PublishLogRecord] = []
        for group in all_draft_groups:
            for draft in group:
                # Skip duplicates that we might get from expanding dependencies.
                if draft.pk in drafts_to_publish:
                    continue
                # Validate Learning Package here where it won't require any extra queries
                if draft.entity.learning_package_id != learning_package_id:
                    raise ValidationError(
                        f"Draft entity (id={draft.entity.id}) is from learning package "
                        f"{draft.entity.learning_package_id}; expected learning package {learning_package_id}."
                    )

                try:
                    old_version_id = draft.entity.published.version_id
                except ObjectDoesNotExist:
                    # This means there is no published version yet.
                    old_version_id = None

                # Create a record describing publishing this particular
                # Publishable (useful for auditing and reverting).
                publish_log_records.append(
                    PublishLogRecord(
                        publish_log=publish_log,
                        entity_id=draft.entity_id,
                        old_version_id=old_version_id,
                        new_version_id=draft.version_id,
                        direct=draft.pk in direct_draft_ids,
                    )
                )
                drafts_to_publish[draft.pk] = draft

        if publish_log_records:
            PublishLogRecord.objects.bulk_create(publish_log_records)
            # Not all databases (e.g. MySQL) return primary keys from a bulk
            # insert, so look up the IDs we just created in one query. There is
            # exactly one record per entity in a PublishLog.
            record_ids_by_entity_id: dict[int, int] = dict(
                PublishLogRecord.objects
                                .filter(publish_log=publish_log)
                                .values_list("entity_id", "id")
            )

            # Update the lookup we use to fetch the published versions. This
            # is a single upsert: entities that have never been published get
            # a new Published row, and the rest have their version and log
            # record updated.
            Published.objects.bulk_create(
                [
                    Published(
                        entity_id=entity_id,
                        version_id=draft.version_id,
                        publish_log_record_id=record_ids_by_entity_id[entity_id],
                    )
                    for entity_id, draft in drafts_to_publish.items()
                ],
                update_conflicts=True,
                update_fields=["version", "publish_log_record"],
                # PostgreSQL and SQLite require the conflicting unique field to
                # be named, but MySQL's ON DUPLICATE KEY UPDATE applies to any
                # unique key, and Django rejects unique_fields for it.
                unique_fields=["entity"] if connection.features.supports_update_conflicts_with_target else None,
            )

        _create_side_effects_for_change_log(publish_log)
        _emit_event_for_change_log(publish_log, timestamp=published_at, user_id=published_by)

    return publish_log


def get_draft_version(
    publishable_entity_or_id: PublishableEntity | PublishableEntity.ID,
    /
) -> PublishableEntityVersion | None:
    """
    Return current draft PublishableEntityVersion for this PublishableEntity.

    This function will return None if there is no current draft.
    """
    if isinstance(publishable_entity_or_id, PublishableEntity):
        # Fetches the draft version for a given PublishableEntity.
        # Gracefully handles cases where no draft is present.
        draft: Optional[Draft] = getattr(publishable_entity_or_id, "draft", None)
        if draft is None:
            return None
        return draft.version
    try:
        draft = Draft.objects.select_related("version").get(
            entity_id=publishable_entity_or_id
        )
    except Draft.DoesNotExist:
        # No draft was ever created.
        return None

    # draft.version could be None if it was set that way by set_draft_version.
    # Setting the Draft.version to None is how we show that we've "deleted" the
    # content in Studio.
    return draft.version


def get_published_version(
    publishable_entity_or_id: PublishableEntity | PublishableEntity.ID,
    /
) -> PublishableEntityVersion | None:
    """
    Return current published PublishableEntityVersion for this PublishableEntity.

    This function will return None if there is no current published version.
    """
    if isinstance(publishable_entity_or_id, PublishableEntity):
        # Fetches the published version for a given PublishableEntity.
        # Gracefully handles cases where no published version is present.
        published: Optional[Published] = getattr(publishable_entity_or_id, "published", None)
        if published is None:
            return None
        return published.version
    try:
        published = Published.objects.select_related("version").get(
            entity_id=publishable_entity_or_id
        )
    except Published.DoesNotExist:
        return None

    # published.version could be None if something was published at one point,
    # had its draft soft deleted, and then was published again.
    return published.version


def get_entity_draft_history(
    publishable_entity_or_id: PublishableEntity | int, /
) -> QuerySet[DraftChangeLogRecord]:
    """
    DraftChangeLogRecords for a PublishableEntity since its last publication.

    Records are ordered from most recent to oldest.

    Edge cases:

    - Never published, no versions: returns an empty queryset.
    - Never published, has versions: returns all DraftChangeLogRecords.
    - No changes since the last publish: returns an empty queryset.
    - Last publish was a soft-delete (Published.version=None): the Published row
      still exists and its published_at timestamp is used as the lower bound, so
      only draft changes made after that soft-delete publish are returned. If
      there are no subsequent changes, the queryset is empty.
    - Unpublished soft-delete (soft-delete in draft, not yet published): the
      soft-delete DraftChangeLogRecord (new_version=None) is included because
      it was made after the last real publish.
    """
    if isinstance(publishable_entity_or_id, int):
        entity_id = PublishableEntity.PublishableEntityID(publishable_entity_or_id)
    else:
        entity_id = publishable_entity_or_id.id

    qs = (
        DraftChangeLogRecord.objects
        .filter(entity_id=entity_id)
        .select_related(
            "draft_change_log__changed_by",
            "old_version",
            "new_version",
        )
        .order_by("-draft_change_log__changed_at")
    )

    # Narrow to changes since the last publication (or last reset to published)
    try:
        published = Published.objects.select_related(
            "publish_log_record__publish_log"
        ).get(entity_id=entity_id)
        published_at = published.publish_log_record.publish_log.published_at
        published_version_id = published.version_id

        # If reset_drafts_to_published() was called after the last publish,
        # there will be a DraftChangeLogRecord where new_version == published
        # version. Use the most recent such record's timestamp as the lower
        # bound so that discarded entries no longer appear in the draft history.
        last_reset_at = (
            DraftChangeLogRecord.objects
            .filter(
                entity_id=entity_id,
                new_version_id=published_version_id,
                draft_change_log__changed_at__gt=published_at,
            )
            .order_by("-draft_change_log__changed_at")
            .values_list("draft_change_log__changed_at", flat=True)
            .first()
        )

        lower_bound = last_reset_at if last_reset_at else published_at
        qs = qs.filter(draft_change_log__changed_at__gt=lower_bound)
    except Published.DoesNotExist:
        pass

    return qs


def get_entity_publish_history(
    publishable_entity_or_id: PublishableEntity | int, /
) -> QuerySet[PublishLogRecord]:
    """
    All PublishLogRecords for a PublishableEntity, ordered most recent first.

    Edge cases:

    - Never published: returns an empty queryset.
    - Soft-delete published (new_version=None): the record is included with
      old_version pointing to the last published version and new_version=None,
      indicating the entity was removed from the published state.
    - Multiple draft versions created between two publishes are compacted: each
      PublishLogRecord captures only the version that was actually published,
      not the intermediate draft versions.
    """
    if isinstance(publishable_entity_or_id, int):
        entity_id = PublishableEntity.PublishableEntityID(publishable_entity_or_id)
    else:
        entity_id = publishable_entity_or_id.id

    return (
        PublishLogRecord.objects
        .filter(entity_id=entity_id)
        .select_related(
            "publish_log__published_by",
            "old_version",
            "new_version",
        )
        .order_by("-publish_log__published_at")
    )


def get_entity_publish_history_entries(
    publishable_entity_or_id: PublishableEntity | int,
    /,
    publish_log_uuid: str,
) -> QuerySet[DraftChangeLogRecord]:
    """
    Return the DraftChangeLogRecords associated with a specific PublishLog.

    Finds the PublishLogRecord for the given entity and publish_log_uuid, then
    returns all DraftChangeLogRecords whose changed_at falls between the previous
    publish for this entity (exclusive) and this publish (inclusive), ordered
    most-recent-first.

    Time bounds are used instead of version bounds because DraftChangeLogRecord
    has no single version_num field (soft-delete records have new_version=None),
    and using published_at timestamps cleanly handles all cases without extra
    joins.

    Edge cases:

    - Each publish group is independent: only the DraftChangeLogRecords that
      belong to the requested publish_log_uuid are returned; changes attributed
      to other publish groups are excluded.
    - Soft-delete publish (PublishLogRecord.new_version=None): the soft-delete
      DraftChangeLogRecord (new_version=None) is included in the entries because
      it falls within the time window of that publish group.

    Raises PublishLogRecord.DoesNotExist if publish_log_uuid is not found for
    this entity.
    """
    if isinstance(publishable_entity_or_id, int):
        entity_id = PublishableEntity.PublishableEntityID(publishable_entity_or_id)
    else:
        entity_id = publishable_entity_or_id.id

    # Fetch the PublishLogRecord for the requested PublishLog
    pub_record = (
        PublishLogRecord.objects
        .filter(entity_id=entity_id, publish_log__uuid=publish_log_uuid)
        .select_related("publish_log")
        .get()
    )
    published_at = pub_record.publish_log.published_at

    # Find the previous publish for this entity to use as the lower time bound
    prev_pub_record = (
        PublishLogRecord.objects
        .filter(entity_id=entity_id, publish_log__published_at__lt=published_at)
        .select_related("publish_log")
        .order_by("-publish_log__published_at")
        .first()
    )
    prev_published_at = prev_pub_record.publish_log.published_at if prev_pub_record else None

    # All draft changes up to (and including) this publish's timestamp
    draft_qs = (
        DraftChangeLogRecord.objects
        .filter(entity_id=entity_id, draft_change_log__changed_at__lte=published_at)
        .select_related(
            "draft_change_log__changed_by",
            "old_version",
            "new_version",
        )
        .order_by("-draft_change_log__changed_at")
    )
    # Exclude changes that belong to an earlier PublishLog's window
    if prev_published_at:
        draft_qs = draft_qs.filter(draft_change_log__changed_at__gt=prev_published_at)

    # Find the baseline: the version that was published in the previous publish group
    # (None if this is the first publish for this entity).
    baseline_version_id = prev_pub_record.new_version_id if prev_pub_record else None

    # If reset_drafts_to_published() was called within this publish window, there
    # will be a DraftChangeLogRecord where new_version == baseline. Use the most
    # recent such record as the new lower bound so discarded entries are excluded.
    reset_filter = {
        "entity_id": entity_id,
        "new_version_id": baseline_version_id,
        "draft_change_log__changed_at__lte": published_at,
    }
    if prev_published_at:
        reset_filter["draft_change_log__changed_at__gt"] = prev_published_at

    last_reset_at = (
        DraftChangeLogRecord.objects
        .filter(**reset_filter)
        .order_by("-draft_change_log__changed_at")
        .values_list("draft_change_log__changed_at", flat=True)
        .first()
    )
    if last_reset_at:
        draft_qs = draft_qs.filter(draft_change_log__changed_at__gt=last_reset_at)

    return draft_qs


def get_entity_version_contributors(
    publishable_entity_or_id: PublishableEntity | int,
    /,
    old_version_num: int,
    new_version_num: int | None,
) -> QuerySet:
    """
    Distinct Users who edited this entity after ``old_version_num``.

    Return distinct User queryset of contributors (changed_by) for
    DraftChangeLogRecords of a PublishableEntity after old_version_num.

    If new_version_num is not None (normal publish), captures records where
    new_version is between old_version_num (exclusive) and new_version_num (inclusive).

    If new_version_num is None (soft delete published), captures both normal
    edits after old_version_num AND the soft-delete record itself (identified
    by new_version=None and old_version >= old_version_num). A soft-delete
    record whose old_version falls before old_version_num is excluded.

    Edge cases:

    - If no DraftChangeLogRecords fall in the range, returns an empty queryset.
    - Records with changed_by=None (system changes with no associated user) are
      always excluded.
    - A user who contributed multiple versions in the range appears only once
      (results are deduplicated with DISTINCT).
    """
    if isinstance(publishable_entity_or_id, int):
        entity_id = PublishableEntity.PublishableEntityID(publishable_entity_or_id)
    else:
        entity_id = publishable_entity_or_id.id

    if new_version_num is not None:
        version_filter = Q(
            new_version__version_num__gt=old_version_num,
            new_version__version_num__lte=new_version_num,
        )
    else:
        # Soft delete: include edits after old_version_num + the soft-delete record
        version_filter = (
            Q(new_version__version_num__gt=old_version_num) |
            Q(new_version__isnull=True, old_version__version_num__gte=old_version_num)
        )

    contributor_ids = (
        DraftChangeLogRecord.objects
        .filter(entity_id=entity_id)
        .filter(version_filter)
        .exclude(draft_change_log__changed_by=None)
        .values_list("draft_change_log__changed_by", flat=True)
        .distinct()
    )
    # Order by most recent contribution first. filter(pk__in=subquery) doesn't
    # preserve subquery ordering, so we annotate each user with their latest
    # changed_at via a correlated subquery and order on that. N (contributors
    # per publish event) is typically 1–5, so the per-row cost is negligible.
    last_contrib_subquery = (
        DraftChangeLogRecord.objects
        .filter(entity_id=entity_id, draft_change_log__changed_by=OuterRef("pk"))
        .filter(version_filter)
        .order_by("-draft_change_log__changed_at")
        .values("draft_change_log__changed_at")[:1]
    )
    return (
        get_user_model().objects
        .filter(pk__in=contributor_ids)
        .annotate(last_contributed=Subquery(last_contrib_subquery))
        .order_by("-last_contributed")
    )


def set_draft_version(
    draft_or_id: Draft | PublishableEntity.ID,
    publishable_entity_version_pk: int | None,
    /,
    set_at: datetime | None = None,
    set_by: int | None = None,  # User.id
) -> None:
    """
    Modify the Draft of a PublishableEntity to be a PublishableEntityVersion.

    The ``draft`` argument can be either a Draft model object, or the primary
    key of a Draft/PublishableEntity (Draft is defined so these will be the same
    value).

    This would most commonly be used to set the Draft to point to a newly
    created PublishableEntityVersion that was created in Studio (because someone
    edited some content). Setting a Draft's version to None is like deleting it
    from Studio's editing point of view (see ``soft_delete_draft`` for more
    details).

    Calling this function attaches a new DraftChangeLogRecord and attaches it to
    a DraftChangeLog.

    This function will create DraftSideEffect entries and properly add any
    containers that may have been affected by this draft update, UNLESS it is
    called from within a bulk_draft_changes_for block. If it is called from
    inside a bulk_draft_changes_for block, it will not add side-effects for
    containers, as bulk_draft_changes_for will automatically do that when the
    block exits.
    """
    if set_at is None:
        set_at = datetime.now(tz=timezone.utc)

    with atomic(savepoint=False):
        if isinstance(draft_or_id, Draft):
            draft = draft_or_id
        elif isinstance(draft_or_id, int):
            draft, _created = Draft.objects.select_related("entity") \
                                           .get_or_create(entity_id=draft_or_id)
        else:
            class_name = draft_or_id.__class__.__name__
            raise TypeError(
                f"draft_or_id must be a Draft or int, not ({class_name})"
            )

        # If the Draft is already pointing at this version, there's nothing to do.
        old_version_id = draft.version_id
        if old_version_id == publishable_entity_version_pk:
            return

        # The actual update of the Draft model is here. Everything after this
        # block is bookkeeping in our DraftChangeLog.
        draft.version_id = publishable_entity_version_pk

        # Validate the entity. We grab the learning package ID in the same
        # query, since we need it below and the Draft's entity relation isn't
        # loaded if get_or_create() just created the Draft.
        if publishable_entity_version_pk is not None:
            version_entity_id, learning_package_id = (
                PublishableEntityVersion.objects
                                        .values_list("entity_id", "entity__learning_package_id")
                                        .get(pk=publishable_entity_version_pk)
            )
            if draft.entity_id != version_entity_id:
                invalid_pev = PublishableEntityVersion.objects.get(pk=publishable_entity_version_pk)
                raise ValidationError(
                    f"Entity mismatch - the specified PublishableEntityVersion ({repr(invalid_pev)}) does not match "
                    f"the PublishableEntity ({repr(draft.entity)})."
                )
        else:
            learning_package_id = draft.entity.learning_package_id

        # Check to see if we're inside a context manager for an active
        # DraftChangeLog (i.e. what happens if the caller is using the public
        # bulk_draft_changes_for() API call), or if we have to make our own.
        active_change_log = DraftChangeLogContext.get_active_draft_change_log(
            learning_package_id
        )
        if active_change_log:
            draft_log_record = _add_to_existing_draft_change_log(
                active_change_log,
                draft.entity_id,
                old_version_id=old_version_id,
                new_version_id=publishable_entity_version_pk,
            )
            if draft_log_record:
                # Normal case: a DraftChangeLogRecord was created or updated.
                draft.draft_log_record = draft_log_record
            else:
                # Edge case: this change cancelled out other changes, and the
                # net effect is that the DraftChangeLogRecord shouldn't exist,
                # i.e. the version at the start and end of the DraftChangeLog is
                # the same. In that case, _add_to_existing_draft_change_log will
                # delete the log record for this Draft state, and we have to
                # look for the most recently created DraftLogRecord from another
                # DraftChangeLog. This value may be None.
                #
                # NOTE: There may be some weird edge cases here around soft-
                # deletions and modifications of the same Draft entry in nested
                # bulk_draft_changes_for() calls. I haven't thought that through
                # yet--it might be better to just throw an error rather than try
                # to accommodate it.
                draft.draft_log_record = (
                    DraftChangeLogRecord.objects
                                        .filter(entity_id=draft.entity_id)
                                        .order_by("-pk")
                                        .first()
                )
            draft.save()

            # We also *don't* create container side effects here because there
            # may be many changes in this DraftChangeLog, some of which haven't
            # been made yet. It wouldn't make sense to create a side effect that
            # says, "this Unit changed because this Component in it changed" if
            # we were changing that same Unit later on in the same
            # DraftChangeLog, because that new Unit version might not even
            # include that child Component. Also, calculating side-effects is
            # expensive, and would result in a lot of wasted queries if we did
            # it for every change will inside an active change log context.
            #
            # Therefore we'll let DraftChangeLogContext do that work when it
            # exits its context.
        else:
            # This means there is no active DraftChangeLog, so we create our own
            # and add our DraftChangeLogRecord to it. This has the very minor
            # optimization that we don't have to check for an existing
            # DraftChangeLogRecord, because we know it can't exist yet.
            change_log = DraftChangeLog.objects.create(
                learning_package_id=learning_package_id,
                changed_at=set_at,
                changed_by_id=set_by,
            )
            draft.draft_log_record = DraftChangeLogRecord.objects.create(
                draft_change_log=change_log,
                entity_id=draft.entity_id,
                old_version_id=old_version_id,
                new_version_id=publishable_entity_version_pk,
            )
            draft.save()
            _create_side_effects_for_change_log(change_log)
            # Send out an event immediately after this database transaction commits, since this is an isolated change.
            _emit_event_for_change_log(change_log, timestamp=set_at, user_id=set_by)


def _add_to_existing_draft_change_log(
    active_change_log: DraftChangeLog,
    entity_id: PublishableEntity.ID,
    old_version_id: int | None,
    new_version_id: int | None,
) -> DraftChangeLogRecord | None:
    """
    Create, update, or delete a DraftChangeLogRecord for the active_change_log.

    The an active_change_log may have many DraftChangeLogRecords already
    associated with it. A DraftChangeLog can only have one DraftChangeLogRecord
    per PublishableEntity, e.g. the same Component can't go from v1 to v2 and v2
    to v3 in the same DraftChangeLog. The DraftChangeLogRecord is meant to
    capture the before and after states of the Draft version for that entity,
    so we always keep the first value for old_version, while updating to the
    most recent value for new_version.

    So for example, if we called this function with the same active_change_log
    and the same entity_id but with versions: (None, v1), (v1, v2), (v2, v3);
    we would collapse them into one DraftChangeLogrecord with old_version = None
    and new_version = v3.

    This also means that if we make a change that undoes the previos change, it
    will delete that DraftChangeLogRecord, e.g. (None, v1) -> (None, v2) ->
    (None -> None), then this entry can be deleted because it didn't change
    anything. This function should never be used for creating side-effect change
    log records (the only place where it's normal to have the same old and new
    versions).
    """
    try:
        # Check to see if this PublishableEntity has already been changed in
        # this DraftChangeLog. If so, we update that record instead of creating
        # a new one.
        change = DraftChangeLogRecord.objects.get(
            draft_change_log=active_change_log,
            entity_id=entity_id,
        )
        if change.old_version_id == new_version_id:
            # Special case: This change undoes the previous change(s). The value
            # in change.old_version_id represents the Draft version before the
            # DraftChangeLog was started, regardless of how many times we've
            # changed it since we entered the bulk_draft_changes_for() context.
            # If we get here in the code, it means that we're now setting the
            # Draft version of this entity to be exactly what it was at the
            # start, and we should remove it entirely from the DraftChangeLog.
            #
            # It's important that we remove these cases, because we use the
            # old_version == new_version convention to record entities that have
            # changed purely due to side-effects. We could technically still
            # differentiate those by actually looking at the DraftSideEffect and
            # PublishSideEffect models, but this is less confusing overall.
            change.delete()
            return None
        else:
            # Normal case: We update the new_version, but leave the old_version
            # as is. The old_version represents what the Draft was pointing to
            # when the bulk_draft_changes_for() context started, so it persists
            # if we change the same entity multiple times in the DraftChangeLog.
            change.new_version_id = new_version_id
            change.save()
    except DraftChangeLogRecord.DoesNotExist:
        # If we're here, this is the first DraftChangeLogRecord we're making for
        # this PublishableEntity in the active DraftChangeLog.
        change = DraftChangeLogRecord.objects.create(
            draft_change_log=active_change_log,
            entity_id=entity_id,
            old_version_id=old_version_id,
            new_version_id=new_version_id,
        )

    return change


def _create_side_effects_for_change_log(change_log: DraftChangeLog | PublishLog):
    """
    Create the side-effects for a DraftChangeLog or PublishLog.

    A side-effect is created whenever a dependency of a draft or published
    entity version is altered.

    For example, say we have a published Unit at version 1 (``U1.v1``).
    ``U1.v1`` is defined to have unpinned references to Components ``C1`` and
    ``C2``, i.e. ``U1.v1 = [C1, C2]``. This means that ``U1.v1`` always shows
    the latest published versions of ``C1`` and ``C2``. While we do have a more
    sophisticated encoding for the ordered parent-child relationships, we
    capture the basic dependency relationship using the M:M
    ``PublishableEntityVersionDependency`` model. In this scenario, C1 and C2
    are dependencies of ``U1.v1``

    In the above example, publishing a newer version of ``C1`` does *not*
    increment the version for Unit ``U1``. But we still want to record that
    ``U1.v1`` was affected by the change in ``C1``. We record this in the
    ``DraftSideEffect`` and ``PublishSideEffect`` models. We also add an entry
    for ``U1`` in the change log, saying that it went from version 1 to version
    1--i.e nothing about the Unit's defintion changed, but it was still affected
    by other changes in the log.

    Only call this function after all the records have already been created.

    Note: The interface between ``DraftChangeLog`` and ``PublishLog`` is similar
    enough that this function has been made to work with both.
    """
    branch_rel: str
    log_rel: str
    log_record_rel: str
    branch_cls: type[Draft] | type[Published]
    change_record_cls: type[DraftChangeLogRecord] | type[PublishLogRecord]
    side_effect_cls: type[DraftSideEffect] | type[PublishSideEffect]
    if isinstance(change_log, DraftChangeLog):
        branch_rel = "draft"
        log_rel = "draft_change_log"
        log_record_rel = "draft_log_record"
        branch_cls = Draft
        change_record_cls = DraftChangeLogRecord
        side_effect_cls = DraftSideEffect
    elif isinstance(change_log, PublishLog):
        branch_rel = "published"
        log_rel = "publish_log"
        log_record_rel = "publish_log_record"
        branch_cls = Published
        change_record_cls = PublishLogRecord
        side_effect_cls = PublishSideEffect
    else:
        raise TypeError(
            f"expected DraftChangeLog or PublishLog, not {type(change_log)}"
        )

    # These are the change records that exist at the start, i.e. the entities
    # that were directly changed. The side-effect records that we create below
    # are not themselves treated as original changes.
    original_changes: list[_ChangeRecord] = list(change_log.records.order_by("pk"))

    # Fetch the entire graph of entities that are (transitively) affected by
    # the original changes, one query per layer of dependencies. Everything
    # after this is computed in memory and then written back in a handful of
    # bulk queries. The previous implementation did several queries for every
    # edge in this graph, which was very expensive for large publishes.
    dependents = _get_dependents_of(
        branch_rel, log_record_rel, [record.entity_id for record in original_changes]
    )
    new_records, side_effects, branch_rows_to_repoint = _plan_side_effects(
        change_log, log_rel, change_record_cls, original_changes, dependents
    )

    # Now write everything back in bulk.
    if new_records:
        _bulk_create(change_record_cls, new_records)
        # Not all databases (e.g. MySQL) return primary keys from a bulk
        # insert, so look up the IDs we just created in one query. There is
        # exactly one record per entity in a change log.
        new_record_ids_by_entity_id: dict[int, int] = dict(
            change_record_cls.objects
                             .filter(**{log_rel: change_log}, entity_id__in=[r.entity_id for r in new_records])
                             .values_list("entity_id", "id")
        )
        for record in new_records:
            record.id = new_record_ids_by_entity_id[record.entity_id]

    if side_effects:
        _bulk_create(
            side_effect_cls,
            [side_effect_cls(cause_id=cause.id, effect_id=effect.id) for cause, effect in side_effects],
        )

    if branch_rows_to_repoint:
        # These are the Published or Draft objects where we need to repoint the
        # log_record (publish_log_record or draft_change_log_record) to point to
        # the side-effect changes, e.g. the DraftChangeLogRecord that says,
        # "This Unit's version stayed the same, but its dependency hash changed
        # because a child Component's draft version was changed."
        _bulk_update(
            branch_cls,
            [
                branch_cls(entity_id=entity_id, **{f"{log_record_rel}_id": record.id})
                for entity_id, record in branch_rows_to_repoint.items()
            ],
            [log_record_rel],
        )

    update_dependencies_hash_digests_for_log(change_log)


_ChangeRecord = DraftChangeLogRecord | PublishLogRecord


class _Dependent(NamedTuple):
    """
    A Draft or Published row whose current version depends on some entity.
    """
    entity_id: PublishableEntity.ID
    version_id: int
    log_record_id: int | None


def _get_dependents_of(
    branch_rel: str,
    log_record_rel: str,
    entity_ids: list[PublishableEntity.ID],
) -> dict[PublishableEntity.ID, list[_Dependent]]:
    """
    Find everything that (transitively) depends on the given entities.

    Returns a mapping of entity ID -> the Draft (``branch_rel="draft"``) or
    Published (``branch_rel="published"``) rows whose current version lists that
    entity as a dependency, ordered by entity ID. The mapping covers every
    entity reachable from ``entity_ids`` by following dependencies upwards, so
    the result can be used to walk the whole affected graph without any
    further queries. It takes one query per layer of dependencies.
    """
    dependents: dict[PublishableEntity.ID, list[_Dependent]] = {}
    frontier = list(entity_ids)
    seen_entity_ids = set(frontier)
    while frontier:
        edges = (
            PublishableEntityVersionDependency.objects
            .filter(referenced_entity_id__in=frontier)
            # Only versions that are the *current* draft/published version count.
            .filter(**{f"referring_version__{branch_rel}__isnull": False})
            .order_by(f"referring_version__{branch_rel}__entity_id")
            .values_list(
                "referenced_entity_id",
                f"referring_version__{branch_rel}__entity_id",
                "referring_version_id",
                f"referring_version__{branch_rel}__{log_record_rel}_id",
            )
        )
        next_frontier: list[PublishableEntity.ID] = []
        for referenced_entity_id, entity_id, version_id, log_record_id in edges:
            dependents.setdefault(referenced_entity_id, []).append(
                _Dependent(entity_id=entity_id, version_id=version_id, log_record_id=log_record_id)
            )
            if entity_id not in seen_entity_ids:
                seen_entity_ids.add(entity_id)
                next_frontier.append(entity_id)
        frontier = next_frontier

    return dependents


def _plan_side_effects(
    change_log: DraftChangeLog | PublishLog,
    log_rel: str,
    change_record_cls: type[DraftChangeLogRecord] | type[PublishLogRecord],
    original_changes: list[_ChangeRecord],
    dependents: dict[PublishableEntity.ID, list[_Dependent]],
) -> tuple[
    list[_ChangeRecord],
    list[tuple[_ChangeRecord, _ChangeRecord]],
    dict[PublishableEntity.ID, _ChangeRecord],
]:
    """
    Work out the side-effects of ``original_changes`` without touching the DB.

    Returns a tuple of:

    1. The new (unsaved) side-effect change records to create, in order.
    2. The (cause, effect) pairs of records to create side-effects for, in
       order. These may reference records from (1) that haven't been saved yet.
    3. A mapping of entity ID -> the record that the entity's Draft/Published
       row should be repointed to.

    See ``_create_side_effects_for_change_log`` for details of what side-effects
    mean. The traversal order here is significant: it determines the order in
    which the side-effect records are created (and therefore their IDs), which
    is the order that records show up in the ENTITIES_*_CHANGED events.
    """
    records_by_entity_id: dict[PublishableEntity.ID, _ChangeRecord] = {
        record.entity_id: record for record in original_changes
    }

    # What each affected Draft/Published row's log record currently points at.
    # Values start out as the IDs we read from the database, and get replaced
    # by (possibly not-yet-saved) record objects as we repoint them.
    branch_pointers: dict[PublishableEntity.ID, int | None | _ChangeRecord] = {}
    for dependents_of_entity in dependents.values():
        for dependent in dependents_of_entity:
            branch_pointers.setdefault(dependent.entity_id, dependent.log_record_id)

    def _points_at(entity_id: PublishableEntity.ID, record: _ChangeRecord) -> bool:
        current = branch_pointers[entity_id]
        return current is record or (record.pk is not None and current == record.pk)

    new_records: list[_ChangeRecord] = []
    side_effects: list[tuple[_ChangeRecord, _ChangeRecord]] = []
    side_effect_keys: set[tuple[PublishableEntity.ID, PublishableEntity.ID]] = set()
    branch_rows_to_repoint: dict[PublishableEntity.ID, _ChangeRecord] = {}

    # propagated_entity_ids holds the entity IDs whose dependents we've already
    # queued up. Each entity's side-effects on *its* dependents only need to be
    # calculated once, no matter how many different changes end up affecting
    # it. So if we're processing all the Components in a Unit, we won't be
    # recalculating the Unit's side-effect on its Subsection, and its
    # Subsection's side-effect on its Section each time through the loop. It
    # also guards against infinite parent-child relationship loops, though
    # those aren't *supposed* to happen anyhow.
    #
    # Note that this only short-circuits the *propagation* from an entity. The
    # side-effect for each individual (cause, effect) edge is always recorded,
    # even when the affected entity has already been processed. (An earlier
    # version of this code skipped the edge as well, so when two sibling Units
    # under the same Subsection both had changed children, only the first
    # Unit's side-effect on the Subsection was recorded.)
    propagated_entity_ids: set[PublishableEntity.ID] = set()
    for original_change in original_changes:
        if original_change.entity_id in propagated_entity_ids:
            # We already reached this entity as a side-effect of an earlier
            # original change, which queued up everything that depends on it.
            continue
        propagated_entity_ids.add(original_change.entity_id)
        # This is a stack, so the last affected entity gets processed first.
        changes_and_affected = [
            (original_change, affected) for affected in dependents.get(original_change.entity_id, [])
        ]
        while changes_and_affected:
            change, affected = changes_and_affected.pop()

            # Example: If the original_change is a DraftChangeLogRecord that
            # represents editing a Component, the side_effect_change is the
            # DraftChangeLogRecord that represents the fact that the containing
            # Unit was also altered (even if the Unit version doesn't change).
            side_effect_change = records_by_entity_id.get(affected.entity_id)
            if side_effect_change is None:
                # If a change record already exists because the affected
                # entity was separately modified, then we don't touch the
                # old/new version entries. But if we're creating this change
                # record as a pure side-effect, then we use the (old_version
                # == new_version) convention to indicate that.
                side_effect_change = change_record_cls(
                    **{log_rel: change_log},
                    entity_id=affected.entity_id,
                    old_version_id=affected.version_id,
                    new_version_id=affected.version_id,
                )
                records_by_entity_id[affected.entity_id] = side_effect_change
                new_records.append(side_effect_change)

            # Update the current branch pointer (Draft or Published) for this
            # entity to point to the side_effect_change (if it's not already).
            if not _points_at(affected.entity_id, side_effect_change):
                branch_pointers[affected.entity_id] = side_effect_change
                branch_rows_to_repoint[affected.entity_id] = side_effect_change

            # Create a new side effect (DraftSideEffect or PublishSideEffect) to
            # record the relationship between the ``change`` and the
            # corresponding ``side_effect_change``. We'll do this regardless of
            # whether we created the ``side_effect_change`` or just pulled back
            # an existing one. This addresses two things:
            #
            # 1. A change in multiple dependencies can generate multiple
            #    side effects that point to the same change log record, i.e.
            #    multiple changes can cause the same ``effect``.
            #    Example: Two draft components in a Unit are changed. Two
            #    DraftSideEffects will be created and point to the same Unit
            #    DraftChangeLogRecord.
            # 2. A entity and its dependency can change at the same time.
            #    Example: If a Unit has a Component, and both the Unit and
            #    Component are edited in the same DraftChangeLog, then the Unit
            #    has changed in both ways (the Unit's internal metadata as well
            #    as the new version of the child component). The version of the
            #    Unit will be incremented, but we'll also create the
            #    DraftSideEffect.
            #
            # There is only one record per entity in a change log, so the pair
            # of entity IDs uniquely identifies a side effect.
            side_effect_key = (change.entity_id, side_effect_change.entity_id)
            if side_effect_key not in side_effect_keys:
                side_effect_keys.add(side_effect_key)
                side_effects.append((change, side_effect_change))

            # Now we find the next layer up by looking at Drafts or Published
            # that have ``affected.entity`` as a dependency, unless we've
            # already done that for this entity.
            if affected.entity_id not in propagated_entity_ids:
                propagated_entity_ids.add(affected.entity_id)
                changes_and_affected.extend(
                    (side_effect_change, next_affected)
                    for next_affected in dependents.get(affected.entity_id, [])
                )

    return new_records, side_effects, branch_rows_to_repoint


def _bulk_create(model_cls, objs):
    """
    bulk_create using the model class.

    Like _bulk_update_hashes(), this exists only to keep mypy from choking on
    bulk_create() being called via a union of model classes. The lack of type
    annotations on this function is intentional.
    """
    model_cls.objects.bulk_create(objs)


def _bulk_update(model_cls, objs, fields):
    """
    bulk_update using the model class (see _bulk_create()).

    The lack of type annotations on this function is intentional.
    """
    model_cls.objects.bulk_update(objs, fields)


def _emit_event_for_change_log(
    change_log: PublishLog | DraftChangeLog, timestamp: datetime, user_id: int | None
) -> None:
    """
    Construct and emit the _CHANGED / _PUBLISHED event when a set of entities is
    changed or published.

    Works with either ``DraftChangeLog`` or ``PublishLog``.
    """

    learning_package_id = change_log.learning_package.id
    learning_package_title = change_log.learning_package.title
    records: list[DraftChangeLogRecord | PublishLogRecord] = list(
        change_log.records.order_by("id").select_related("old_version", "new_version").all()
    )

    # For draft change logs, distinguish "restored" entities (un-soft-delete) from brand-new entities.
    # Both have old_version_id=None, but a brand-new entity has no DraftChangeLogRecord in any prior
    # change log, while a restored entity does (its creation, soft-delete, and possibly more).
    restored_entity_ids: set[int] = set()
    if isinstance(change_log, DraftChangeLog):
        candidate_entity_ids = [
            r.entity_id for r in records if r.old_version_id is None and r.new_version_id is not None
        ]
        if candidate_entity_ids:
            restored_entity_ids = set(
                DraftChangeLogRecord.objects
                .filter(entity_id__in=candidate_entity_ids)
                .exclude(draft_change_log_id=change_log.id)
                .values_list("entity_id", flat=True)
                .distinct()
            )

    changes = [
        signals.ChangeLogRecordData(
            entity_id=record.entity_id,
            old_version=record.old_version.version_num if record.old_version else None,
            old_version_id=record.old_version_id,
            new_version=record.new_version.version_num if record.new_version else None,
            new_version_id=record.new_version_id,
            direct=record.direct if isinstance(record, PublishLogRecord) else None,
            restored=record.entity_id in restored_entity_ids,
        )
        for record in records
    ]

    change_log_data: signals.DraftChangeLogEventData | signals.PublishLogEventData
    if isinstance(change_log, DraftChangeLog):
        signal = signals.ENTITIES_DRAFT_CHANGED
        change_log_data = signals.DraftChangeLogEventData(draft_change_log_id=change_log.id, changes=changes)
    else:
        assert isinstance(change_log, PublishLog)
        signal = signals.ENTITIES_PUBLISHED
        change_log_data = signals.PublishLogEventData(publish_log_id=change_log.id, changes=changes)

    # Send out an event immediately after this database transaction commits.
    on_commit(partial(
        signal.send_event,
        time=timestamp,
        learning_package=signals.LearningPackageEventData(id=learning_package_id, title=learning_package_title),
        changed_by=signals.UserAttributionEventData(user_id=user_id),
        change_log=change_log_data,
    ))


def update_dependencies_hash_digests_for_log(
    change_log: DraftChangeLog | PublishLog,
    backfill: bool = False,
) -> None:
    """
    Update dependencies_hash_digest for Drafts or Published in a change log.

    All the data for Draft/Published, DraftChangeLog/PublishLog, and
    DraftChangeLogRecord/PublishLogRecord have been set at this point *except*
    the dependencies_hash_digest of DraftChangeLogRecord/PublishLogRecord. Those
    log records are newly created at this point, so dependencies_hash_digest are
    set to their default values.

    Args:
        change_log: A DraftChangeLog or PublishLog that already has all
            side-effects added to it. The Draft and Published models should
            already be updated to point to the post-change versions.
        backfill: If this is true, we will not trust the hash values stored on
            log records outside of our log, i.e. things that we would normally
            expect to be pre-calculated. This will be important for the initial
            data migration.
    """
    if isinstance(change_log, DraftChangeLog):
        branch = "draft"
        log_record_relation = "draft_log_record"
        record_cls = DraftChangeLogRecord
    elif isinstance(change_log, PublishLog):
        branch = "published"
        log_record_relation = "publish_log_record"
        record_cls = PublishLogRecord  # type: ignore[assignment]
    else:
        raise TypeError(
            f"expected DraftChangeLog or PublishLog, not {type(change_log)}"
        )

    dependencies_prefetch = Prefetch(
        "new_version__dependencies",
        queryset=PublishableEntity.objects
                                  .select_related(
                                      f"{branch}__version",
                                      f"{branch}__{log_record_relation}",
                                   )
                                  .order_by(f"{branch}__version__uuid")
    )
    changed_records: QuerySet[DraftChangeLogRecord] | QuerySet[PublishLogRecord]
    changed_records = (
        change_log.records
                  .select_related("new_version", f"entity__{branch}")
                  .prefetch_related(dependencies_prefetch)
    )

    record_ids_to_hash_digests: dict[int, str | None] = {}
    record_ids_to_live_deps: dict[int, list[PublishableEntity]] = {}
    records_that_need_hashes = []

    for record in changed_records:
        # This is a soft-deletion, so the dependency hash is default/blank. We
        # set this value in our record_ids_to_hash_digests cache, but we don't
        # need to write it to the database because it's just the default value.
        if record.new_version is None:
            record_ids_to_hash_digests[record.id] = ''
            continue

        # Now check to see if the new version has "live" dependencies, i.e.
        # dependencies that have not been deleted.
        deps = list(
            entity for entity in record.new_version.dependencies.all()
            if hasattr(entity, branch) and getattr(entity, branch).version
        )

        # If there are no live dependencies, this log record also gets the
        # default/blank value.
        if not deps:
            record_ids_to_hash_digests[record.id] = ''
            continue

        # If we've gotten this far, it means that this record has dependencies
        # and does need to get a hash computed for it.
        records_that_need_hashes.append(record)
        record_ids_to_live_deps[record.id] = deps

    if backfill:
        untrusted_record_id_set = None
    else:
        untrusted_record_id_set = set(rec.id for rec in records_that_need_hashes)

    for record in records_that_need_hashes:
        record.dependencies_hash_digest = hash_for_log_record(
            record,
            record_ids_to_hash_digests,
            record_ids_to_live_deps,
            untrusted_record_id_set,
        )

    _bulk_update_hashes(record_cls, records_that_need_hashes)


def _bulk_update_hashes(model_cls, records):
    """
    bulk_update using the model class (PublishLogRecord or DraftChangeLogRecord)

    The only reason this function exists is because mypy 1.18.2 throws an
    exception in validate_bulk_update() during "make quality" checks otherwise
    (though curiously enough, not when that same version of mypy is called
    directly). Given that I'm writing this on the night before the Ulmo release
    cut, I'm not really interested in tracking down the underlying issue.

    The lack of type annotations on this function is very intentional.
    """
    model_cls.objects.bulk_update(records, ['dependencies_hash_digest'])


def hash_for_log_record(
    record: DraftChangeLogRecord | PublishLogRecord,
    record_ids_to_hash_digests: dict,
    record_ids_to_live_deps: dict,
    untrusted_record_id_set: set | None,
) -> str:
    """
    The hash digest for a given change log record.

    Note that this code is a little convoluted because we're working hard to
    minimize the number of database requests. All the data we really need could
    be derived from querying various relations off the record that's passed in
    as the first parameter, but at a far higher cost.

    The hash calculated here will be used for the dependencies_hash_digest
    attribute of DraftChangeLogRecord and PublishLogRecord. The hash is intended
    to calculate the currently "live" (current draft or published) state of all
    dependencies (and transitive dependencies) of the PublishableEntityVersion
    pointed to by DraftChangeLogRecord.new_version/PublishLogRecord.new_version.

    The common case we have at the moment is when a container type like a Unit
    has unpinned child Components as dependencies. In the data model, those
    dependency relationships are represented by the "dependencies" M:M relation
    on PublishableEntityVersion. Since the Unit version's references to its
    child Components are unpinned, the draft Unit is always pointing to the
    latest draft versions of those Components and the published Unit is always
    pointing to the latest published versions of those Components.

    This means that the total draft or published state of any PublishableEntity
    depends on the combination of:

    1. The definition of the current draft/published version of that entity.
       Example: Version 1 of a Unit would define that it had children [C1, C2].
       Version 2 of the same Unit might have children [C1, C2, C3].
    2. The current draft/published versions of all dependencies. Example: What
       are the current draft and published versions of C1, C2, and C3.

    This is why it makes sense to capture in a log record, since
    PublishLogRecords or DraftChangeLogRecords are created whenever one of the
    above two things changes.

    Here are the possible scenarios, including edge cases:

    EntityVersions with no dependencies
      If record.new_version has no dependencies, dependencies_hash_digest is
      set to the default value of ''. This will be the most common case.

    EntityVersions with dependencies
      If an EntityVersion has dependencies, then its draft/published state
      hash is based on the concatenation of, for each non-deleted dependency:
        (i)  the dependency's draft/published EntityVersion primary key, and
        (ii) the dependency's own draft/published state hash, recursively re-
             calculated if necessary.

    Soft-deletions
      If the record.new_version is None, that means we've just soft-deleted
      something (or published the soft-delete of something). We adopt the
      convention that if something is soft-deleted, its dependencies_hash_digest
      is reset to the default value of ''. This is not strictly necessary for
      the recursive hash calculation, but deleted entities will not have their
      hash updated even as their non-deleted dependencies are updated underneath
      them, so we set to '' to avoid falsely implying that the deleted entity's
      dep hash is up to date.

    EntityVersions with soft-deleted dependencies
      A soft-deleted dependency isn't counted (it's as if the dependency were
      removed). If all of an EntityVersion's dependencies are soft-deleted,
      then it will go back to having to having the default blank string for its
      dependencies_hash_digest.
    """
    # Case #1: We've already computed this, or it was bootstrapped for us in the
    # cache because the record is a deletion or doesn't have dependencies.
    if record.id in record_ids_to_hash_digests:
        return record_ids_to_hash_digests[record.id]

    # Case #2: The log_record is a dependency of something that was affected by
    # a change, but the dependency itself did not change in any way (neither
    # directly, nor as a side-effect).
    #
    # Example: A Unit has two Components. One of the Components changed, forcing
    # us to recalculate the dependencies_hash_digest for that Unit. Doing that
    # recalculation requires us to fetch the dependencies_hash_digest of the
    # unchanged child Component as well.
    #
    # If we aren't given an explicit untrusted_record_id_set, it means we can't
    # trust anything. This would happen when we're bootstrapping things with an
    # initial data migration.
    if (untrusted_record_id_set is not None) and (record.id not in untrusted_record_id_set):
        return record.dependencies_hash_digest

    # Normal recursive case starts here:
    if isinstance(record, DraftChangeLogRecord):
        branch = "draft"
    elif isinstance(record, PublishLogRecord):
        branch = "published"
    else:
        raise TypeError(
            f"expected DraftChangeLogRecord or PublishLogRecord, not {type(record)}"
        )

    # This is extra work that only happens in case of a backfill, where we might
    # need to compute dependency hashes for things outside of our log (because
    # we don't trust them).
    if record.id not in record_ids_to_live_deps:
        if record.new_version is None:
            record_ids_to_hash_digests[record.id] = ''
            return ''
        deps = list(
            entity for entity in record.new_version.dependencies.all()
            if hasattr(entity, branch) and getattr(entity, branch).version
        )
        # If there are no live dependencies, this log record also gets the
        # default/blank value.
        if not deps:
            record_ids_to_hash_digests[record.id] = ''
            return ''

        record_ids_to_live_deps[record.id] = deps
    # End special handling for backfill.

    # Begin normal
    dependencies: list[PublishableEntity] = sorted(
        record_ids_to_live_deps[record.id],
        key=lambda entity: getattr(entity, branch).log_record.new_version_id,
    )
    dep_state_entries = []
    for dep_entity in dependencies:
        new_version_id = getattr(dep_entity, branch).log_record.new_version_id
        hash_digest = hash_for_log_record(
            getattr(dep_entity, branch).log_record,
            record_ids_to_hash_digests,
            record_ids_to_live_deps,
            untrusted_record_id_set,
        )
        dep_state_entries.append(f"{new_version_id}:{hash_digest}")
    summary_text = "\n".join(dep_state_entries)

    digest = create_hash_digest(summary_text.encode(), num_bytes=4)
    record_ids_to_hash_digests[record.id] = digest

    return digest


def soft_delete_draft(publishable_entity_id: PublishableEntity.ID, /, deleted_by: int | None = None) -> None:
    """
    Sets the Draft version to None.

    This "deletes" the ``PublishableEntity`` from the point of an authoring
    environment like Studio, but doesn't immediately remove the ``Published``
    version. No version data is actually deleted, so restoring is just a matter
    of pointing the Draft back to the most recent ``PublishableEntityVersion``
    for a given ``PublishableEntity``.
    """
    return set_draft_version(publishable_entity_id, None, set_by=deleted_by)


def reset_drafts_to_published(
    learning_package_id: LearningPackage.ID,
    /,
    reset_at: datetime | None = None,
    reset_by: int | None = None,  # User.id
) -> None:
    """
    Reset all Drafts to point to the most recently Published versions.

    This is a way to say "discard my unpublished changes" at the level of an
    entire LearningPackage. Note that the ``PublishableEntityVersions`` that
    were created in the mean time are not deleted.

    Let's look at the example of a PublishableEntity where Draft and Published
    both point to version 4.

    * The PublishableEntity is then edited multiple times, creating a new
      version with every save. Each new save also updates the the Draft to point
      to it. After three such saves, Draft points at version 7.
    * No new publishes have happened, so Published still points to version 4.
    * ``reset_drafts_to_published`` is called. Draft now points to version 4 to
      match Published.
    * The PublishableEntity is edited again. This creates version 8, and Draft
      now points to this new version.

    So in the above example, versions 5-7 aren't discarded from the history, and
    it's important that the code creating the "next" version_num looks at the
    latest version created for a PublishableEntity (its ``latest`` attribute),
    rather than basing it off of the version that Draft points to.
    """
    if reset_at is None:
        reset_at = datetime.now(tz=timezone.utc)

    # These are all the drafts that are different from the published versions.
    draft_qset = Draft.objects \
                      .select_related("entity__published") \
                      .filter(entity__learning_package_id=learning_package_id) \
                      .exclude(entity__published__version_id=F("version_id")) \
                      .exclude(
                          # NULL != NULL in SQL, so we want to exclude entries
                          # where both the published version and draft version
                          # are None. This edge case happens when we create
                          # something and then delete it without publishing, and
                          # then reset Drafts to their published state.
                          Q(entity__published__version__isnull=True) &
                          Q(version__isnull=True)
                      )
    # If there's nothing to reset because there are no changes from the
    # published version, just return early rather than making an empty
    # DraftChangeLog.
    if not draft_qset:
        return

    active_change_log = DraftChangeLogContext.get_active_draft_change_log(learning_package_id)

    # If there's an active DraftChangeLog, we're already in a transaction, so
    # there's no need to open a new one.
    tx_context: ContextManager
    if active_change_log:
        tx_context = nullcontext()
    else:
        tx_context = bulk_draft_changes_for(
            learning_package_id, changed_at=reset_at, changed_by=reset_by
        )

    with tx_context:
        # Note: We can't do an .update with a F() on a joined field in the ORM,
        # so we have to loop through the drafts individually to reset them
        # anyhow. We can rework this into a bulk update or custom SQL if it
        # becomes a performance issue, as long as we also port over the
        # bookkeeping code in set_draft_version.
        for draft in draft_qset:
            if hasattr(draft.entity, 'published'):
                published_version_id = draft.entity.published.version_id
            else:
                published_version_id = None

            set_draft_version(draft, published_version_id)


def register_publishable_models(
    content_model_cls: type[PublishableEntityMixin],
    content_version_model_cls: type[PublishableEntityVersionMixin],
) -> PublishableContentModelRegistry:
    """
    Register what content model maps to what content version model.

    This is so that we can provide convenience links between content models and
    content version models *through* the publishing apps, so that you can do
    things like finding the draft version of a content model more easily. See
    the publishable_entity.py module for more details.

    This should only be imported and run from the your app's AppConfig.ready()
    method. For example, in the components app, this looks like:

        def ready(self):
            from ..publishing.api import register_publishable_models
            from .models import Component, ComponentVersion

            register_publishable_models(Component, ComponentVersion)

    There may be a more clever way to introspect this information from the model
    metadata, but this is simple and explicit.
    """
    return PublishableContentModelRegistry.register(
        content_model_cls, content_version_model_cls
    )


def filter_publishable_entities(
    entities: QuerySet[PublishableEntity],
    has_draft=None,
    has_published=None
) -> QuerySet[PublishableEntity]:
    """
    Filter an entities query set.

    has_draft: You can filter by entities that has a draft or not.
    has_published: You can filter by entities that has a published version or not.
    """
    if has_draft is not None:
        entities = entities.filter(draft__version__isnull=not has_draft)
    if has_published is not None:
        entities = entities.filter(published__version__isnull=not has_published)

    return entities


def get_published_version_as_of(
    entity_id: PublishableEntity.ID,
    publish_log_id: int,
) -> PublishableEntityVersion | None:
    """
    Get the published version of the given entity, at a specific snapshot in the
    history of this Learning Package, given by the PublishLog ID.

    This is a semi-private function, only available to other apps in the
    authoring package.
    """
    record = (
        PublishLogRecord.objects.filter(
            entity_id=entity_id,
            publish_log_id__lte=publish_log_id,
        )
        .order_by("-publish_log_id")
        .first()
    )
    return record.new_version if record else None


def bulk_draft_changes_for(
    learning_package_id: LearningPackage.ID,
    changed_by: int | None = None,
    changed_at: datetime | None = None
) -> DraftChangeLogContext:
    """
    Context manager to do a single batch of Draft changes in.

    Each publishable entity that is edited in this context will be tied to a
    single DraftChangeLogRecord, representing the cumulative changes made to
    that entity. Upon closing of the context, side effects of these changes will
    be calcuated, which may result in more DraftChangeLogRecords being created
    or updated. The resulting DraftChangeLogRecords and DraftChangeSideEffects
    will be tied together into a single DraftChangeLog, representing the
    collective changes to the learning package that happened in this context.
    All changes will be committed in a single atomic transaction.

    Example::

        with bulk_draft_changes_for(learning_package.id):
            for section in course:
                update_section_drafts(learning_package.id, section)

    If you make a change to an entity *without* using this context manager, then
    the individual change (and its side effects) will be automatically wrapped
    in a one-off change context. For example, this::

        update_one_component(component.learning_package, component)

    is identical to this::

        with bulk_draft_changes_for(component.learning_package.id):
            update_one_component(component.learning_package.id, component)
    """
    if not changed_at:
        changed_at = datetime.now(tz=timezone.utc)
    return DraftChangeLogContext(
        learning_package_id,
        changed_at=changed_at,
        changed_by=changed_by,
        exit_callbacks=[
            _create_side_effects_for_change_log,
            partial(_emit_event_for_change_log, timestamp=changed_at, user_id=changed_by),
        ]
    )
