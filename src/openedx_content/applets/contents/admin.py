"""
Django admin for contents models
"""
import base64

from django.contrib import admin
from django.utils.html import format_html

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin

from .models import Content


@admin.register(Content)
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
    def os_path(self, content: Content):
        return content.os_path() or ""

    def path(self, content: Content):
        return content.path if content.has_file else ""

    def text_preview(self, content: Content):
        if not content.text:
            return ""
        return format_html(
            '<pre style="white-space: pre-wrap;">\n{}\n</pre>',
            content.text,
        )

    def image_preview(self, content: Content):
        """
        Return HTML for an image, if that is the underlying Content.

        Otherwise, just return a blank string.
        """
        if content.media_type.type != "image":
            return ""

        data = content.read_file().read()
        return format_html(
            '<img src="data:{};base64, {}" style="max-width: 100%;" />',
            content.mime_type,
            base64.encodebytes(data).decode('utf8'),
        )
