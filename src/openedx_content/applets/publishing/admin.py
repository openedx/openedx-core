"""
Django admin for publishing models
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Count, F
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import urlencode

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin, one_to_one_related_model_html

from .models import (
    DraftChangeLog,
    DraftChangeLogRecord,
    LearningPackage,
    PublishableEntity,
    PublishableEntityVersion,
    PublishLog,
    PublishLogRecord,
)
from .models.publish_log import Published


@admin.register(LearningPackage)
class LearningPackageAdmin(ReadOnlyModelAdmin):
    """
    Read-only admin for LearningPackage model
    """

    fields = ["package_ref", "title", "uuid", "created", "updated", "entities_link"]
    readonly_fields = ["package_ref", "title", "uuid", "created", "updated", "entities_link"]
    list_display = ["package_ref", "title", "uuid", "created", "updated"]
    search_fields = ["package_ref", "title", "uuid"]

    @admin.display(description="Entities")
    def entities_link(self, obj: LearningPackage) -> str | None:
        """A link to the entities in the package"""
        count = obj.publishable_entities.count()
        url = reverse("admin:openedx_content_publishableentity_changelist")
        query = urlencode({"learning_package__id__exact": obj.id})
        return format_html('<a href="{}?{}">{}</a>', url, query, count)


class PublishLogRecordTabularInline(admin.TabularInline):
    """
    Inline read-only tabular view for PublishLogRecords
    """
    model = PublishLogRecord
    fields = (
        "entity",
        "title",
        "old_version_num",
        "new_version_num",
        "dependencies_hash_digest",
        "direct",
    )
    readonly_fields = fields

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("entity", "old_version", "new_version")

    def old_version_num(self, pl_record: PublishLogRecord):
        if pl_record.old_version is None:
            return "-"
        return pl_record.old_version.version_num

    def new_version_num(self, pl_record: PublishLogRecord):
        if pl_record.new_version is None:
            return "-"
        return pl_record.new_version.version_num

    def title(self, pl_record: PublishLogRecord):
        """
        Get the title to display for the PublishLogRecord
        """
        if pl_record.new_version:
            return pl_record.new_version.title
        if pl_record.old_version:
            return pl_record.old_version.title
        return ""


@admin.register(PublishLog)
class PublishLogAdmin(ReadOnlyModelAdmin):
    """
    Read-only admin view for PublishLog
    """
    inlines = [PublishLogRecordTabularInline]

    fields = ("uuid", "learning_package", "published_at", "published_by", "message")
    readonly_fields = fields
    list_display = fields
    list_filter = ["learning_package"]


class PublishableEntityVersionTabularInline(admin.TabularInline):
    """
    Tabular inline for a single Draft change.
    """
    model = PublishableEntityVersion

    fields = (
        "version_num",
        "title",
        "created",
        "created_by",
        "dependencies_list",
    )
    readonly_fields = fields

    def dependencies_list(self, version: PublishableEntityVersion):
        identifiers = sorted(
            [str(dep.entity_ref) for dep in version.dependencies.all()]
        )
        return "\n".join(identifiers)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return (
            queryset
            .order_by('-version_num')
            .select_related('created_by', 'entity')
            .prefetch_related('dependencies')
        )


class PublishStatusFilter(admin.SimpleListFilter):
    """
    Custom filter for entities that have unpublished changes.
    """
    title = "publish status"
    parameter_name = "publish_status"

    def lookups(self, request, model_admin):
        return [
            ("unpublished_changes", "Has unpublished changes"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "unpublished_changes":
            return (
                queryset
                .exclude(
                    published__version__isnull=True,
                    draft__version__isnull=True,
                )
                .exclude(
                    published__version=F("draft__version"),
                    published__dependencies_hash_digest=F("draft__dependencies_hash_digest")
                )
            )
        return queryset


@admin.register(PublishableEntity)
class PublishableEntityAdmin(ReadOnlyModelAdmin):
    """
    Read-only admin view for Publishable Entities
    """
    inlines = [PublishableEntityVersionTabularInline]

    list_display = [
        "entity_ref",
        "published_version",
        "draft_version",
        "uuid",
        "learning_package",
        "created",
        "created_by",
        "can_stand_alone",
    ]
    list_filter = ["learning_package", PublishStatusFilter]
    search_fields = ["entity_ref", "uuid"]

    fields = [
        "entity_ref",
        "published_version",
        "draft_version",
        "uuid",
        "learning_package",
        "created",
        "created_by",
        "see_also",
        "can_stand_alone",
    ]
    readonly_fields = [
        "entity_ref",
        "published_version",
        "draft_version",
        "uuid",
        "learning_package",
        "created",
        "created_by",
        "see_also",
        "can_stand_alone",
    ]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "learning_package", "published__version", "draft__version", "created_by"
        )

    def see_also(self, entity):
        return one_to_one_related_model_html(entity)

    def draft_version(self, entity: PublishableEntity):
        """
        Version num + dependency hash if applicable, e.g. "5" or "5 (825064c2)"

        If the version info is different from the published version, we
        italicize the text for emphasis.
        """
        if hasattr(entity, "draft") and entity.draft.version:
            draft_log_record = entity.draft.draft_log_record
            if draft_log_record and draft_log_record.dependencies_hash_digest:
                version_str = (
                    f"{entity.draft.version.version_num} "
                    f"({draft_log_record.dependencies_hash_digest})"
                )
            else:
                version_str = str(entity.draft.version.version_num)

            if version_str == self.published_version(entity):
                return version_str
            else:
                return format_html("<em>{}</em>", version_str)

        return None

    def published_version(self, entity: PublishableEntity):
        """
        Version num + dependency hash if applicable, e.g. "5" or "5 (825064c2)"
        """
        if hasattr(entity, "published") and entity.published.version:
            publish_log_record = entity.published.publish_log_record
            if publish_log_record.dependencies_hash_digest:
                return (
                    f"{entity.published.version.version_num} "
                    f"({publish_log_record.dependencies_hash_digest})"
                )
            else:
                return str(entity.published.version.version_num)

        return None


@admin.register(Published)
class PublishedAdmin(ReadOnlyModelAdmin):
    """
    Read-only admin view for Published model
    """
    fields = ("entity", "version_num", "previous", "published_at", "message")
    list_display = fields

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "entity",
            "version",
            "publish_log_record",
            "publish_log_record__old_version",
            "publish_log_record__publish_log",
        )

    def version_num(self, published_obj):
        if published_obj.version:
            return published_obj.version.version_num
        return None

    def previous(self, published_obj):
        """
        Determine what to show in the "Previous" field
        """
        old_version = published_obj.publish_log_record.old_version
        # if there was no previous old version, old version is None
        if not old_version:
            return old_version
        return old_version.version_num

    def published_at(self, published_obj):
        return published_obj.publish_log_record.publish_log.published_at

    def message(self, published_obj):
        return published_obj.publish_log_record.publish_log.message


class DraftChangeLogRecordTabularInline(admin.TabularInline):
    """
    Tabular inline for a single Draft change.
    """
    model = DraftChangeLogRecord

    fields = (
        "entity",
        "title",
        "old_version_num",
        "new_version_num",
        "dependencies_hash_digest",
    )
    readonly_fields = fields

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("entity", "old_version", "new_version") \
                       .order_by("entity__entity_ref")

    def old_version_num(self, draft_change: DraftChangeLogRecord):
        if draft_change.old_version is None:
            return "-"
        return draft_change.old_version.version_num

    def new_version_num(self, draft_change: DraftChangeLogRecord):
        if draft_change.new_version is None:
            return "-"
        return draft_change.new_version.version_num

    def title(self, draft_change: DraftChangeLogRecord):
        """
        Get the title to display for the DraftChange
        """
        if draft_change.new_version:
            return draft_change.new_version.title
        if draft_change.old_version:
            return draft_change.old_version.title
        return ""


@admin.register(DraftChangeLog)
class DraftChangeSetAdmin(ReadOnlyModelAdmin):
    """
    Read-only admin to view Draft changes (via inline tables)
    """
    inlines = [DraftChangeLogRecordTabularInline]
    fields = (
        "pk",
        "learning_package",
        "num_changes",
        "changed_at",
        "changed_by",
    )
    readonly_fields = fields
    list_display = fields
    list_filter = ["learning_package"]

    def num_changes(self, draft_change_set):
        return draft_change_set.num_changes

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("learning_package", "changed_by") \
                       .annotate(num_changes=Count("records"))
