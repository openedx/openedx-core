"""
Django admin for publishing models
"""
from __future__ import annotations

import functools

from django.contrib import admin
from django.db.models import Count, F
from django.utils.html import format_html
from django.utils.safestring import SafeText

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin, model_detail_link, one_to_one_related_model_html

from .models import (
    Container,
    ContainerVersion,
    DraftChangeLog,
    DraftChangeLogRecord,
    EntityList,
    EntityListRow,
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
    fields = ["key", "title", "uuid", "created", "updated"]
    readonly_fields = ["key", "title", "uuid", "created", "updated"]
    list_display = ["key", "title", "uuid", "created", "updated"]
    search_fields = ["key", "title", "uuid"]


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
            [str(dep.key) for dep in version.dependencies.all()]
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
        "key",
        "published_version",
        "draft_version",
        "uuid",
        "learning_package",
        "created",
        "created_by",
        "can_stand_alone",
    ]
    list_filter = ["learning_package", PublishStatusFilter]
    search_fields = ["key", "uuid"]

    fields = [
        "key",
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
        "key",
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
                       .order_by("entity__key")

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


def _entity_list_detail_link(el: EntityList) -> SafeText:
    """
    A link to the detail page for an EntityList which includes its PK and length.
    """
    num_rows = el.entitylistrow_set.count()
    rows_noun = "row" if num_rows == 1 else "rows"
    return model_detail_link(el, f"EntityList #{el.pk} with {num_rows} {rows_noun}")


class ContainerVersionInlineForContainer(admin.TabularInline):
    """
    Inline admin view of ContainerVersions in a given Container
    """
    model = ContainerVersion
    ordering = ["-publishable_entity_version__version_num"]
    fields = [
        "pk",
        "version_num",
        "title",
        "children",
        "created",
        "created_by",
    ]
    readonly_fields = fields  # type: ignore[assignment]
    extra = 0

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "publishable_entity_version"
        )

    def children(self, obj: ContainerVersion):
        return _entity_list_detail_link(obj.entity_list)


@admin.register(Container)
class ContainerAdmin(ReadOnlyModelAdmin):
    """
    Django admin configuration for Container
    """
    list_display = ("key", "created", "draft", "published", "see_also")
    fields = [
        "pk",
        "publishable_entity",
        "learning_package",
        "draft",
        "published",
        "created",
        "created_by",
        "see_also",
        "most_recent_parent_entity_list",
    ]
    readonly_fields = fields  # type: ignore[assignment]
    search_fields = ["publishable_entity__uuid", "publishable_entity__key"]
    inlines = [ContainerVersionInlineForContainer]

    def learning_package(self, obj: Container) -> SafeText:
        return model_detail_link(
            obj.publishable_entity.learning_package,
            obj.publishable_entity.learning_package.key,
        )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "publishable_entity",
            "publishable_entity__learning_package",
            "publishable_entity__published__version",
            "publishable_entity__draft__version",
        )

    def draft(self, obj: Container) -> str:
        """
        Link to this Container's draft ContainerVersion
        """
        if draft := obj.versioning.draft:
            return format_html(
                'Version {} "{}" ({})', draft.version_num, draft.title, _entity_list_detail_link(draft.entity_list)
            )
        return "-"

    def published(self, obj: Container) -> str:
        """
        Link to this Container's published ContainerVersion
        """
        if published := obj.versioning.published:
            return format_html(
                'Version {} "{}" ({})',
                published.version_num,
                published.title,
                _entity_list_detail_link(published.entity_list),
            )
        return "-"

    def see_also(self, obj: Container):
        return one_to_one_related_model_html(obj)

    def most_recent_parent_entity_list(self, obj: Container) -> str:
        if latest_row := EntityListRow.objects.filter(entity_id=obj.publishable_entity_id).order_by("-pk").first():
            return _entity_list_detail_link(latest_row.entity_list)
        return "-"


class ContainerVersionInlineForEntityList(admin.TabularInline):
    """
    Inline admin view of ContainerVersions which use a given EntityList
    """
    model = ContainerVersion
    verbose_name = "Container Version that references this Entity List"
    verbose_name_plural = "Container Versions that reference this Entity List"
    ordering = ["-pk"]  # Newest first
    fields = [
        "pk",
        "version_num",
        "container_key",
        "title",
        "created",
        "created_by",
    ]
    readonly_fields = fields  # type: ignore[assignment]
    extra = 0

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "container",
            "container__publishable_entity",
            "publishable_entity_version",
        )

    def container_key(self, obj: ContainerVersion) -> SafeText:
        return model_detail_link(obj.container, obj.container.key)


