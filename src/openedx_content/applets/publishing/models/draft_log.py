"""
Models relating to the creation and management of Draft information.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from openedx_django_lib.fields import hash_field, immutable_uuid_field, manual_date_time_field

from .learning_package import LearningPackage
from .publishable_entity import PublishableEntity, PublishableEntityVersion


class Draft(models.Model):
    """
    Find the active draft version of an entity (usually most recently created).

    This model mostly only exists to allow us to join against a bunch of
    PublishableEntity objects at once and get all their latest drafts. You might
    use this together with Published in order to see which Drafts haven't been
    published yet.

    A Draft entry should be created whenever a new PublishableEntityVersion is
    created. This means there are three possible states:

    1. No Draft entry for a PublishableEntity: This means a PublishableEntity
       was created, but no PublishableEntityVersion was ever made for it, so
       there was never a Draft version.
    2. A Draft entry exists and points to a PublishableEntityVersion: This is
       the most common state.
    3. A Draft entry exists and points to a null version: This means a version
       used to be the draft, but it's been functionally "deleted". The versions
       still exist in our history, but we're done using it.

    It would have saved a little space to add this data to the Published model
    (and possibly call the combined model something else). Split Modulestore did
    this with its active_versions table. I keep it separate here to get a better
    separation of lifecycle events: i.e. this table *only* changes when drafts
    are updated, not when publishing happens. The Published model only changes
    when something is published.

    .. no_pii
    """
    # If we're removing a PublishableEntity entirely, also remove the Draft
    # entry for it. This isn't a normal operation, but can happen if you're
    # deleting an entire LearningPackage.
    entity = models.OneToOneField(
        PublishableEntity,
        on_delete=models.CASCADE,
        primary_key=True,
    )
    version = models.OneToOneField(
        PublishableEntityVersion,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
    )
    # Note: this is actually a 1:1 relation in practice, but I'm keeping the
    # definition more consistent with the Published model, which has an fkey
    # to PublishLogRecord. Unlike PublishLogRecord, this fkey is a late
    # addition to this data model, so we have to allow null values for the
    # initial migration. But making this nullable also has another advantage,
    # in that it allows us to set the draft_log_record to the most recent change
    # while inside a bulk_draft_changes_for operation, and then delete that log
    # record if it is undone in the same bulk operation.
    draft_log_record = models.ForeignKey(
        "DraftChangeLogRecord",
        on_delete=models.SET_NULL,
        null=True,
    )

    @property
    def log_record(self):
        return self.draft_log_record

    class DraftQuerySet(models.QuerySet):
        """
        Custom QuerySet/Manager so we can chain common queries.
        """

        def with_unpublished_changes(self):
            """
            Drafts with versions that are different from what is Published.

            This will not return Drafts that have unpublished changes in their
            dependencies. Example: A Unit is published with a Component as one
            of its child. Then someone modifies the draft of the Component. If
            both the Unit and the Component Drafts were part of the queryset,
            this method would return only the changed Component, and not the
            Unit. (We can add this as an optional flag later if we want.)
            """
            return (
                self.select_related("entity__published__version")

                # Exclude where draft and published versions are the same
                    .exclude(entity__published__version_id=F("version_id"))

                # Account for soft-deletes:
                # NULL != NULL in SQL, so simply excluding entities where
                # the Draft and Published versions match will not catch the
                # case where a soft-delete has been published (i.e. both the
                # Draft and Published versions are NULL). We need to
                # explicitly check for that case instead, or else we will
                # re-publish the same soft-deletes over and over again.
                    .exclude(
                        Q(version__isnull=True) &
                        Q(entity__published__version__isnull=True)
                    )
            )

    objects = DraftQuerySet.as_manager()


class DraftChangeLog(models.Model):
    """
    There is one row in this table for every time Drafts are created/modified.

    There are some operations that affect many Drafts at once, such as
    discarding changes (i.e. reset to the published versions) or doing an
    import. These would be represented by one DraftChangeLog with many
    DraftChangeLogRecords in it--one DraftChangeLogRecord for every
    PublishableEntity that was modified.

    Even if we're only directly changing the draft version of one
    PublishableEntity, we will get multiple DraftChangeLogRecords if changing
    that entity causes side-effects. See the docstrings for DraftChangeLogRecord
    and DraftSideEffect for more details.

    .. no_pii:
    """
    uuid = immutable_uuid_field()
    learning_package = models.ForeignKey(LearningPackage, on_delete=models.CASCADE)
    changed_at = manual_date_time_field()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = _("Draft Change Log")
        verbose_name_plural = _("Draft Change Logs")


class DraftChangeLogRecord(models.Model):
    """
    A single change in the PublishableEntity that Draft points to.

    Within a single DraftChangeLog, there can be only one DraftChangeLogRecord
    per PublishableEntity. If a PublishableEntity goes from v1 -> v2 and then v2
    -> v3 within the same DraftChangeLog, the expectation is that these will be
    collapsed into one DraftChangeLogRecord that goes from v1 -> v3. A single
    PublishableEntity may have many DraftChangeLogRecords that describe its full
    draft edit history, but each DraftChangeLogRecord will be a part of a
    different DraftChangeLog.

    New PublishableEntityVersions are created with a monotonically increasing
    version_num for their PublishableEntity. However, knowing that is not enough
    to accurately reconstruct how the Draft changes over time because the Draft
    does not always point to the most recently created PublishableEntityVersion.
    We also have the concept of side-effects, where we consider a
    PublishableEntity to have changed in some way, even if no new version is
    explicitly created.

    The following scenarios may occur:

    Scenario 1: old_version is None, new_version.version_num = 1

      This is the common case when we're creating the first version for editing.

    Scenario 2: old_version.version_num + 1 == new_version.version_num

      This is the common case when we've made an edit to something, which
      creates the next version of an entity, which we then point the Draft at.

    Scenario 3: old_version.version_num >=1, new_version is None

      This is a soft-deletion. We never actually delete a row from the
      PublishableEntity model, but set its current Draft version to be None
      instead.

    Scenario 4: old_version.version_num > new_version.version_num

      This can happen if we "discard changes", meaning that we call
      reset_drafts_to_published(). The versions we created before resetting
      don't get deleted, but the Draft model's pointer to the current version
      has been reset to match the Published model.

    Scenario 5: old_version.version_num + 1 < new_version.version_num

      Sometimes we'll have a gap between the two version numbers that is > 1.
      This can happen if we make edits (new versions) after we called
      reset_drafts_to_published. PublishableEntityVersions are created with a
      monotonically incrementing version_num which will continue to go up with
      the next edit, regardless of whether Draft is pointing to the most
      recently created version or not. In terms of (old_version, new version)
      changes, it could look like this:

      - (None, v1): Initial creation
      - # Publish happens here, so v1 of this PublishableEntity is published.
      - (v1, v2): Normal edit in draft
      - (v2, v3): Normal edit in draft
      - (v3, v1): Reset to published happened here.
      - (v1, v4): Normal edit in draft

      This could also technically happen if we change the same entity more than
      once in the the same bulk_draft_changes_for() context, thereby putting
      them into the same DraftChangeLog, which forces us to squash the changes
      together into one DraftChangeLogRecord.

    Scenario 6: old_version is None, new_version > 1

      This edge case can happen if we soft-deleted a published entity, and then
      called reset_drafts_to_published before we published that soft-deletion.
      It would effectively undo our soft-delete because the published version
      was not yet marked as deleted.

    Scenario 7: old_version == new_version

      This means that the data associated with the Draft version of an entity
      has changed purely as a side-effect of some other entity changing.

      The main example we have of this are containers. Imagine that we have a
      Unit that is at v1, and has unpinned references to various Components that
      are its children. The Unit's version does not get incremented when the
      Components are edited, because the Unit container is defined to always get
      the most recent version of those Components. We would only make a new
      version of the Unit if we changed the metadata of the Unit itself (e.g.
      the title), or if we added, removed, or reordered the children.

      Yet updating a Component intuitively changes what we think of as the
      content of the Unit. Users who are working on Units also expect that a
      change to a Component will be reflected when looking at a Unit's
      "last updated" info. The old_version == new_version convention lets us
      represent that in a useful way because that Unit *is* a part of the change
      set represented by a DraftChangeLog, even if its own versioned data hasn't
      changed.

    .. no_pii:
    """
    draft_change_log = models.ForeignKey(
        DraftChangeLog,
        on_delete=models.CASCADE,
        related_name="records",
    )
    entity = models.ForeignKey(PublishableEntity, on_delete=models.RESTRICT)
    old_version = models.ForeignKey(
        PublishableEntityVersion,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="+",
    )
    new_version = models.ForeignKey(
        PublishableEntityVersion, on_delete=models.RESTRICT, null=True, blank=True
    )

    # The dependencies_hash_digest is used when the version alone isn't enough
    # to let us know the full draft state of an entity. This happens any time a
    # Draft version has dependencies (see the PublishableEntityVersionDependency
    # model), because changes in those dependencies will cause changes to the
    # state of the Draft. The main example of this is containers, where changing
    # an unpinned child affects the state of the parent container, even if that
    # container's definition (and thus version) does not change.
    #
    # If a Draft has no dependencies, then its entire state is captured by its
    # version, and the dependencies_hash_digest is blank. (Blank is slightly
    # more convenient for database comparisons than NULL.)
    #
    # Note: There is an equivalent of this field in the Published model and the
    # the values may drift away from each other.
    dependencies_hash_digest = hash_field(blank=True, default='', max_length=8)

    class Meta:
        constraints = [
            # A PublishableEntity can have only one DraftLogRecord per DraftLog.
            # You can't simultaneously change the same thing in two different
            # ways, e.g. set the Draft to version 1 and version 2 at the same
            # time; or delete a Draft and set it to version 2 at the same time.
            models.UniqueConstraint(
                fields=[
                    "draft_change_log",
                    "entity",
                ],
                name="oel_dlr_uniq_dcl",
            )
        ]
        indexes = [
            # Entity (reverse) DraftLog Index:
            #   * Find the history of draft changes for a given entity, starting
            #     with the most recent (since IDs are ascending ints).
            models.Index(
                fields=["entity", "-draft_change_log"],
                name="oel_dlr_idx_entity_rdcl",
            ),
        ]
        verbose_name = _("Draft Change Log Record")
        verbose_name_plural = _("Draft Change Log Records")

    def __str__(self):
        old_version_num = None if self.old_version is None else self.old_version.version_num
        new_version_num = None if self.new_version is None else self.new_version.version_num
        return f"DraftChangeLogRecord: {self.entity} ({old_version_num} -> {new_version_num})"


class DraftSideEffect(models.Model):
    """
    Model to track when a change in one Draft affects other Drafts.

    Our first use case for this is that changes involving child components are
    thought to affect parent Units, even if the parent's version doesn't change.

    Side-effects are recorded in a collapsed form that only captures one level.
    So if Components C1 and C2 are both changed and they are part of Unit U1,
    which is in turn a part of Subsection SS1, then the DraftSideEffect entries
    are::

      (C1, U1)
      (C2, U1)
      (U1, SS1)

    We do not keep entries for (C1, SS1) or (C2, SS1). This is to make the model
    simpler, so we don't have to differentiate between direct side-effects and
    transitive side-effects in the model.

    We will record side-effects on a parent container whenever a child changes,
    even if the parent container is also changing in the same DraftChangeLog.
    The child change is still affecting the parent container, whether the
    container happens to be changing for other reasons as well. Whether a parent
    -child relationship exists or not depends on the draft state of the
    container at the *end* of a bulk_draft_changes_for context. To give concrete
    examples:

    Setup: A Unit version U1.v1 has defined C1 to be a child. The current draft
    version of C1 is C1.v1.

    Scenario 1: In the a bulk_draft_changes_for context, we edit C1 so that the
    draft version of C1 is now C1.v2. Result:
    - a DraftChangeLogRecord is created for C1.v1 -> C1.v2
    - a DraftChangeLogRecord is created for U1.v1 -> U1.v1
    - a DraftSideEffect is created with cause (C1.v1 -> C1.v2) and effect
      (U1.v1 -> U1.v1). The Unit draft version has not been incremented because
      the metadata a Unit defines for itself hasn't been altered, but the Unit
      has *changed* in some way because of the side effect of its child being
      edited.

    Scenario 2: In a bulk_draft_changes_for context, we edit C1 so that the
    draft version of C1 is now C1.v2. In the same context, we edit U1's metadata
    so that the draft version of U1 is now U1.v2. U1.v2 still lists C1 as a
    child entity. Result:
    - a DraftChangeLogRecord is created for C1.v1 -> C1.v2
    - a DraftChangeLogRecord is created for U1.v1 -> U1.v2
    - a DraftSideEffect is created with cause (C1.v1 -> C1.v2) and effect
      (U1.v1 -> U1.v2)

    Scenario 3: In a bulk_draft_changes_for context, we edit C1 so that the
    draft version of C1 is now C1.v2. In the same context, we edit U1's list of
    children so that C1 is no longer a child of U1.v2. Result:
    - a DraftChangeLogRecord is created for C1.v1 -> C1.v2
    - a DraftChangeLogRecord is created for U1.v1 -> U1.v2
    - no SideEffect is created, since changing C1 does not have an impact on the
      current draft of U1 (U1.v2). A DraftChangeLog is considered a single
      atomic operation, so there was never a point at which C1.v1 -> C1.v2
      affected the draft state of U1.

    .. no_pii:
    """
    cause = models.ForeignKey(
        DraftChangeLogRecord,
        on_delete=models.RESTRICT,
        related_name='causes',
    )
    effect = models.ForeignKey(
        DraftChangeLogRecord,
        on_delete=models.RESTRICT,
        related_name='affected_by',
    )

    class Meta:
        constraints = [
            # Duplicate entries for cause & effect are just redundant. This is
            # here to guard against weird bugs that might introduce this state.
            models.UniqueConstraint(
                fields=["cause", "effect"],
                name="oel_pub_dse_uniq_c_e",
            )
        ]
        verbose_name = _("Draft Side Effect")
        verbose_name_plural = _("Draft Side Effects")
