"""
Publishing API (warning: UNSTABLE, in progress API)

Please look at the models.py file for more information about the kinds of data
are stored in this app.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import ContextManager, Optional, TypeVar

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import F, Prefetch, Q, QuerySet
from django.db.transaction import atomic

from openedx_django_lib.fields import create_hash_digest

from .contextmanagers import DraftChangeLogContext
from .models import (
    Container,
    ContainerVersion,
    Draft,
    DraftChangeLog,
    DraftChangeLogRecord,
    DraftSideEffect,
    EntityList,
    EntityListRow,
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

# A few of the APIs in this file are generic and can be used for Containers in
# general, or e.g. Units (subclass of Container) in particular. These type
# variables are used to provide correct typing for those generic API methods.
ContainerModel = TypeVar('ContainerModel', bound=Container)
ContainerVersionModel = TypeVar('ContainerVersionModel', bound=ContainerVersion)

# The public API that will be re-exported by openedx_content.api
# is listed in the __all__ entries below. Internal helper functions that are
# private to this module should start with an underscore. If a function does not
# start with an underscore AND it is not in __all__, that function is considered
# to be callable only by other apps in the authoring package.
__all__ = [
    "get_learning_package",
    "get_learning_package_by_key",
    "create_learning_package",
    "update_learning_package",
    "learning_package_exists",
    "create_publishable_entity",
    "create_publishable_entity_version",
    "get_publishable_entity",
    "get_publishable_entity_by_key",
    "get_publishable_entities",
    "get_last_publish",
    "get_all_drafts",
    "get_entities_with_unpublished_changes",
    "get_entities_with_unpublished_deletes",
    "publish_all_drafts",
    "publish_from_drafts",
    "get_draft_version",
    "get_published_version",
    "set_draft_version",
    "soft_delete_draft",
    "reset_drafts_to_published",
    "register_publishable_models",
    "filter_publishable_entities",
    # 🛑 UNSTABLE: All APIs related to containers are unstable until we've figured
    #              out our approach to dynamic content (randomized, A/B tests, etc.)
    "create_container",
    "create_container_version",
    "create_next_container_version",
    "get_container",
    "get_container_by_key",
    "get_containers",
    "get_collection_containers",
    "ChildrenEntitiesAction",
    "ContainerEntityListEntry",
    "ContainerEntityRow",
    "get_entities_in_container",
    "contains_unpublished_changes",
    "get_containers_with_entity",
    "get_container_children_count",
    "bulk_draft_changes_for",
    "get_container_children_entities_keys",
]


def get_learning_package(learning_package_id: int, /) -> LearningPackage:
    """
    Get LearningPackage by ID.
    """
    return LearningPackage.objects.get(id=learning_package_id)


def get_learning_package_by_key(key: str) -> LearningPackage:
    """
    Get LearningPackage by key.

    Can throw a NotFoundError
    """
    return LearningPackage.objects.get(key=key)


def create_learning_package(
    key: str, title: str, description: str = "", created: datetime | None = None
) -> LearningPackage:
    """
    Create a new LearningPackage.

    The ``key`` must be unique.

    Errors that can be raised:

    * django.core.exceptions.ValidationError
    """
    if not created:
        created = datetime.now(tz=timezone.utc)

    package = LearningPackage(
        key=key,
        title=title,
        description=description,
        created=created,
        updated=created,
    )
    package.full_clean()
    package.save()

    return package


def update_learning_package(
    learning_package_id: int,
    /,
    key: str | None = None,
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
    if all(field is None for field in [key, title, description, updated]):
        return lp

    if key is not None:
        lp.key = key
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
    return lp


def learning_package_exists(key: str) -> bool:
    """
    Check whether a LearningPackage with a particular key exists.
    """
    return LearningPackage.objects.filter(key=key).exists()


def create_publishable_entity(
    learning_package_id: int,
    /,
    key: str,
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
        key=key,
        created=created,
        created_by_id=created_by,
        can_stand_alone=can_stand_alone,
    )


def create_publishable_entity_version(
    entity_id: int,
    /,
    version_num: int,
    title: str,
    created: datetime,
    created_by: int | None,
    *,
    dependencies: list[int] | None = None,  # PublishableEntity IDs
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
    dependencies: list[int]  # List of PublishableEntity.id
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


def get_publishable_entity(publishable_entity_id: int, /) -> PublishableEntity:
    return PublishableEntity.objects.get(id=publishable_entity_id)


def get_publishable_entity_by_key(learning_package_id, /, key) -> PublishableEntity:
    return PublishableEntity.objects.get(
        learning_package_id=learning_package_id,
        key=key,
    )


def get_last_publish(learning_package_id: int, /) -> PublishLog | None:
    return PublishLog.objects \
                     .filter(learning_package_id=learning_package_id) \
                     .order_by('-id') \
                     .first()


def get_all_drafts(learning_package_id: int, /) -> QuerySet[Draft]:
    return Draft.objects.filter(
        entity__learning_package_id=learning_package_id,
        version__isnull=False,
    )


def get_publishable_entities(learning_package_id: int, /) -> QuerySet[PublishableEntity]:
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
    learning_package_id: int,
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


def get_entities_with_unpublished_deletes(learning_package_id: int, /) -> QuerySet[PublishableEntity]:
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
    learning_package_id: int,
    /,
    message="",
    published_at: datetime | None = None,
    published_by: int | None = None
) -> PublishLog:
    """
    Publish everything that is a Draft and is not already published.
    """
    draft_qset = (
        Draft.objects
             .filter(entity__learning_package_id=learning_package_id)
             .with_unpublished_changes()
    )
    return publish_from_drafts(
        learning_package_id, draft_qset, message, published_at, published_by
    )


def _get_dependencies_with_unpublished_changes(
    draft_qset: QuerySet[Draft]
) -> list[QuerySet[Draft]]:
    """
    Return all dependencies to publish as a list of Draft QuerySets.

    This should only return the Drafts that have actual changes, not pure side-
    effects. The side-effect calculations will happen separately.
    """
    # First we have to do a full crawl of *all* dependencies, regardless of
    # whether they have unpublished changes or not. This is because we might
    # have a dependency-of-a-dependency that has changed somewhere down the
    # line. Example: The draft_qset includes a Subsection. Even if the Unit
    # versions are still the same, there might be a changed Component that needs
    # to be published.
    all_dependency_drafts = []
    dependency_drafts = Draft.objects.filter(
        entity__affects__in=draft_qset.values_list("version_id", flat=True)
    ).distinct()

    while dependency_drafts:
        all_dependency_drafts.append(dependency_drafts)
        dependency_drafts = Draft.objects.filter(
            entity__affects__in=dependency_drafts.all().values_list("version_id", flat=True)
        ).distinct()

    unpublished_dependency_drafts = [
        dependency_drafts_qset.all().with_unpublished_changes()
        for dependency_drafts_qset in all_dependency_drafts
    ]
    return unpublished_dependency_drafts


def publish_from_drafts(
    learning_package_id: int,  # LearningPackage.id
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
    if published_at is None:
        published_at = datetime.now(tz=timezone.utc)

    with atomic():
        if publish_dependencies:
            dependency_drafts_qsets = _get_dependencies_with_unpublished_changes(draft_qset)
        else:
            dependency_drafts_qsets = []

        # One PublishLog for this entire publish operation.
        publish_log = PublishLog(
            learning_package_id=learning_package_id,
            message=message,
            published_at=published_at,
            published_by_id=published_by,
        )
        publish_log.full_clean()
        publish_log.save(force_insert=True)

        # We're intentionally avoiding union() here because Django ORM unions
        # introduce cumbersome restrictions (can only union once, can't
        # select_related on it after, the extra rows must be exactly compatible
        # in unioned qsets, etc.) Instead, we're going to have one queryset per
        # dependency layer.
        all_draft_qsets = [
            draft_qset.select_related("entity__published__version"),
            *dependency_drafts_qsets,  # one QuerySet per layer of dependencies
        ]
        published_draft_ids = set()
        for qset in all_draft_qsets:
            for draft in qset:
                # Skip duplicates that we might get from expanding dependencies.
                if draft.pk in published_draft_ids:
                    continue

                try:
                    old_version = draft.entity.published.version
                except ObjectDoesNotExist:
                    # This means there is no published version yet.
                    old_version = None

                # Create a record describing publishing this particular
                # Publishable (useful for auditing and reverting).
                publish_log_record = PublishLogRecord(
                    publish_log=publish_log,
                    entity=draft.entity,
                    old_version=old_version,
                    new_version=draft.version,
                )
                publish_log_record.full_clean()
                publish_log_record.save(force_insert=True)

                # Update the lookup we use to fetch the published versions
                Published.objects.update_or_create(
                    entity=draft.entity,
                    defaults={
                        "version": draft.version,
                        "publish_log_record": publish_log_record,
                    },
                )

                published_draft_ids.add(draft.pk)

        _create_side_effects_for_change_log(publish_log)

    return publish_log


def get_draft_version(publishable_entity_or_id: PublishableEntity | int, /) -> PublishableEntityVersion | None:
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


def get_published_version(publishable_entity_or_id: PublishableEntity | int, /) -> PublishableEntityVersion | None:
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


def set_draft_version(
    draft_or_id: Draft | int,
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

        # Check to see if we're inside a context manager for an active
        # DraftChangeLog (i.e. what happens if the caller is using the public
        # bulk_draft_changes_for() API call), or if we have to make our own.
        learning_package_id = draft.entity.learning_package_id
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


def _add_to_existing_draft_change_log(
    active_change_log: DraftChangeLog,
    entity_id: int,
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
    branch_cls: type[Draft] | type[Published]
    change_record_cls: type[DraftChangeLogRecord] | type[PublishLogRecord]
    side_effect_cls: type[DraftSideEffect] | type[PublishSideEffect]
    if isinstance(change_log, DraftChangeLog):
        branch_cls = Draft
        change_record_cls = DraftChangeLogRecord
        side_effect_cls = DraftSideEffect
        log_record_rel = "draft_log_record_id"
    elif isinstance(change_log, PublishLog):
        branch_cls = Published
        change_record_cls = PublishLogRecord
        side_effect_cls = PublishSideEffect
        log_record_rel = "publish_log_record_id"
    else:
        raise TypeError(
            f"expected DraftChangeLog or PublishLog, not {type(change_log)}"
        )

    # processed_entity_ids holds the entity IDs that we've already calculated
    # side-effects for. This is to save us from recalculating side-effects for
    # the same dependency relationships over and over again. So if we're calling
    # this function in a loop for all the Components in a Unit, we won't be
    # recalculating the Unit's side-effect on its Subsection, and its
    # Subsection's side-effect on its Section each time through the loop.
    # It also guards against infinite parent-child relationship loops, though
    # those aren't *supposed* to happen anyhow.
    processed_entity_ids: set[int] = set()
    for original_change in change_log.records.all():
        affected_by_original_change = branch_cls.objects.filter(
            version__dependencies=original_change.entity
        )
        changes_and_affected = [
            (original_change, current) for current in affected_by_original_change
        ]

        # These are the Published or Draft objects where we need to repoint the
        # log_record (publish_log_record or draft_change_log_record) to point to
        # the side-effect changes, e.g. the DraftChangeLogRecord that says,
        # "This Unit's version stayed the same, but its dependency hash changed
        # because a child Component's draft version was changed." We gather them
        # all up in a list so we can do a bulk_update on them.
        branch_objs_to_update_with_side_effects = []

        while changes_and_affected:
            change, affected = changes_and_affected.pop()
            change_log_param = {}
            if branch_cls == Draft:
                change_log_param['draft_change_log'] = change.draft_change_log  # type: ignore[union-attr]
            elif branch_cls == Published:
                change_log_param['publish_log'] = change.publish_log  # type: ignore[union-attr]

            # Example: If the original_change is a DraftChangeLogRecord that
            # represents editing a Component, the side_effect_change is the
            # DraftChangeLogRecord that represents the fact that the containing
            # Unit was also altered (even if the Unit version doesn't change).
            side_effect_change, _created = change_record_cls.objects.get_or_create(
                **change_log_param,
                entity_id=affected.entity_id,
                defaults={
                    # If a change record already exists because the affected
                    # entity was separately modified, then we don't touch the
                    # old/new version entries. But if we're creating this change
                    # record as a pure side-effect, then we use the (old_version
                    # == new_version) convention to indicate that.
                    'old_version_id': affected.version_id,
                    'new_version_id': affected.version_id,
                }
            )
            # Update the current branch pointer (Draft or Published) for this
            # entity to point to the side_effect_change (if it's not already).
            if branch_cls == Published:
                published_obj = affected.entity.published
                if published_obj.publish_log_record != side_effect_change:
                    published_obj.publish_log_record = side_effect_change
                    branch_objs_to_update_with_side_effects.append(published_obj)
            elif branch_cls == Draft:
                draft_obj = affected.entity.draft
                if draft_obj.draft_log_record != side_effect_change:
                    draft_obj.draft_log_record = side_effect_change
                    branch_objs_to_update_with_side_effects.append(draft_obj)

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
            side_effect_cls.objects.get_or_create(
                cause=change,
                effect=side_effect_change,
            )

            # Now we find the next layer up by looking at Drafts or Published
            # that have ``affected.entity`` as a dependency.
            next_layer_of_affected = branch_cls.objects.filter(
                version__dependencies=affected.entity
            )

            # Make sure we never re-add the change we just processed when we
            # queue up the next layer.
            processed_entity_ids.add(change.entity_id)

            changes_and_affected.extend(
                (side_effect_change, affected)
                for affected in next_layer_of_affected
                if affected.entity_id not in processed_entity_ids
            )

        branch_cls.objects.bulk_update(
            branch_objs_to_update_with_side_effects,  # type: ignore[arg-type]
            [log_record_rel],
        )

    update_dependencies_hash_digests_for_log(change_log)


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


def soft_delete_draft(publishable_entity_id: int, /, deleted_by: int | None = None) -> None:
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
    learning_package_id: int,
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


def get_published_version_as_of(entity_id: int, publish_log_id: int) -> PublishableEntityVersion | None:
    """
    Get the published version of the given entity, at a specific snapshot in the
    history of this Learning Package, given by the PublishLog ID.

    This is a semi-private function, only available to other apps in the
    authoring package.
    """
    record = PublishLogRecord.objects.filter(
        entity_id=entity_id,
        publish_log_id__lte=publish_log_id,
    ).order_by('-publish_log_id').first()
    return record.new_version if record else None


def create_container(
    learning_package_id: int,
    key: str,
    created: datetime,
    created_by: int | None,
    *,
    can_stand_alone: bool = True,
    # The types on the following line are correct, but mypy will complain - https://github.com/python/mypy/issues/3737
    container_cls: type[ContainerModel] = Container,  # type: ignore[assignment]
) -> ContainerModel:
    """
    [ 🛑 UNSTABLE ]
    Create a new container.

    Args:
        learning_package_id: The ID of the learning package that contains the container.
        key: The key of the container.
        created: The date and time the container was created.
        created_by: The ID of the user who created the container
        can_stand_alone: Set to False when created as part of containers
        container_cls: The subclass of Container to use, if applicable

    Returns:
        The newly created container.
    """
    assert issubclass(container_cls, Container)
    with atomic():
        publishable_entity = create_publishable_entity(
            learning_package_id,
            key,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        container = container_cls.objects.create(
            publishable_entity=publishable_entity,
        )
    return container


def create_entity_list() -> EntityList:
    """
    [ 🛑 UNSTABLE ]
    Create a new entity list. This is an structure that holds a list of entities
    that will be referenced by the container.

    Returns:
        The newly created entity list.
    """
    return EntityList.objects.create()


def create_entity_list_with_rows(
    entity_rows: list[ContainerEntityRow],
    *,
    learning_package_id: int | None,
) -> EntityList:
    """
    [ 🛑 UNSTABLE ]
    Create new entity list rows for an entity list.

    Args:
        entity_rows: List of ContainerEntityRows specifying the publishable entity ID and version ID (if pinned).
        learning_package_id: Optional. Verify that all the entities are from
            the specified learning package.

    Returns:
        The newly created entity list.
    """
    # Do a quick check that the given entities are in the right learning package:
    if learning_package_id:
        if PublishableEntity.objects.filter(
            pk__in=[entity.entity_pk for entity in entity_rows],
        ).exclude(
            learning_package_id=learning_package_id,
        ).exists():
            raise ValidationError("Container entities must be from the same learning package.")

    # Ensure that any pinned entity versions are linked to the correct entity
    pinned_entities = {
        entity.version_pk: entity.entity_pk
        for entity in entity_rows if entity.pinned
    }
    if pinned_entities:
        entity_versions = PublishableEntityVersion.objects.filter(
            pk__in=pinned_entities.keys(),
        ).only('pk', 'entity_id')
        for entity_version in entity_versions:
            if pinned_entities[entity_version.pk] != entity_version.entity_id:
                raise ValidationError("Container entity versions must belong to the specified entity.")

    with atomic(savepoint=False):
        entity_list = create_entity_list()
        EntityListRow.objects.bulk_create(
            [
                EntityListRow(
                    entity_list=entity_list,
                    entity_id=entity.entity_pk,
                    order_num=order_num,
                    entity_version_id=entity.version_pk,
                )
                for order_num, entity in enumerate(entity_rows)
            ]
        )
    return entity_list


def _create_container_version(
    container: Container,
    version_num: int,
    *,
    title: str,
    entity_list: EntityList,
    created: datetime,
    created_by: int | None,
    container_version_cls: type[ContainerVersionModel] = ContainerVersion,  # type: ignore[assignment]
) -> ContainerVersionModel:
    """
    Private internal method for logic shared by create_container_version() and
    create_next_container_version().
    """
    assert issubclass(container_version_cls, ContainerVersion)
    with atomic(savepoint=False):  # Make sure this will happen atomically but we don't need to create a new savepoint.
        publishable_entity_version = create_publishable_entity_version(
            container.publishable_entity_id,
            version_num=version_num,
            title=title,
            created=created,
            created_by=created_by,
            dependencies=[
                entity_row.entity_id
                for entity_row in entity_list.rows
                if entity_row.is_unpinned()
            ]
        )
        container_version = container_version_cls.objects.create(
            publishable_entity_version=publishable_entity_version,
            container_id=container.pk,
            entity_list=entity_list,
        )

    return container_version


def create_container_version(
    container_id: int,
    version_num: int,
    *,
    title: str,
    entity_rows: list[ContainerEntityRow],
    created: datetime,
    created_by: int | None,
    container_version_cls: type[ContainerVersionModel] = ContainerVersion,  # type: ignore[assignment]
) -> ContainerVersionModel:
    """
    [ 🛑 UNSTABLE ]
    Create a new container version.

    Args:
        container_id: The ID of the container that the version belongs to.
        version_num: The version number of the container.
        title: The title of the container.
        entity_rows: List of ContainerEntityRows specifying the publishable entity ID and version ID (if pinned).
        created: The date and time the container version was created.
        created_by: The ID of the user who created the container version.
        container_version_cls: The subclass of ContainerVersion to use, if applicable.

    Returns:
        The newly created container version.
    """
    assert title is not None
    assert entity_rows is not None

    with atomic(savepoint=False):
        container = Container.objects.select_related("publishable_entity").get(pk=container_id)
        entity = container.publishable_entity
        entity_list = create_entity_list_with_rows(
            entity_rows=entity_rows,
            learning_package_id=entity.learning_package_id,
        )
        container_version = _create_container_version(
            container,
            version_num,
            title=title,
            entity_list=entity_list,
            created=created,
            created_by=created_by,
            container_version_cls=container_version_cls,
        )

    return container_version


class ChildrenEntitiesAction(Enum):
    """Possible actions for children entities"""

    APPEND = "append"
    REMOVE = "remove"
    REPLACE = "replace"


def create_next_entity_list(
    learning_package_id: int,
    last_version: ContainerVersion,
    entity_rows: list[ContainerEntityRow],
    entities_action: ChildrenEntitiesAction = ChildrenEntitiesAction.REPLACE,
) -> EntityList:
    """
    Creates next entity list based on the given entities_action.

    Args:
        learning_package_id: Learning package ID
        last_version: Last version of container.
        entity_rows: List of ContainerEntityRows specifying the publishable entity ID and version ID (if pinned).
        entities_action: APPEND, REMOVE or REPLACE given entities from/to the container

    Returns:
        The newly created entity list.
    """
    if entities_action == ChildrenEntitiesAction.APPEND:
        # get previous entity list rows
        last_entities = last_version.entity_list.entitylistrow_set.only(
            "entity_id",
            "entity_version_id"
        ).order_by("order_num")
        # append given entity_rows to the existing children
        entity_rows = [
            ContainerEntityRow(
                entity_pk=entity.entity_id,
                version_pk=entity.entity_version_id,
            )
            for entity in last_entities
        ] + entity_rows
    elif entities_action == ChildrenEntitiesAction.REMOVE:
        # get previous entity list, excluding the entities in entity_rows
        last_entities = last_version.entity_list.entitylistrow_set.only(
            "entity_id",
            "entity_version_id"
        ).exclude(
            entity_id__in=[entity.entity_pk for entity in entity_rows]
        ).order_by("order_num")
        entity_rows = [
            ContainerEntityRow(
                entity_pk=entity.entity_id,
                version_pk=entity.entity_version_id,
            )
            for entity in last_entities.all()
        ]

    return create_entity_list_with_rows(
        entity_rows=entity_rows,
        learning_package_id=learning_package_id,
    )


def create_next_container_version(
    container_pk: int,
    *,
    title: str | None,
    entity_rows: list[ContainerEntityRow] | None,
    created: datetime,
    created_by: int | None,
    container_version_cls: type[ContainerVersionModel] = ContainerVersion,  # type: ignore[assignment]
    entities_action: ChildrenEntitiesAction = ChildrenEntitiesAction.REPLACE,
    force_version_num: int | None = None,
) -> ContainerVersionModel:
    """
    [ 🛑 UNSTABLE ]
    Create the next version of a container. A new version of the container is created
    only when its metadata changes:

    * Something was added to the Container.
    * We re-ordered the rows in the container.
    * Something was removed from the container.
    * The Container's metadata changed, e.g. the title.
    * We pin to different versions of the Container.

    Args:
        container_pk: The ID of the container to create the next version of.
        title: The title of the container. None to keep the current title.
        entity_rows: List of ContainerEntityRows specifying the publishable entity ID and version ID (if pinned).
        Or None for no change.
        created: The date and time the container version was created.
        created_by: The ID of the user who created the container version.
        container_version_cls: The subclass of ContainerVersion to use, if applicable.
        force_version_num (int, optional): If provided, overrides the automatic version number increment and sets
            this version's number explicitly. Use this if you need to restore or import a version with a specific
            version number, such as during data migration or when synchronizing with external systems.

    Returns:
        The newly created container version.

    Why use force_version_num?
        Normally, the version number is incremented automatically from the latest version.
        If you need to set a specific version number (for example, when restoring from backup,
        importing legacy data, or synchronizing with another system),
        use force_version_num to override the default behavior.
    """
    assert issubclass(container_version_cls, ContainerVersion)
    with atomic():
        container = Container.objects.select_related("publishable_entity").get(pk=container_pk)
        entity = container.publishable_entity
        last_version = container.versioning.latest
        if last_version is None:
            next_version_num = 1
        else:
            next_version_num = last_version.version_num + 1

        if force_version_num is not None:
            next_version_num = force_version_num

        if entity_rows is None and last_version is not None:
            # We're only changing metadata. Keep the same entity list.
            next_entity_list = last_version.entity_list
        else:
            next_entity_list = create_next_entity_list(
                entity.learning_package_id,
                last_version,
                entity_rows if entity_rows is not None else [],
                entities_action
            )

        next_container_version = _create_container_version(
            container,
            next_version_num,
            title=title if title is not None else last_version.title,
            entity_list=next_entity_list,
            created=created,
            created_by=created_by,
            container_version_cls=container_version_cls,
        )

    return next_container_version


def get_container(pk: int) -> Container:
    """
    [ 🛑 UNSTABLE ]
    Get a container by its primary key.

    Args:
        pk: The primary key of the container.

    Returns:
        The container with the given primary key.
    """
    return Container.objects.get(pk=pk)


def get_container_by_key(learning_package_id: int, /, key: str) -> Container:
    """
    [ 🛑 UNSTABLE ]
    Get a container by its learning package and primary key.

    Args:
        learning_package_id: The ID of the learning package that contains the container.
        key: The primary key of the container.

    Returns:
        The container with the given primary key.
    """
    return Container.objects.get(
        publishable_entity__learning_package_id=learning_package_id,
        publishable_entity__key=key,
    )


def get_containers(
    learning_package_id: int,
    container_cls: type[ContainerModel] = Container,  # type: ignore[assignment]
    include_deleted: bool | None = False,
) -> QuerySet[ContainerModel]:
    """
    [ 🛑 UNSTABLE ]
    Get all containers in the given learning package.

    Args:
        learning_package_id: The primary key of the learning package
        container_cls: The subclass of Container to use, if applicable
        include_deleted: If True, include deleted containers (with no draft version) in the result.

    Returns:
        A queryset containing the container associated with the given learning package.
    """
    assert issubclass(container_cls, Container)
    container_qset = container_cls.objects.filter(publishable_entity__learning_package=learning_package_id)
    if not include_deleted:
        container_qset = container_qset.filter(publishable_entity__draft__version__isnull=False)

    return container_qset.order_by('pk')


def get_collection_containers(
    learning_package_id: int,
    collection_key: str,
) -> QuerySet[Container]:
    """
    Returns a QuerySet of Containers relating to the PublishableEntities in a Collection.

    Containers have a one-to-one relationship with PublishableEntity, but the reverse may not always be true.
    """
    return Container.objects.filter(
        publishable_entity__learning_package_id=learning_package_id,
        publishable_entity__collections__key=collection_key,
    ).order_by('pk')


@dataclass(frozen=True)
class ContainerEntityListEntry:
    """
    [ 🛑 UNSTABLE ]
    Data about a single entity in a container, e.g. a component in a unit.
    """
    entity_version: PublishableEntityVersion
    pinned: bool

    @property
    def entity(self):
        return self.entity_version.entity


@dataclass(frozen=True, kw_only=True, slots=True)
class ContainerEntityRow:
    """
    [ 🛑 UNSTABLE ]
    Used to specify the primary key of PublishableEntity and optional PublishableEntityVersion.

    If version_pk is None (default), then the entity is considered "unpinned",
    meaning that the latest version of the entity will be used.
    """
    entity_pk: int
    version_pk: int | None = None

    @property
    def pinned(self):
        return self.entity_pk and self.version_pk is not None


def get_entities_in_container(
    container: Container,
    *,
    published: bool,
    select_related_version: str | None = None,
) -> list[ContainerEntityListEntry]:
    """
    [ 🛑 UNSTABLE ]
    Get the list of entities and their versions in the current draft or
    published version of the given container.

    Args:
        container: The Container, e.g. returned by `get_container()`
        published: `True` if we want the published version of the container, or
            `False` for the draft version.
        select_related_version: An optional optimization; specify a relationship
        on ContainerVersion, like `componentversion` or `containerversion__x`
        to preload via select_related.
    """
    assert isinstance(container, Container)
    if published:
        # Very minor optimization: reload the container with related 1:1 entities
        container = Container.objects.select_related(
            "publishable_entity__published__version__containerversion__entity_list").get(pk=container.pk)
        container_version = container.versioning.published
        select_related = ["entity__published__version"]
        if select_related_version:
            select_related.append(f"entity__published__version__{select_related_version}")
    else:
        # Very minor optimization: reload the container with related 1:1 entities
        container = Container.objects.select_related(
            "publishable_entity__draft__version__containerversion__entity_list").get(pk=container.pk)
        container_version = container.versioning.draft
        select_related = ["entity__draft__version"]
        if select_related_version:
            select_related.append(f"entity__draft__version__{select_related_version}")
    if container_version is None:
        raise ContainerVersion.DoesNotExist  # This container has not been published yet, or has been deleted.
    assert isinstance(container_version, ContainerVersion)
    entity_list: list[ContainerEntityListEntry] = []
    for row in container_version.entity_list.entitylistrow_set.select_related(
        "entity_version",
        *select_related,
    ).order_by("order_num"):
        entity_version = row.entity_version  # This will be set if pinned
        if not entity_version:  # If this entity is "unpinned", use the latest published/draft version:
            entity_version = row.entity.published.version if published else row.entity.draft.version
        if entity_version is not None:  # As long as this hasn't been soft-deleted:
            entity_list.append(ContainerEntityListEntry(
                entity_version=entity_version,
                pinned=row.entity_version is not None,
            ))
        # else we could indicate somehow a deleted item was here, e.g. by returning a ContainerEntityListEntry with
        # deleted=True, but we don't have a use case for that yet.
    return entity_list


def contains_unpublished_changes(container_id: int) -> bool:
    """
    [ 🛑 UNSTABLE ]
    Check recursively if a container has any unpublished changes.

    Note: I've preserved the API signature for now, but we probably eventually
    want to make a more general function that operates on PublishableEntities
    and dependencies, once we introduce those with courses and their files,
    grading policies, etc.

    Note: unlike this method, the similar-sounding
    `container.versioning.has_unpublished_changes` property only reports
    if the container itself has unpublished changes, not
    if its contents do. So if you change a title or add a new child component,
    `has_unpublished_changes` will be `True`, but if you merely edit a component
    that's in the container, it will be `False`. This method will return `True`
    in either case.
    """
    container = (
        Container.objects
                 .select_related('publishable_entity__draft__draft_log_record')
                 .select_related('publishable_entity__published__publish_log_record')
                 .get(pk=container_id)
    )
    if container.versioning.has_unpublished_changes:
        return True

    draft = container.publishable_entity.draft
    published = container.publishable_entity.published

    # Edge case: A container that was created and then immediately soft-deleted
    # does not contain any unpublished changes.
    if draft is None and published is None:
        return False

    # The dependencies_hash_digest captures the state of all descendants, so we
    # can do this quick comparison instead of iterating through layers of
    # containers.
    draft_version_hash_digest = draft.log_record.dependencies_hash_digest
    published_version_hash_digest = published.log_record.dependencies_hash_digest

    return draft_version_hash_digest != published_version_hash_digest


def get_containers_with_entity(
    publishable_entity_pk: int,
    *,
    ignore_pinned=False,
    published=False,
) -> QuerySet[Container]:
    """
    [ 🛑 UNSTABLE ]
    Find all draft containers that directly contain the given entity.

    They will always be from the same learning package; cross-package containers
    are not allowed.

    Args:
        publishable_entity_pk: The ID of the PublishableEntity to search for.
        ignore_pinned: if true, ignore any pinned references to the entity.
    """
    branch = "published" if published else "draft"
    if ignore_pinned:
        filter_dict = {
            # Note: these two conditions must be in the same filter() call,
            # or the query won't be correct.
            (
                f"publishable_entity__{branch}__version__"
                "containerversion__entity_list__entitylistrow__entity_id"
            ): publishable_entity_pk,
            (
                f"publishable_entity__{branch}__version__"
                "containerversion__entity_list__entitylistrow__entity_version_id"
            ): None,
        }
        qs = Container.objects.filter(**filter_dict)
    else:
        filter_dict = {
            (
                f"publishable_entity__{branch}__version__"
                "containerversion__entity_list__entitylistrow__entity_id"
            ): publishable_entity_pk
        }
        qs = Container.objects.filter(**filter_dict)

    return qs.order_by("pk").distinct()  # Ordering is mostly for consistent test cases.


def get_container_children_count(
    container: Container,
    *,
    published: bool,
):
    """
    [ 🛑 UNSTABLE ]
    Get the count of entities in the current draft or published version of the given container.

    Args:
        container: The Container, e.g. returned by `get_container()`
        published: `True` if we want the published version of the container, or
            `False` for the draft version.
    """
    assert isinstance(container, Container)
    container_version = container.versioning.published if published else container.versioning.draft
    if container_version is None:
        raise ContainerVersion.DoesNotExist  # This container has not been published yet, or has been deleted.
    assert isinstance(container_version, ContainerVersion)
    if published:
        filter_deleted = {"entity__published__version__isnull": False}
    else:
        filter_deleted = {"entity__draft__version__isnull": False}
    return container_version.entity_list.entitylistrow_set.filter(**filter_deleted).count()


def get_container_children_entities_keys(container_version: ContainerVersion) -> list[str]:
    """
    Fetch the list of entity keys for all entities in the given container version.

    Args:
        container_version: The ContainerVersion to fetch the entity keys for.
    Returns:
        A list of entity keys for all entities in the container version, ordered by entity key.
    """
    return list(
        container_version.entity_list.entitylistrow_set
        .values_list("entity__key", flat=True)
        .order_by("order_num")
    )


def bulk_draft_changes_for(
    learning_package_id: int,
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
    return DraftChangeLogContext(
        learning_package_id,
        changed_at=changed_at,
        changed_by=changed_by,
        exit_callbacks=[
            _create_side_effects_for_change_log,
        ]
    )
