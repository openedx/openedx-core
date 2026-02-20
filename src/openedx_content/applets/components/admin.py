"""
Django admin for components models
"""
import base64

from django.contrib import admin
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeText

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin

from .models import Component, ComponentVersion, ComponentVersionMedia


class ComponentVersionInline(admin.TabularInline):
    """
    Inline admin view of ComponentVersion from the Component Admin
    """
    model = ComponentVersion
    fields = ["version_num", "created", "title", "format_uuid"]
    readonly_fields = ["version_num", "created", "title", "uuid", "format_uuid"]
    extra = 0

    @admin.display(description="UUID")
    def format_uuid(self, cv_obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:openedx_content_componentversion_change", args=(cv_obj.pk,)),
            cv_obj.uuid,
        )


@admin.register(Component)
class ComponentAdmin(ReadOnlyModelAdmin):
    """
    Django admin configuration for Component
    """
    list_display = ("key", "uuid", "component_type", "created")
    readonly_fields = [
        "learning_package",
        "uuid",
        "component_type",
        "key",
        "created",
    ]
    list_filter = ("component_type", "learning_package")
    search_fields = ["publishable_entity__uuid", "publishable_entity__key"]
    inlines = [ComponentVersionInline]


class ContentInline(admin.TabularInline):
    """
    Django admin configuration for Content
    """
    model = ComponentVersion.media.through

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "media",
            "media__learning_package",
            "media__media_type",
            "component_version",
            "component_version__publishable_entity_version",
            "component_version__component",
            "component_version__component__publishable_entity",
        )

    fields = [
        "key",
        "format_size",
        "rendered_data",
    ]
    readonly_fields = [
        "media",
        "key",
        "format_size",
        "rendered_data",
    ]
    extra = 0

    def has_file(self, cvm_obj):
        return cvm_obj.media.has_file

    def rendered_data(self, cvm_obj):
        return media_preview(cvm_obj)

    @admin.display(description="Size")
    def format_size(self, cvm_obj):
        return filesizeformat(cvm_obj.media.size)


@admin.register(ComponentVersion)
class ComponentVersionAdmin(ReadOnlyModelAdmin):
    """
    Django admin configuration for ComponentVersion
    """
    readonly_fields = [
        "component",
        "uuid",
        "title",
        "version_num",
        "created",
        "media",
    ]
    fields = [
        "component",
        "uuid",
        "title",
        "version_num",
        "created",
    ]
    list_display = ["component", "version_num", "uuid", "created"]
    inlines = [ContentInline]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "component",
            "component__publishable_entity",
            "publishable_entity_version",
        )


def format_text_for_admin_display(text: str) -> SafeText:
    """
    Get the HTML to display the given plain text (preserving formatting)
    """
    return format_html(
        '<pre style="white-space: pre-wrap;">\n{}\n</pre>',
        text,
    )


def media_preview(cvm_obj: ComponentVersionMedia) -> SafeText:
    """
    Get the HTML to display a preview of the given ComponentVersionMedia
    """
    media_obj = cvm_obj.media

    if media_obj.media_type.type == "image":
        # This base64 encoding looks really goofy and is bad for performance,
        # but image previews in the admin are extremely useful, and this lets us
        # have them without creating a separate view in Open edX Core. (Keep in
        # mind that these assets are private, so they cannot be accessed via the
        # MEDIA_URL like most Django uploaded assets.)
        data = media_obj.read_file().read()
        return format_html(
            '<img src="data:{};base64, {}" style="max-width: 100%;" /><br><pre>{}</pre>',
            media_obj.mime_type,
            base64.encodebytes(data).decode('utf8'),
            media_obj.os_path(),
        )

    return format_text_for_admin_display(
        media_obj.text or ""
    )
