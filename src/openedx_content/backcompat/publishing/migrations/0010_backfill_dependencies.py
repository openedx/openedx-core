"""
Backfill PublishableEntityVersionDependency entries based on ContainerVersions.

We're introducing a lower-level publishing concept of a dependency that will be
used by Containers, but this means we have to backfill that dependency info for
existing Containers in the system.

For more information, please see the PublishableEntityVersionDependency model.

Note that we copy a *lot* of API code here because we want to make sure that
this migration continues to work on the historical version of the models at this
point in time, regardless of any future changes that might be made to the API
for calculating dependencies. Most of the API code here is copy-pasted from the
publishing API, with very minor changes to grab the historical models using
apps.get_model(), rather than importing the models directly.
"""
from django.db import migrations
from django.db.models import F, Prefetch

from openedx_django_lib.fields import create_hash_digest


def create_backfill(apps, schema_editor):
    """
    Create dependency entries and update dep hashes for Draft and Published.
    """
    _create_dependencies(apps)
    _update_drafts(apps)
    _update_draft_dependencies_hashes(apps)
    _update_published_dependencies_hashes(apps)


def _create_dependencies(apps):
    """
    Populate the PublishableEntityVersion.dependencies relation.

    The only ones we should have in the system at this point are the ones from
    containers, so we query ContainerVersion for that.
    """
    PublishableEntityVersionDependency = apps.get_model(
        "oel_publishing", "PublishableEntityVersionDependency"
    )
    ContainerVersion = apps.get_model("oel_publishing", "ContainerVersion")

    for container_version in ContainerVersion.objects.all():
        # child_entity_ids is a set to de-dupe. This doesn't handle pinned
        # child references yet, but you can't actually make those in a real
        # library yet, so we shouldn't have that data lying around to migrate.
        child_entity_ids = set(
            container_version
                .entity_list
                .entitylistrow_set
                .all()
                .values_list("entity_id", flat=True)
        )
        PublishableEntityVersionDependency.objects.bulk_create(
            [
                PublishableEntityVersionDependency(
                    referring_version_id=container_version.pk,
                    referenced_entity_id=entity_id
                )
                for entity_id in child_entity_ids
            ],
            ignore_conflicts=True,
        )


def _update_drafts(apps):
    """
    Update Draft entries to point to their most recent DraftLogRecord.

    This is slow and expensive.
    """
    Draft = apps.get_model("oel_publishing", "Draft")
    DraftChangeLogRecord = apps.get_model("oel_publishing", "DraftChangeLogRecord")
    for draft in Draft.objects.all():
        draft_log_record = (
            # Find the most recent DraftChangeLogRecord related to this Draft,
            DraftChangeLogRecord.objects
                .filter(entity_id=draft.pk)
                .order_by('-pk')
                .first()
        )
        draft.draft_log_record = draft_log_record
        draft.save()


def _update_draft_dependencies_hashes(apps):
    """
    Update the dependency_hash_digest for all DraftChangeLogRecords.

    Backfill dependency state hashes. The important thing here is that things
    without dependencies will have the default (blank) state hash, so we only
    need to query for Draft entries for Containers.

    We are only backfilling the current DraftChangeLogRecords pointed to by the
    Draft entries now. We are not backfilling all historical
    DraftChangeLogRecords. Full historical reconstruction is probably possible,
    but it's not really worth the cost and complexity.
    """
    DraftChangeLog = apps.get_model("oel_publishing", "DraftChangeLog")

    # All DraftChangeLogs that have records that are pointed to by the current
    # Draft and have a possibility of having dependencies.
    change_logs = DraftChangeLog.objects.filter(
        pk=F('records__entity__draft__draft_log_record__draft_change_log'),
        records__entity__draft__version__isnull=False,
        records__entity__container__isnull=False,
    ).distinct()
    for change_log in change_logs:
        update_dependencies_hash_digests_for_log(change_log, apps)

