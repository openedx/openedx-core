"""
Tagging app system-defined taxonomies data models
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from openedx_tagging.models.base import Tag

from .base import Tag, Taxonomy

log = logging.getLogger(__name__)


class SystemDefinedTaxonomy(Taxonomy):
    """
    Simple subclass of Taxonomy which requires the system_defined flag to be set.
    """

    class Meta:
        proxy = True

    @property
    def system_defined(self) -> bool:
        """
        Indicates that tags and metadata for this taxonomy are maintained by the system;
        taxonomy admins will not be permitted to modify them.
        """
        return True


class ModelSystemDefinedTaxonomy(SystemDefinedTaxonomy):
    """
    Model based system taxonomy abstract class.

    This type of taxonomy has an associated Django model in
    ModelSystemDefinedTaxonomy.tag_class_model.

    They are designed to create Tags when required for new ObjectTags, to
    maintain their status as "closed" taxonomies.

    The Tags are representations of the instances of the associated model.

    Tag.external_id stores an identifier from the instance (`pk` as default)
    and Tag.value stores a human readable representation of the instance
    (e.g. `username`).
    The subclasses can override this behavior, to choose the right field.

    When an ObjectTag is created with an existing Tag,
    the Tag is re-synchronized with its instance.
    """

    class Meta:
        proxy = True

    @property
    def tag_class_model(self) -> type[models.Model]:
        """
        Define what Django model this taxonomy is associated with
        """
        raise NotImplementedError

    @property
    def tag_class_value_field(self) -> str:
        """
        The name of the tag_class_model field to use as the Tag.value when creating Tags for this taxonomy.

        Subclasses may override this method to use different fields.
        """
        raise NotImplementedError

    @property
    def tag_class_key_field(self) -> str:
        """
        The name of the tag_class_model field to use as the Tag.external_id when creating Tags for this taxonomy.

        This must be an immutable ID.
        """
        return "pk"

    def validate_value(self, value: str):
        """
        Check if 'value' is part of this Taxonomy, based on the specified model.
        """
        try:
            # See https://github.com/typeddjango/django-stubs/issues/1684 for why we need to ignore this.
            self.tag_class_model.objects.get(  # type: ignore[attr-defined]
                **{f"{self.tag_class_value_field}__iexact": value}
            )
            return True
        except ObjectDoesNotExist:
            return False

    def tag_for_value(self, value: str):
        """
        Get the Tag object for the given value.
        """
        try:
            # First we look up the instance by value.
            # We specify 'iexact' but whether it's case sensitive or not on MySQL depends on the model's collation.
            # See https://github.com/typeddjango/django-stubs/issues/1684 for why we need to ignore this.
            instance = self.tag_class_model.objects.get(  # type: ignore[attr-defined]
                **{f"{self.tag_class_value_field}__iexact": value}
            )
        except ObjectDoesNotExist as exc:
            raise Tag.DoesNotExist from exc
        # Use the canonical value from here on (possibly with different case from the value given as a parameter)
        value = getattr(instance, self.tag_class_value_field)
        # We assume the value may change but the external_id is immutable.
        # So look up keys using external_id. There may be a key with the same external_id but an out of date value.
        external_id = str(getattr(instance, self.tag_class_key_field))
        tag, _created = self.tag_set.get_or_create(external_id=external_id, defaults={"value": value})
        if tag.value != value:
            # Update the Tag to reflect the new cached 'value'
            tag.value = value
            tag.save()
        return tag

    def validate_external_id(self, external_id: str):
        """
        Check if 'external_id' is part of this Taxonomy.
        """
        try:
            # See https://github.com/typeddjango/django-stubs/issues/1684 for why we need to ignore this.
            self.tag_class_model.objects.get(  # type: ignore[attr-defined]
                **{f"{self.tag_class_key_field}__iexact": external_id}
            )
            return True
        except ObjectDoesNotExist:
            return False

    def tag_for_external_id(self, external_id: str):
        """
        Get the Tag object for the given external_id.
        Some Taxonomies may auto-create the Tag at this point, e.g. a User
        Taxonomy will create User Tags "just in time".

        Will raise Tag.DoesNotExist if the tag is not valid for this taxonomy.
        """
        try:
            # First we look up the instance by external_id
            # We specify 'iexact' but whether it's case sensitive or not on MySQL depends on the model's collation.
            # See https://github.com/typeddjango/django-stubs/issues/1684 for why we need to ignore this.
            instance = self.tag_class_model.objects.get(  # type: ignore[attr-defined]
                **{f"{self.tag_class_key_field}__iexact": external_id}
            )
        except ObjectDoesNotExist as exc:
            raise Tag.DoesNotExist from exc
        value = getattr(instance, self.tag_class_value_field)
        # Use the canonical external_id from here on (may differ in capitalization)
        external_id = getattr(instance, self.tag_class_key_field)
        tag, _created = self.tag_set.get_or_create(external_id=external_id, defaults={"value": value})
        if tag.value != value:
            # Update the Tag to reflect the new cached 'value'
            tag.value = value
            tag.save()
        return tag


class UserSystemDefinedTaxonomy(ModelSystemDefinedTaxonomy):
    """
    A Taxonomy that allows tagging objects using users.
    """

    class Meta:
        proxy = True

    @property
    def tag_class_model(self) -> type[models.Model]:
        """
        Define what Django model this taxonomy is associated with
        """
        return get_user_model()

    @property
    def tag_class_value_field(self) -> str:
        """
        Returns the name of the tag_class_model field to use as the Tag.value when creating Tags for this taxonomy.

        Subclasses may override this method to use different fields.
        """
        return "username"


class LanguageTaxonomy(SystemDefinedTaxonomy):
    """
    Language System-defined taxonomy

    The tags are filtered and validated taking into account the
    languages available in Django LANGUAGES settings var
    """

    class Meta:
        proxy = True

    def validate_value(self, value: str):
        """
        Check if 'value' is part of this Taxonomy, based on the specified model.
        """
        for _, lang_name in settings.LANGUAGES:
            if lang_name == value:
                return True
        return False

    def tag_for_value(self, value: str):
        """
        Get the Tag object for the given value.
        """
        for lang_code, lang_name in settings.LANGUAGES:
            if lang_name == value:
                return self.tag_for_external_id(lang_code)
        raise Tag.DoesNotExist

    def validate_external_id(self, external_id: str):
        """
        Check if 'external_id' is part of this Taxonomy.
        """
        lang_code = external_id.lower()
        # Get settings.LANGUAGES (a list of tuples) as a dict. In LMS/CMS this is already cached as LANGUAGE_DICT
        languages_as_dict = getattr(settings, "LANGUAGE_DICT", dict(settings.LANGUAGES))
        return lang_code in languages_as_dict

    def tag_for_external_id(self, external_id: str):
        """
        Get the Tag object for the given external_id.
        Some Taxonomies may auto-create the Tag at this point, e.g. a User
        Taxonomy will create User Tags "just in time".

        Will raise Tag.DoesNotExist if the tag is not valid for this taxonomy.
        """
        lang_code = external_id.lower()
        # Get settings.LANGUAGES (a list of tuples) as a dict. In LMS/CMS this is already cached as LANGUAGE_DICT
        languages_as_dict = getattr(settings, "LANGUAGE_DICT", dict(settings.LANGUAGES))
        try:
            lang_name = languages_as_dict[lang_code]
        except KeyError as exc:
            raise Tag.DoesNotExist from exc
        tag, _created = self.tag_set.get_or_create(external_id=lang_code, defaults={"value": lang_name})
        if tag.value != lang_name:
            # Update the Tag to reflect the new language name
            tag.value = lang_name
            tag.save()
        return tag
