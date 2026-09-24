"""
CatalogPathway model
"""

import logging
from typing import NewType

from django.contrib import admin
from django.db import models
from django.db.models.functions import Length, Lower
from django.utils.translation import gettext_lazy as _
from organizations.models import Organization  # type: ignore[import]

from openedx_django_lib.fields import (
    MultiCollationTextField,
    TypedBigAutoField,
    case_insensitive_char_field,
    code_field,
    code_field_check,
)
from openedx_django_lib.validators import validate_utc_datetime

from .pathway_category import PathwayCategory, get_default_pathway_category_id

log = logging.getLogger(__name__)

# Make 'length' available for CHECK constraints. OK if this is called multiple times.
models.CharField.register_lookup(Length)


class CatalogPathway(models.Model):
    """
    The learner-browsable, enrollable half of a Pathway.

    A Pathway is split in two (see the openedx_learning ADR 0007). This model is the catalog half: the display name, the
    description shown in the catalog, and the `PathwayCategory`. It is **not versioned**, because marketing copy is
    revised frequently and casually and versioning it would be pure overhead.

    The other half - the *definition* of the Pathway, meaning its Items and completion criteria - lives in
    `openedx_learning.applets.pathways` and *is* versioned, so that progress and credentials can be judged against the
    definition that was in effect at the time.

    The link between the two halves lives on the content side (`Pathway.catalog_pathway`, one-to-one), never here: this
    app stays unaware of content, so that changes in how content is represented never reach it (see the openedx_catalog
    ADR 0001, decision 2). A `CatalogPathway` therefore cannot, on its own, tell you which content implements it;
    queries in that direction start from the content side.

    A `CatalogPathway` may exist before any content implements it, in the same way that a `CatalogCourse` may exist as a
    marketing placeholder for a course that has no content yet.

    Like `CatalogCourse`, this model is intentionally minimal. Additional catalog-side fields should generally go in a
    related model in your own app, with a `ForeignKey` or `OneToOneField` to this one.

    .. no_pii:
    """

    CatalogPathwayID = NewType("CatalogPathwayID", int)
    type ID = CatalogPathwayID

    class IDField(TypedBigAutoField[ID]):  # Boilerplate for fully-typed ID field.
        pass

    id = IDField(
        primary_key=True,
        verbose_name=_("Primary Key"),
        help_text=_("The internal database ID for this catalog pathway. Should not be exposed to users nor in APIs."),
        editable=False,
    )
    org = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=False,
        related_name="catalog_pathways",
    )
    pathway_code = code_field(
        unicode=False,
        help_text=_('The pathway code/number, e.g. "DataScience2026".'),
    )
    created = models.DateTimeField(
        auto_now_add=True,
        validators=[validate_utc_datetime],
        editable=False,
    )
    # This reflects edits to the *catalog* fields on this row only (title, category, description). It says nothing about
    # when the pathway's *definition* - its Items and completion criteria - last changed. That happens on the content
    # side and is versioned there; ask `openedx_learning.api` for it.
    modified = models.DateTimeField(
        auto_now=True,
        validators=[validate_utc_datetime],
        editable=False,
        help_text=_("When the catalog fields of this pathway were last edited. Unrelated to its content."),
    )
    title = case_insensitive_char_field(
        max_length=255,
        blank=True,  # Only allowed to be blank temporarily when creating a new instance in the Django admin form.
        help_text=_(
            'The full title (display name) of this pathway, e.g. "Data Science Professional Certificate". '
            "Leave blank to use the pathway code as the title."
        ),
    )
    category = models.ForeignKey(
        PathwayCategory,
        on_delete=models.PROTECT,
        null=False,
        default=get_default_pathway_category_id,
        related_name="pathways",
        help_text=_("The learner-facing kind of pathway this is. Always required; defaults to a category we ship."),
    )
    description = MultiCollationTextField(
        blank=True,
        null=False,
        default="",
        max_length=10_000,
        # We don't expect to sort by this column, but we may want case-insensitive searches over it.
        db_collations={
            "sqlite": "NOCASE",
            "mysql": "utf8mb4_unicode_ci",
        },
        help_text=_("The description shown to learners browsing the catalog."),
    )

    # 🛑 Avoid adding additional fields here. Anything that describes what a learner must *do* belongs on the content
    #    side, where it is versioned. Anything else catalog-related should go in a related model in your own app.

    @property
    @admin.display(ordering="org__short_name")
    def org_code(self) -> str:
        """
        Get the org code (Organization short_name) of this pathway, e.g. "MITx".
        """
        return self.org.short_name

    @org_code.setter
    def org_code(self, org_code: str) -> None:
        """
        Convenience method to set the related organization using its short_name.
        """
        # As with CatalogCourse, we don't use `get_organization_by_short_name` because it filters for active orgs only,
        # and we need to allow inactive orgs to support historical data and backfills.
        self.org = Organization.objects.get(short_name__iexact=org_code)

    @property
    def key_str(self) -> str:
        """
        A string key that can be used to identify this catalog pathway in URLs or APIs.

        As with `CatalogCourse.key_str`, this may become based on an editable `SlugField` or an opaque key in the
        future, so don't assume it never changes.
        """
        return f"catalog-pathway:{self.org_code}:{self.pathway_code}"

    def clean(self) -> None:
        """Validate/normalize fields when edited via Django admin."""
        # Set a default value for title:
        if not self.title:
            self.title = self.pathway_code

    def save(self, *args, **kwargs):
        """Save the model, with some defaults and validation."""
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.title} ({self.org_code} {self.pathway_code})"

    class Meta:
        verbose_name = _("Catalog Pathway")
        verbose_name_plural = _("Catalog Pathways")
        ordering = ("-created",)
        indexes = [
            # We need fast lookups by (org, pathway_code) pairs. We generally want this lookup to be case sensitive.
            models.Index(fields=["org", "pathway_code"]),
        ]
        constraints = [
            # The pathway_code must be case-insensitively unique per org:
            models.UniqueConstraint("org", Lower("pathway_code"), name="oex_catalog_catalogpathway_org_code_uniq_ci"),
            code_field_check("pathway_code", name="oex_catalog_catalogpathway_pathway_code_regex", unicode=False),
            # Enforce at the DB level that these required fields are not blank:
            models.CheckConstraint(
                condition=models.Q(title__length__gt=0), name="oex_catalog_catalogpathway_title_not_blank"
            ),
        ]
