"""
Django admin for ``assets`` models
"""

from django.contrib import admin

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


@admin.register(AssetVersion)
class AssetVersionAdmin(ReadOnlyModelAdmin):
    """
    Django admin for AssetVersion model
    """
