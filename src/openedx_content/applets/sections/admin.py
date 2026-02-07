"""
Django admin for sections models
"""
from django.contrib import admin
from django.utils.safestring import SafeText

from openedx_django_lib.admin_utils import ReadOnlyModelAdmin, model_detail_link

from ..publishing.models import ContainerVersion
from .models import Section, SectionVersion


class SectionVersionInline(admin.TabularInline):
    """
    Minimal table for section versions in a section.

    (Generally, this information is useless, because each SectionVersion should have a
     matching ContainerVersion, shown in much more detail on the Container detail page.
     But we've hit at least one bug where ContainerVersions were being created without
     their connected SectionVersions, so we'll leave this table here for debugging
     at least until we've made the APIs more robust against that sort of data corruption.)
    """
    model = SectionVersion
    fields = ["pk"]
    readonly_fields = ["pk"]
    ordering = ["-pk"]  # Newest first

    def pk(self, obj: ContainerVersion) -> SafeText:
        return obj.pk


@admin.register(Section)
class SectionAdmin(ReadOnlyModelAdmin):
    """
    Very minimal interface... just direct the admin user's attention towards the related Container model admin.
    """
    inlines = [SectionVersionInline]
    list_display = ["pk", "key"]
    fields = ["key"]
    readonly_fields = ["key"]

    def key(self, obj: Section) -> SafeText:
        return model_detail_link(obj.container, obj.key)

    def get_form(self, request, obj=None, change=False, **kwargs):
        help_texts = {'key': f'For more details of this {self.model.__name__}, click above to see its Container view'}
        kwargs.update({'help_texts': help_texts})
        return super().get_form(request, obj, **kwargs)
