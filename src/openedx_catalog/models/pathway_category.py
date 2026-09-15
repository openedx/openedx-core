"""
PathwayCategory model
"""

import logging
from typing import NewType

from django.db import models
from django.db.models.functions import Length, Lower
from django.utils.translation import gettext_lazy as _

from openedx_django_lib.fields import TypedBigAutoField, case_insensitive_char_field, code_field, code_field_check

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
