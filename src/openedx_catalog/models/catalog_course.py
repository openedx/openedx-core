"""
CatalogCourse model
"""

import logging

from django.conf import settings
from django.contrib import admin
from django.db import models
from django.db.models.functions import Length
from django.db.models.lookups import Regex
from django.utils.translation import gettext_lazy as _
from organizations.models import Organization

from openedx_django_lib.fields import case_insensitive_char_field, case_sensitive_char_field
from openedx_django_lib.validators import validate_utc_datetime

log = logging.getLogger(__name__)


def get_default_language_code() -> str:
    return settings.LANGUAGE_CODE


# Make 'length' available for CHECK constraints. OK if this is called multiple times.
models.CharField.register_lookup(Length)


class CatalogCourse(models.Model):
    """
    A **catalog course** is a set of course runs.

    For example, the "Math 100" catalog course is comprised of one or more
    "course runs" (e.g. "Math 100 2025Fall", "Math 100 2026Spring").

    Note that most parts of the Open edX system only need to know/care about
    course runs, not catalog courses, but this package nevertheless provides the
    CatalogCourse model as a foundation for the few use cases that require it.

    ⚠️ `CatalogCourse`s and `CourseRun`s may exist as marketing/enrollment
    placeholders for courses that do not yet otherwise exist (do not yet have
    content). So in general, you should not assume that any related models /
    content exist just because the `CatalogCourse` or `CourseRun` exists.

    A `CatalogCourse` may exist without any `CourseRun`s, but a `CourseRun`
    cannot exist without a `CatalogCourse`, and this is enforced by the
    database.

    🔍 This model is intentionally as minimal as possible. For anything else you
    may want to do that involves new fields, please add them via a related model
    in your own app, and make a `ForeignKey` or `OneToOneField` relationship to
    this model. (Unless you really think it's something core that all catalog
    courses in all instances of Open edX will need.)
    """

    id = models.BigAutoField(
        primary_key=True,
        verbose_name=_("Primary Key"),
        help_text=_("The internal database ID for this catalog course. Should not be exposed to users nor in APIs."),
        editable=False,
    )
    org = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=False,
        # Initially, I had to_field="short_name" here, which has the nice property that we can look up an org's
        # short_name without doing a JOIN. But that also prevents changing the org's short_name, which could be
        # necessary to fix capitalization problems. (We wouldn't want to allow other changes to an org's short_name
        # though; only fixing capitalization.)
    )
    course_code = case_insensitive_char_field(
        max_length=255,
        blank=False,
        null=False,
        help_text=_('The course ID, e.g. "Math100".'),
    )
    # When this catalog course was first created. We don't track "modified" as this model should be basically immutable,
    # and the only field that may ever change is "display_name"; we also don't want people to think that the "modified"
    # time reflects when other related models/data was updated.
    created = models.DateTimeField(
        auto_now_add=True,
        validators=[validate_utc_datetime],
        editable=False,
    )
    # Note: display_name should never be blank. But we previously didn't store a name for catalog courses in the core.
    # For backfilling, if there is only one run, we use that run's name as the catalog course name. Otherwise, we can
    # use the org + course code as the display name.
    display_name = case_insensitive_char_field(
        max_length=255,
        blank=False,
        help_text=_(
            'The full name of this catalog course. e.g. "Introduction to Calculus". '
            'Individual course runs may override this, e.g. "Into to Calc (Fall 2026 with Dr. Newton)".'
        ),
    )
    language = case_sensitive_char_field(  # Case sensitive but constraints force it to be lowercase.
        max_length=64,
        blank=False,
        null=False,
        default=get_default_language_code,
        help_text=_(
            "The code representing the language of this catalog course's content. "
            "The first two digits must be the lowercase ISO 639-1 language code. "
            'e.g. "en", "es", "en-us", "pt-br". '
        ),
    )

    # 🛑 Avoid adding additional fields here. The core Open edX platform doesn't do much with catalog courses, so
    #    we don't need "description", "visibility", "prereqs", or anything else. If you want to use such fields and
    #    expose them to users, you'll need to supplement this with additional data/models as mentioned in the docstring.

    @property
    @admin.display(ordering="org__short_name")
    def org_code(self) -> str:
        """
        Get the org code (Organization short_name) of this course, e.g. "MITx"

        ⚠️ This is the org's canonical short_name and may differ in case from
        the actual org code used in the course ID's of the runs of this course,
        especially with older catalog courses.
        """
        return self.org.short_name

    @org_code.setter
    def org_code(self, org_code: str) -> None:
        """
        Convenience method to set the related organization using its short_name.
        """
        # We don't org the Organizations API `get_organization_by_short_name`
        # method because it filters for only active organizations.
        # To support historical data, backfilling, etc., we need to allow inactive orgs here.
        self.org = Organization.objects.get(short_name__iexact=org_code)
        # Note: on SQLite, it's possible for multiple orgs with the same short_name to exist,
        # and the above query could raise `Organization.MultipleObjectsReturned`, but it's
        # not possible on MySQL, using the default collation used by the Organizations app.
        # So we do not worry about that possibility here.

    def save(self, *args, **kwargs):
        """Save the model, with defaults and validation."""
        # Set a default value for display_name:
        if not self.display_name:
            self.display_name = self.course_code
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.display_name} ({self.org_code} {self.course_code})"

    class Meta:
        verbose_name = _("Catalog Course")
        verbose_name_plural = _("Catalog Courses")
        ordering = ("-created",)
        constraints = [
            models.UniqueConstraint(
                fields=["org", "course_code"],
                name="oex_catalog_catalog_course_org_code_pair_uniq",
            ),
            # Enforce at the DB level that these required fields are not blank:
            models.CheckConstraint(
                condition=models.Q(course_code__length__gt=0), name="oex_catalog_catalogcourse_course_code_not_blank"
            ),
            models.CheckConstraint(
                condition=models.Q(display_name__length__gt=0), name="oex_catalog_catalogcourse_display_name_not_blank"
            ),
            # Language code must be lowercase, and locale codes separated by "-" (django convention) not "_"
            models.CheckConstraint(
                condition=Regex(models.F("language"), r"^[a-z][a-z]((\-|@)[a-z]+)?$"),
                name="oex_catalog_catalogcourse_language_regex",
                violation_error_message=_(
                    'The language code must be lowercase, e.g. "en". If a country/locale code is provided, '
                    'it must be separated by a hyphen or @ sign, e.g. "en-us", "zh-hk", or "ca@valencia". '
                ),
            ),
        ]