class EntityListRowInline(admin.TabularInline):
    """
    Table of entity rows in the entitylist admin
    """
    model = EntityListRow
    readonly_fields = [
        "order_num",
        "pinned_version_num",
        "entity_models",
        "container_models",
        "container_children",
    ]
    fields = readonly_fields  # type: ignore[assignment]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "entity",
            "entity_version",
        )

    def pinned_version_num(self, obj: EntityListRow):
        return str(obj.entity_version.version_num) if obj.entity_version else "(Unpinned)"

    def entity_models(self, obj: EntityListRow):
        return format_html(
            "{}<ul>{}</ul>",
            model_detail_link(obj.entity, obj.entity.key),
            one_to_one_related_model_html(obj.entity),
        )

    def container_models(self, obj: EntityListRow) -> SafeText:
        if not hasattr(obj.entity, "container"):
            return SafeText("(Not a Container)")
        return format_html(
            "{}<ul>{}</ul>",
            model_detail_link(obj.entity.container, str(obj.entity.container)),
            one_to_one_related_model_html(obj.entity.container),
        )

    def container_children(self, obj: EntityListRow) -> SafeText:
        """
        If this row holds a Container, then link *its* EntityList, allowing easy hierarchy browsing.

        When determining which ContainerVersion to grab the EntityList from, prefer the pinned
        version if there is one; otherwise use the Draft version.
        """
        if not hasattr(obj.entity, "container"):
            return SafeText("(Not a Container)")
        child_container_version: ContainerVersion = (
            obj.entity_version.containerversion
            if obj.entity_version
            else obj.entity.container.versioning.draft
        )
        return _entity_list_detail_link(child_container_version.entity_list)


@admin.register(EntityList)
class EntityListAdmin(ReadOnlyModelAdmin):
    """
    Django admin configuration for EntityList
    """
    list_display = [
        "entity_list",
        "row_count",
        "recent_container_version_num",
        "recent_container",
        "recent_container_package"
    ]
    inlines = [ContainerVersionInlineForEntityList, EntityListRowInline]

    def entity_list(self, obj: EntityList) -> SafeText:
        return model_detail_link(obj, f"EntityList #{obj.pk}")

    def row_count(self, obj: EntityList) -> int:
        return obj.entitylistrow_set.count()

    def recent_container_version_num(self, obj: EntityList) -> str:
        """
        Number of the newest ContainerVersion that references this EntityList
        """
        if latest := _latest_container_version(obj):
            return f"Version {latest.version_num}"
        else:
            return "-"

    def recent_container(self, obj: EntityList) -> SafeText | None:
        """
        Link to the Container of the newest ContainerVersion that references this EntityList
        """
        if latest := _latest_container_version(obj):
            return format_html("of: {}", model_detail_link(latest.container, latest.container.key))
        else:
            return None

    def recent_container_package(self, obj: EntityList) -> SafeText | None:
        """
        Link to the LearningPackage of the newest ContainerVersion that references this EntityList
        """
        if latest := _latest_container_version(obj):
            return format_html(
                "in: {}",
                model_detail_link(
                    latest.container.publishable_entity.learning_package,
                    latest.container.publishable_entity.learning_package.key
                )
            )
        else:
            return None

    # We'd like it to appear as if these three columns are just a single
    # nicely-formatted column, so only give the left one a description.
    recent_container_version_num.short_description = (  # type: ignore[attr-defined]
        "Most recent container version using this entity list"
    )
    recent_container.short_description = ""  # type: ignore[attr-defined]
    recent_container_package.short_description = ""  # type: ignore[attr-defined]


@functools.cache
def _latest_container_version(obj: EntityList) -> ContainerVersion | None:
    """
    Any given EntityList can be used by multiple ContainerVersion (which may even
    span multiple Containers). We only have space here to show one ContainerVersion
    easily, so let's show the one that's most likely to be interesting to the Django
    admin user: the most-recently-created one.
    """
    return obj.container_versions.order_by("-pk").first()
