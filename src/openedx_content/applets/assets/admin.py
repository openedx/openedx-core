"""
Django admin for ``assets`` models
"""

from django.contrib import admin
from django.template.defaultfilters import filesizeformat

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin

from .models import Asset, AssetVersion


@admin.register(Asset)
class AssetAdmin(ReadOnlyModelAdmin):
    """
    Django admin for Asset model
    """

    list_display = ("id", "path", "learning_package", "permissions", "allow_scripts", "created", "created_by")
    list_filter = ("learning_package",)
    search_fields = ("path",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "learning_package",
            "publishable_entity",
            "publishable_entity__created_by",
        )


@admin.register(AssetVersion)
class AssetVersionAdmin(ReadOnlyModelAdmin):
    """
    Django admin for AssetVersion model
    """

    list_display = ("asset", "version_num", "title", "media_type", "format_size", "created", "created_by")
    list_filter = ("asset__learning_package",)
    search_fields = ("asset__path", "publishable_entity_version__title")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "asset",
            "publishable_entity_version",
            "publishable_entity_version__created_by",
            "media",
            "media__media_type",
        )

    @admin.display(description="Media type")
    def media_type(self, av_obj: AssetVersion) -> str:
        return str(av_obj.media.media_type)

    @admin.display(description="Size")
    def format_size(self, av_obj: AssetVersion) -> str:
        return filesizeformat(av_obj.media.size)
