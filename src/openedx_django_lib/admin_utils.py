"""
Convenience utilities for the Django Admin.
"""
from django.contrib import admin
from django.db import models
from django.db.models.fields.reverse_related import OneToOneRel
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeText


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """
    ModelAdmin subclass that removes any editing ability.

    The Django Admin is really useful for quickly examining model data. At the
    same time, model creation and updates follow specific rules that are meant
    to be enforced above the model layer (in api.py files), so making edits in
    the Django Admin is potentially dangerous.

    In general, if you're providing Django Admin interfaces for your
    openedx-learning related app data models, you should subclass this class
    instead of subclassing admin.ModelAdmin directly.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def one_to_one_related_model_html(model_obj: models.Model) -> SafeText:
    """
    HTML for clickable list of a models that are 1:1-related to ``model_obj``.

    Our design pattern encourages people to hang models off of our lower-level
    core lib models. For example, Component has a OneToOneField that references
    PublishableEntity. It would be really convenient to have PublishableEntity's
    admin page display the link to Component, but the ``publishable`` app is
    intended to be a lower-level app than ``components`` and isn't supposed to
    be aware of it. The same situation occurs for third-party apps that might
    want to extend Component.

    So instead of creating a circular dependency by having ``publishing``
    referencing ``components``, we use Django model introspection to iterate
    over all models that have a OneToOneField to the passe din``model_obj``.
    This allows us to preserve our dependency boundaries within openedx-learning
    and accomodate any third party apps that might further extend these models.

    This will output a list with one entry for each related field.

    * If the field's value is None, we output f"{field_name}: -"
    * If the field has a value but no "change" admin page, we output the string
      representation of the model obj referenced by that field, i.e.
      f{"field_name: {related_model_obj}"}.
    * If the field has a value and an admin page, we output the same as above,
      but we make the related model object's string representation a link to its
      "change" admin page.
    """
    one_to_one_field_names = [
        field.name
        for field in model_obj._meta.related_objects
        if isinstance(field, OneToOneRel)
    ]
    text = []
    for field_name in one_to_one_field_names:
        related_model_obj = getattr(model_obj, field_name, None)

        # No instance of the related model was found, so just use "-"
        if related_model_obj is None:
            text.append(f"{field_name}: -")
            continue

        app_label = related_model_obj._meta.app_label
        model_name = related_model_obj._meta.model_name
        try:
            details_url = reverse(
                f"admin:{app_label}_{model_name}_change",
                args=(related_model_obj.pk,)
            )
        except NoReverseMatch:
            # No Admin URL available, so just put the str representation of the
            # related model instance.
            text.append(f"{field_name}: {related_model_obj}")
            continue

        # If we go this far, there is a related model instance and it has a
        # "change" admin page (even though it's probably read-only via
        # permissions).
        html = format_html(
            '{}: <a href="{}">{}</a>',
            field_name,
            details_url,
            related_model_obj,
        )
        text.append(html)

    return format_html_join("\n", "<li>{}</li>", ((t,) for t in text))


def model_detail_link(obj: models.Model, link_text: str) -> SafeText:
    """
    Render an HTML link to the admin focus page for `obj`.
    """
    return format_html(
        '<a href="{}">{}</a>',
        reverse(
            f"admin:{obj._meta.app_label}_{(obj._meta.model_name or obj.__class__.__name__).lower()}_change",
            args=(obj.pk,),
        ),
        link_text,
    )
