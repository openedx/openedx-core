"""
Django admin for media.models
"""
import base64

from django.contrib import admin
from django.utils.html import format_html

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin

from .models import Media


@admin.register(Media)
class ContentAdmin(ReadOnlyModelAdmin):
    """
    Django admin for Content model
    """
    list_display = [
        "hash_digest",
        "learning_package",
        "media_type",
        "size",
        "created",
        "has_file",
    ]
    fields = [
        "learning_package",
        "hash_digest",
        "media_type",
        "size",
        "created",
        "has_file",
        "path",
        "os_path",
        "text_preview",
        "image_preview",
    ]
    list_filter = ("media_type", "learning_package")
    search_fields = ("hash_digest",)

    @admin.display(description="OS Path")
    def os_path(self, media: Media):
        return media.os_path() or ""

    def path(self, media: Media):
        return media.path if media.has_file else ""

    def text_preview(self, media: Media):
        if not media.text:
            return ""
        return format_html(
            '<pre style="white-space: pre-wrap;">\n{}\n</pre>',
            media.text,
        )

    def image_preview(self, media: Media):
        """
        Return HTML for an image, if that is the underlying Content.

        Otherwise, just return a blank string.
        """
        if media.media_type.type != "image":
            return ""

        data = media.read_file().read()
        return format_html(
            '<img src="data:{};base64, {}" style="max-width: 100%;" />',
            media.mime_type,
            base64.encodebytes(data).decode('utf8'),
        )
