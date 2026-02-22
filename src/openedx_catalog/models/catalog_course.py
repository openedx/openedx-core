"""
CatalogCourse model
"""

import logging

from django.conf import settings
from django.contrib import admin
from django.db import models
from django.db.models.functions import Length, Lower
from django.db.models.lookups import Regex
from django.utils.translation import gettext_lazy as _
from organizations.models import Organization  # type: ignore[import]

from openedx_django_lib.fields import case_insensitive_char_field, case_sensitive_char_field
from openedx_django_lib.validators import validate_utc_datetime

log = logging.getLogger(__name__)


def get_default_language_code() -> str:
    """
    Default language code used for CatalogCourse.language

    Note: this function is used in migration 0001 so update that migration if
    moving it or changing its signature.
    """
    return settings.LANGUAGE_CODE  # e.g. "en-us", "fr-ca"


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
        # though; only fixing capitalization - see openedx_catalog.signals.verify_organization_change.)
    )
    course_code = case_sensitive_char_field(
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
    # Note: language codes used on the Open edX platform are inconsistent.
    # See https://github.com/openedx/openedx-platform/issues/38036
    # For this model going forward, we normalized them to match settings.LANGUAGES (en, fr-ca, zh-cn, zh-hk) but for
    # backwards compatibility, you can get/set the language_short field which uses the mostly two-letter values from
    # the platform's ALL_LANGUAGES setting (en, fr, es, zh_HANS, zh_HANT).
    language = case_sensitive_char_field(  # Case sensitive but constraints force it to be lowercase.
        max_length=64,
        blank=False,
        null=False,
        default=get_default_language_code,
        help_text=_(
            "The code representing the language of this catalog course's content. "
            "The first two digits must be the lowercase ISO 639-1 language code, "
            "optionally followed by a country/locale code. "
            'e.g. "en", "es", "fr-ca", "pt-br", "zh-cn", "zh-hk". '
        ),
    )

    # 🛑 Avoid adding additional fields here. The core Open edX platform doesn't do much with catalog courses, so
    #    we don't need "description", "visibility", "prereqs", or anything else. If you want to use such fields and
    #    expose them to users, you'll need to supplement this with additional data/models as mentioned in the docstring.

    @property
    def language_short(self) -> str:
        """
        Get the language code used by this catalog course, without locale.
        This is always a two-digit code, except for Mandarin and Cantonese.
        (This should be a value from settings.ALL_LANGUAGES, and should match
         the CourseOverview.language field.)
        """
        if self.language == "zh-cn":  # Chinese (Mainland China)
            return "zh_HANS"  # Mandarin / Simplified
        elif self.language == "zh-hk":  # Chinese (Hong Kong)
            return "zh_HANT"  # Cantonese / Traditional
        return self.language[:2]  # Strip locale

    @language_short.setter
    def language_short(self, legacy_code: str) -> None:
        """
        Set the language code used by this catalog course, without locale.
        This is always a two-digit code, except for Mandarin and Cantonese.
        (This should be a value from settings.ALL_LANGUAGES, and should match
         the CourseOverview.language field.)
        """
        if hasattr(settings, "ALL_LANGUAGES"):
            assert legacy_code in [code for (code, _name) in settings.ALL_LANGUAGES]  # type: ignore
        if legacy_code == "zh_HANS":  # Mandarin / Simplified
            self.language = "zh-cn"  # Chinese (Mainland China)
        elif legacy_code == "zh_HANT":  # Cantonese / Traditional
            self.language = "zh-hk"  # Chinese (Hong Kong)
        else:
            self.language = legacy_code

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

    @property
    def url_slug(self):  # Do we need this? Would an opaque key be better?
        """
        An ID that can be used to identify this catalog course in URLs or APIs.
        In the future, this may be an editable SlugField, so don't assume that
        it never changes.
        """
        # '+' is a bad separator because it can mean " " in URLs.
        # '-', '.', and '_' cannot be used since they're allowed in the org code
        # So for now we use ':', and in the future we may make the whole slug customizable.
        return f"{self.org_code}:{self.course_code}"

    def clean(self):
        """Validate/normalize fields when edited via Django admin"""
        # Set a default value for display_name:
        if not self.display_name:
            self.display_name = self.course_code
        # Normalize language codes to match settings.LANGUAGES.
        # It's safe to assume language is lowercase here, because if it's not the DB will reject its CHECK constraint.
        if self.language == "zh-hans":
            self.language = "zh-cn"
        if self.language == "zh-hant":
            self.language = "zh-hk"

    def save(self, *args, **kwargs):
        """Save the model, with some defaults and validation."""
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.display_name} ({self.org_code} {self.course_code})"

    class Meta:
        verbose_name = _("Catalog Course")
        verbose_name_plural = _("Catalog Courses")
        ordering = ("-created",)
        indexes = [
            # We need fast lookups by (org, course_code) pairs. We generally want this lookup to be case sensitive.
            models.Index(fields=["org", "course_code"]),
        ]
        constraints = [
            # The course_course must be case-insensitively unique per org:
            models.UniqueConstraint("org", Lower("course_code"), name="oex_catalog_catalogcourse_org_code_uniq_ci"),
            # Enforce at the DB level that these required fields are not blank:
            models.CheckConstraint(
                condition=models.Q(course_code__length__gt=0), name="oex_catalog_catalogcourse_course_code_not_blank"
            ),
            models.CheckConstraint(
                condition=models.Q(display_name__length__gt=0), name="oex_catalog_catalogcourse_display_name_not_blank"
            ),
            # Language code must be lowercase, and locale codes separated by "-" (django convention) not "_"
            models.CheckConstraint(
                condition=Regex(models.F("language"), r"^[a-z][a-z](\-[a-z0-9]+)*$"),
                name="oex_catalog_catalogcourse_language_regex",
                violation_error_message=_(
                    'The language code must be lowercase, e.g. "en". If a country/locale code is provided, '
                    'it must be separated by a hyphen, e.g. "en-us", "zh-hk". '
                ),
            ),
        ]
