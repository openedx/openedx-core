"""
PathwayCategory model
"""

import logging
from typing import NewType

from django.db import models
from django.db.models.functions import Length, Lower
from django.db.models.lookups import Regex
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from openedx_django_lib.fields import (
    TypedBigAutoField,
    case_insensitive_char_field,
    case_sensitive_char_field,
    code_field,
    code_field_check,
)

log = logging.getLogger(__name__)

# Make 'length' available for CHECK constraints. OK if this is called multiple times.
models.CharField.register_lookup(Length)

DEFAULT_PATHWAY_CATEGORY_CODE = "pathway"
DEFAULT_PATHWAY_CATEGORY_NAME = "Pathway"


class PathwayCategory(models.Model):
    """
    A student-facing label for a kind of Pathway.

    Learners are shown the category ("Master's Degree", "Annual Training") rather than the word "Pathway". In authoring
    contexts - Studio, Django admin, code, docs - the terminology stays "Pathway", with the category shown explicitly;
    relabelling is a learner-facing concern of the catalog side only.

    The ``category_code`` is the stable identifier that code and imports may key off. The ``name`` is what learners see,
    and operators are free to change it - including on the default category shipped by the initial migration.

    ``name`` is in the instance's default language. Operators can add names in other languages as
    :class:`PathwayCategoryTranslation` rows, and :attr:`localized_name` is what to show a learner: the name in the
    active language, falling back to ``name``.

    .. no_pii:
    """

    PathwayCategoryID = NewType("PathwayCategoryID", int)
    type ID = PathwayCategoryID

    class IDField(TypedBigAutoField[ID]):  # Boilerplate for fully-typed ID field.
        pass

    id = IDField(
        primary_key=True,
        verbose_name=_("Primary Key"),
        help_text=_("The internal database ID for this pathway category. Should not be exposed to users nor in APIs."),
        editable=False,
    )
    category_code = code_field(
        unicode=False,
        help_text=_('A stable slug identifying this category, e.g. "masters-degree". Not shown to learners.'),
    )
    name = case_insensitive_char_field(
        max_length=255,
        blank=False,
        help_text=_('The learner-facing name of this category, e.g. "Master\'s Degree". Operators may change this.'),
    )

    @property
    def localized_name(self) -> str:
        """
        The learner-facing name in the active language, e.g. the language of the current request.

        See `get_localized_name` for how the name is picked.
        """
        return self.get_localized_name()

    def get_localized_name(self, language_code: str | None = None) -> str:
        """
        Get the learner-facing name in ``language_code``, by default the active language.

        Looks for a translation in exactly that language ("pt-br"), then in its base language ("pt"), and falls back to
        ``name``, which is in the instance's default language; so does having no active language at all. Language codes
        are compared in Django's format (lowercase, hyphenated), so "pt_BR" works too.

        This reads ``translations.all()``, which costs a query unless the translations were prefetched. The
        ``openedx_catalog.api`` functions that return catalog pathways prefetch them.
        """
        if language_code is None:
            language_code = get_language()
        if not language_code:
            return str(self.name)

        language_code = language_code.lower().replace("_", "-")
        names = {row.language_code: row.name for row in self.translations.all()}
        base_language_code = language_code.split("-", 1)[0]
        return names.get(language_code) or names.get(base_language_code) or str(self.name)

    def __str__(self) -> str:
        return str(self.name)

    class Meta:
        verbose_name = _("Pathway Category")
        verbose_name_plural = _("Pathway Categories")
        ordering = ("name",)
        constraints = [
            # The category_code must be case-insensitively unique:
            models.UniqueConstraint(Lower("category_code"), name="oex_catalog_pathwaycategory_code_uniq_ci"),
            code_field_check("category_code", name="oex_catalog_pathwaycategory_code_regex", unicode=False),
            # Enforce at the DB level that this required field is not blank:
            models.CheckConstraint(
                condition=models.Q(name__length__gt=0), name="oex_catalog_pathwaycategory_name_not_blank"
            ),
        ]


class PathwayCategoryTranslation(models.Model):
    """
    The learner-facing name of a :class:`PathwayCategory` in one language other than the instance's default.

    Managed in the Django admin, like the categories themselves. A category needs no translations at all: learners
    whose language has none see its ``name``.

    .. no_pii:
    """

    PathwayCategoryTranslationID = NewType("PathwayCategoryTranslationID", int)
    type ID = PathwayCategoryTranslationID

    class IDField(TypedBigAutoField[ID]):  # Boilerplate for fully-typed ID field.
        pass

    id = IDField(
        primary_key=True,
        verbose_name=_("Primary Key"),
        help_text=_("The internal database ID for this translation. Should not be exposed to users nor in APIs."),
        editable=False,
    )
    pathway_category = models.ForeignKey(
        PathwayCategory,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = case_sensitive_char_field(  # Case sensitive, but the constraints force it to be lowercase.
        max_length=64,
        blank=False,
        help_text=_(
            "The language of this name, as in the LANGUAGES setting: a lowercase ISO 639-1 code, optionally followed "
            'by a hyphen and a country/locale code, e.g. "fr", "pt-br", "zh-cn".'
        ),
    )
    name = case_insensitive_char_field(
        max_length=255,
        blank=False,
        help_text=_("The learner-facing name of the category in this language."),
    )

    def clean(self) -> None:
        """Normalize the language code when edited via Django admin, e.g. "pt_BR" to "pt-br"."""
        self.language_code = self.language_code.strip().lower().replace("_", "-")

    def __str__(self) -> str:
        return f"{self.name} ({self.language_code})"

    class Meta:
        verbose_name = _("Pathway Category Translation")
        verbose_name_plural = _("Pathway Category Translations")
        ordering = ("language_code",)
        constraints = [
            # One name per language; otherwise which one a learner sees would be arbitrary.
            models.UniqueConstraint(
                fields=["pathway_category", "language_code"],
                name="oex_catalog_pathwaycategorytranslation_uniq_lang",
            ),
            # Same format as CatalogCourse.language, so that codes compare equal to Django's (e.g. get_language()).
            models.CheckConstraint(
                condition=Regex(models.F("language_code"), r"^[a-z][a-z](\-[a-z0-9]+)*$"),
                name="oex_catalog_pathwaycategorytranslation_lang_regex",
                violation_error_message=_(
                    'The language code must be lowercase, e.g. "fr". If a country/locale code is provided, '
                    'it must be separated by a hyphen, e.g. "pt-br", "zh-cn".'
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(name__length__gt=0),
                name="oex_catalog_pathwaycategorytranslation_name_not_blank",
            ),
        ]


def get_default_pathway_category() -> PathwayCategory:
    """
    Get the default `PathwayCategory`, creating it if it doesn't exist.

    Every `CatalogPathway` must have a category. Rather than falling back to the word "Pathway" in code, we ship a
    database row with that name, so that operators can rename it or add categories of their own without a code change.
    See the openedx_learning ADR 0007.
    """
    category, _created = PathwayCategory.objects.get_or_create(
        category_code=DEFAULT_PATHWAY_CATEGORY_CODE,
        defaults={"name": DEFAULT_PATHWAY_CATEGORY_NAME},
    )
    return category


def get_default_pathway_category_id() -> PathwayCategory.ID:
    """
    Get the ID of the default `PathwayCategory`, creating it if it doesn't exist.

    Note: this function is used as a field default and is therefore referenced from migrations, so update those
    migrations if moving it or changing its signature.
    """
    return get_default_pathway_category().id