def _update_published_dependencies_hashes(apps):
    """
    Update all container Published.dependencies_hash_digest

    Backfill dependency state hashes. The important thing here is that things
    without dependencies will have the default (blank) state hash, so we only
    need to query for Published entries for Containers.
    """
    PublishLog = apps.get_model("oel_publishing", "PublishLog")

    # All PublishLogs that have records that are pointed to by the current
    # Published and have a possibility of having dependencies.
    change_logs = PublishLog.objects.filter(
        pk=F('records__entity__published__publish_log_record__publish_log'),
        records__entity__published__version__isnull=False,
        records__entity__container__isnull=False,
    ).distinct()
    for change_log in change_logs:
        update_dependencies_hash_digests_for_log(change_log, apps)

def remove_backfill(apps, schema_editor):
    """
    Reset all dep hash values to default ('') and remove dependencies.
    """
    Draft = apps.get_model("oel_publishing", "Draft")
    DraftChangeLogRecord = apps.get_model("oel_publishing", "DraftChangeLogRecord")
    PublishLogRecord = apps.get_model("oel_publishing", "PublishLogRecord")
    PublishableEntityVersionDependency = apps.get_model(
        "oel_publishing", "PublishableEntityVersionDependency"
    )

    PublishLogRecord.objects.all().update(dependencies_hash_digest='')
    DraftChangeLogRecord.objects.all().update(dependencies_hash_digest='')
    PublishableEntityVersionDependency.objects.all().delete()
    Draft.objects.all().update(draft_log_record=None)


def update_dependencies_hash_digests_for_log(
    change_log,  # this is a historical DraftChangeLog or PublishLog
    apps,
    backfill=True,
) -> None:
    """
    Update dependencies_hash_digest for Drafts or Published in a change log.

    This is copied from the publishing API to make sure we don't accidentally
    break it with future changes as the data model evolves. It has also been
    modified to use historical models, rather than having references to the new
    ones that have been moved to the centralized authoring app. It has also been
    modified to assume that it's being used as a backfill (the original makes it
    optional).

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
    DraftChangeLog = apps.get_model("oel_publishing", "DraftChangeLog")
    DraftChangeLogRecord = apps.get_model("oel_publishing", "DraftChangeLogRecord")
    PublishLog = apps.get_model("oel_publishing", "PublishLog")
    PublishLogRecord = apps.get_model("oel_publishing", "PublishLogRecord")
    PublishableEntity = apps.get_model("oel_publishing", "PublishableEntity")

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
    changed_records = (
        change_log.records
                  .select_related("new_version", f"entity__{branch}")
                  .prefetch_related(dependencies_prefetch)
    )

    record_ids_to_hash_digests: dict[int, str | None] = {}
    record_ids_to_live_deps: dict[int, list] = {}
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
            apps,
            record,
            record_ids_to_hash_digests,
            record_ids_to_live_deps,
            untrusted_record_id_set,
        )

    _bulk_update_hashes(record_cls, records_that_need_hashes)


def _bulk_update_hashes(model_cls, records):
    """
    bulk_update using the model class (PublishLogRecord or DraftChangeLogRecord)

    This is copied from the publishing API to make sure we don't accidentally
    break it with future changes as the data model evolves.
    """
    model_cls.objects.bulk_update(records, ['dependencies_hash_digest'])


def hash_for_log_record(
    apps,
    record,  # historical DraftChangeLogRecord | PublishLogRecord,
    record_ids_to_hash_digests: dict,
    record_ids_to_live_deps: dict,
    untrusted_record_id_set: set | None,
) -> str:
    """
    The hash digest for a given change log record.

    This is copied from the publishing API to make sure we don't accidentally
    break it with future changes as the data model evolves. It has also been
    modified to use historical models, rather than having references to the new
    ones that have been moved to the centralized authoring app.

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
    DraftChangeLogRecord = apps.get_model("oel_publishing", "DraftChangeLogRecord")
    PublishLogRecord = apps.get_model("oel_publishing", "PublishLogRecord")

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
    dependencies = sorted(
        record_ids_to_live_deps[record.id],
        key=lambda entity: getattr(entity, branch).log_record.new_version_id,
    )
    dep_state_entries = []
    for dep_entity in dependencies:
        new_version_id = getattr(dep_entity, branch).log_record.new_version_id
        hash_digest = hash_for_log_record(
            apps,
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


class Migration(migrations.Migration):

    dependencies = [
        ('oel_publishing', '0009_dependencies_and_hashing'),
    ]

    operations = [
        migrations.RunPython(create_backfill, reverse_code=remove_backfill)
    ]
