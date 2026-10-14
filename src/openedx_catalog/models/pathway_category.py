"""
PathwayCategory model
"""

import logging
from typing import NewType

from django.db import models
from django.db.models.functions import Length, Lower
from django.utils.translation import gettext_lazy as _

from openedx_django_lib.fields import TypedAutoField, case_insensitive_char_field, code_field, code_field_check

log = logging.getLogger(__name__)

# Make 'length' available for CHECK constraints. OK if this is called multiple times.
models.CharField.register_lookup(Length)


class PathwayCategory(models.Model):
    """
    A student-facing label for a kind of Pathway.

    Learners are shown the category ("Master's Degree", "Annual Training") rather than the word "Pathway". In authoring
    contexts - Studio, Django admin, code, docs - the terminology stays "Pathway", with the category shown explicitly;
    relabelling is a learner-facing concern of the catalog side only.

    The ``category_code`` is the stable identifier that code and imports may key off. The ``title`` is what learners
    see, and operators are free to change it.

    Where learners see several pathways of a category together ("3 Master's Degrees"), use `get_title_plural`.

    .. no_pii:
    """

    PathwayCategoryID = NewType("PathwayCategoryID", int)
    type ID = PathwayCategoryID

    # A 32-bit key is plenty: an instance has a handful of categories, not millions.
    class IDField(TypedAutoField[ID]):  # Boilerplate for fully-typed ID field.
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
    title = case_insensitive_char_field(
        max_length=255,
        blank=False,
        help_text=_('The learner-facing title of this category, e.g. "Master\'s Degree". Operators may change this.'),
    )
    title_plural = case_insensitive_char_field(
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            'The plural of the title. Leave blank to use the title with an "s" appended, which suits most titles; '
            'set it for titles that don\'t pluralize that way, e.g. "Classes" for "Class".'
        ),
    )

    def get_title_plural(self) -> str:
        """
        Get the plural of the title: ``title_plural`` if set, and otherwise ``title`` with an "s" appended.
        """
        return self.title_plural or f"{self.title}s"

    def __str__(self) -> str:
        return self.title

    class Meta:
        verbose_name = _("Pathway Category")
        verbose_name_plural = _("Pathway Categories")
        ordering = ("title",)
        constraints = [
            # The category_code must be case-insensitively unique:
            models.UniqueConstraint(Lower("category_code"), name="oex_catalog_pathwaycategory_code_uniq_ci"),
            code_field_check("category_code", name="oex_catalog_pathwaycategory_code_regex", unicode=False),
            # Enforce at the DB level that this required field is not blank:
            models.CheckConstraint(
                condition=models.Q(title__length__gt=0), name="oex_catalog_pathwaycategory_title_not_blank"
            ),
        ]
