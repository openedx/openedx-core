"""
Django admin for the assets applet.

Unlike Container subclasses (which are browsable through the generic Container
admin), Asset and AssetBundle are standalone PublishableEntities, so they each
get their own read-only admin here.
"""
from django.contrib import admin
from django.template.defaultfilters import filesizeformat

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin, model_detail_link

from .models import Asset, AssetBundle, AssetBundleType, AssetBundleVersion, AssetType, AssetVersion


@admin.register(AssetType)
class AssetTypeAdmin(ReadOnlyModelAdmin):
    """Read-only admin for AssetType."""

    list_display = ("code",)
    search_fields = ("code",)


@admin.register(AssetBundleType)
class AssetBundleTypeAdmin(ReadOnlyModelAdmin):
    """Read-only admin for AssetBundleType."""

    list_display = ("code",)
    search_fields = ("code",)


class AssetVersionInline(admin.TabularInline):
    """Inline view of AssetVersions from the Asset admin."""

    model = AssetVersion
    fields = ["version_num", "title", "uuid", "created"]
    readonly_fields = fields  # type: ignore[assignment]
    extra = 0

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("publishable_entity_version")


@admin.register(Asset)
class AssetAdmin(ReadOnlyModelAdmin):
    """Read-only admin for Asset."""

    list_display = ("asset_code", "asset_type", "uuid", "created")
    readonly_fields = ["learning_package", "asset_type", "asset_code", "uuid", "created"]
    list_filter = ("asset_type", "learning_package")
    search_fields = ["asset_code", "publishable_entity__uuid", "publishable_entity__entity_ref"]
    inlines = [AssetVersionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "asset_type",
            "publishable_entity",
            "publishable_entity__learning_package",
        )


class AssetVersionMediaInline(admin.TabularInline):
    """Inline view of the Media variants attached to an AssetVersion."""

    model = AssetVersion.media.through
    fields = ["variant", "media", "format_size"]
    readonly_fields = fields  # type: ignore[assignment]
    extra = 0

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("media", "media__media_type")

    @admin.display(description="Size")
    def format_size(self, avm_obj):
        return filesizeformat(avm_obj.media.size)


@admin.register(AssetVersion)
class AssetVersionAdmin(ReadOnlyModelAdmin):
    """Read-only admin for AssetVersion."""

    list_display = ["asset", "version_num", "uuid", "created"]
    fields = ["asset", "uuid", "title", "version_num", "created"]
    readonly_fields = fields  # type: ignore[assignment]
    inlines = [AssetVersionMediaInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "asset",
            "asset__publishable_entity",
            "publishable_entity_version",
        )


class AssetBundleVersionInline(admin.TabularInline):
    """Inline view of AssetBundleVersions from the AssetBundle admin."""

    model = AssetBundleVersion
    fields = ["version_num", "title", "uuid", "created"]
    readonly_fields = fields  # type: ignore[assignment]
    extra = 0

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("publishable_entity_version")


@admin.register(AssetBundle)
class AssetBundleAdmin(ReadOnlyModelAdmin):
    """Read-only admin for AssetBundle."""

    list_display = ("bundle_code", "asset_bundle_type", "uuid", "created")
    readonly_fields = ["learning_package", "asset_bundle_type", "bundle_code", "uuid", "created"]
    list_filter = ("asset_bundle_type", "learning_package")
    search_fields = ["bundle_code", "publishable_entity__uuid", "publishable_entity__entity_ref"]
    inlines = [AssetBundleVersionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "asset_bundle_type",
            "publishable_entity",
            "publishable_entity__learning_package",
        )


class AssetBundleVersionAssetInline(admin.TabularInline):
    """Inline view of the member Assets of an AssetBundleVersion."""

    model = AssetBundleVersion.assets.through
    fields = ["asset_link"]
    readonly_fields = fields  # type: ignore[assignment]
    extra = 0

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("asset", "asset__asset_type")

    @admin.display(description="Asset")
    def asset_link(self, abva_obj):
        return model_detail_link(abva_obj.asset, str(abva_obj.asset))


@admin.register(AssetBundleVersion)
class AssetBundleVersionAdmin(ReadOnlyModelAdmin):
    """Read-only admin for AssetBundleVersion."""

    list_display = ["asset_bundle", "version_num", "uuid", "created"]
    fields = ["asset_bundle", "uuid", "title", "version_num", "created"]
    readonly_fields = fields  # type: ignore[assignment]
    inlines = [AssetBundleVersionAssetInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "asset_bundle",
            "asset_bundle__publishable_entity",
            "publishable_entity_version",
        )
