"""
CourseRun model
"""

import logging

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Concat, Length, Lower, Right
from django.db.models.lookups import Exact
from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.django.models import CourseKeyField
from opaque_keys.edx.locator import CourseLocator

from openedx_django_lib.fields import case_insensitive_char_field, case_sensitive_char_field
from openedx_django_lib.validators import validate_utc_datetime

from .catalog_course import CatalogCourse

log = logging.getLogger(__name__)

# Make 'length' available for CHECK constraints. OK if this is called multiple times.
models.CharField.register_lookup(Length)


class CourseRun(models.Model):
    """
    The principal canonical model for courses (course runs).

    This is a NEW model (as of 2026). Going forward:
    Anytime you need to reference a course [run] in a django model, you should
    do so by creating a foreign key to this model. However, any APIs or events
    should always identify courses by their full string course ID
    ("course-v1:...") and never expose the integer primary key of this model.

    Note: throughout the system, we often abbreviate "course run" as just
    "course", so the two concepts are usually interchangeable. The _set_ of runs
    of a given course should always be called a "catalog course" in the code to
    avoid ambiguity. That is, a "catalog course" (e.g. "Math 100") is comprised
    of one or more "course runs" (e.g. "Math 100 2025Fall", "Math 100
    2026Spring"), and we generally refer to those course runs as "courses". Most
    parts of the Open edX system only need to know/care about course runs, not
    catalog courses.

    ⚠️ `CourseRun`s (this model) may exist as marketing/enrollment placeholders
    for courses that do not yet otherwise exist (do not yet have content). So
    in general, you should not assume that any related models/content exist just
    because the `CourseRun` exists.

    `CourseRun` is NOT a generic model for a learning context; this model only
    represents courses and never represents content libraries, pathways, etc.

    Existing related models:
    - This `CourseRun` model should generally be 1:1 with `CourseOverview`;
      however, `CourseOverview` has a number of design issues so we intend to
      slowly transition all use cases to this new model. (In particular,
      `CourseOverview` uses very large string primary keys; has way too many
      fields; and has weird magic behavior when some fields are accessed).
        * Do not assume that `CourseOverview` exists just because `CourseRun`
          exists, nor that `CourseOverview` can be created from a `CourseRun`
          alone.
    - For modulestore courses (in MongoDB), there is a 1:1 relationship to
      `SplitModulestoreCourseIndex`, if content exists, but this relationship
      should only be used for very low-level code, and we intend to delete it in
      the future as content is migrated to Learning Core.

    Future related models:
    - In the future, course metadata should be attached via a series of models,
      like `CourseSchedule`, `CourseGradingPolicy`, `CourseEnrollmentOptions`,
      `CourseMode`, `CoursePricing`, etc. Each of these models may be part of
      this catalog app or other apps. They should either be versioned using
      `PublishableEntity` or use the `HistoricalRecords()` history from
      `django-simple-history` to preserve a record of all changes.
    - In the future, there will be a relationship to Learning Package. Several
      course runs from the same catalog course may be stored in the same
      learning package.
    """

    # Use this field for relationships within the database:
    id = models.BigAutoField(
        primary_key=True,
        verbose_name=_("Primary Key"),
        help_text=_("The internal database ID for this course. Should not be exposed to users nor in APIs."),
        editable=False,
    )
    course_id = CourseKeyField(
        case_sensitive=True,
        db_index=True,
        verbose_name=_("Course ID"),
        help_text=_("The main identifier for this course. Includes the org, course code, and run."),
        editable=False,
        # This column must be unique, but we don't specify 'unique=True' here because we have an even stronger "case
        # insensitively unique" constraint applied to this field below in Meta.constraints.
    )
    # The catalog course stores the 'org' and 'course_code' fields, which must match the ones in the course ID.
    # Note: there is no need to load this relationship to get 'org' or 'course_code'; get them from `course_id` instead.
    catalog_course = models.ForeignKey(
        CatalogCourse,
        on_delete=models.PROTECT,
        null=False,
        related_name="runs",
    )
    run = case_sensitive_char_field(
        # This is case sensitive for simplicity and better validation/constraints, but we have constraints that will
        # prevent duplicate courses with similar runs that differ only in case.
        max_length=128,
        blank=False,
        null=False,
        help_text=_('The code that identifies this particular run of the course, e.g. "2026", "2026Fall" or "2T2026"'),
    )
    # When this course run was first created. We don't track "modified" as this model should be basically immutable,
    # and the only field that may ever change is "display_name"; we also don't want people to think that the "modified"
    # time reflects when edits were last made to the course.
    created = models.DateTimeField(
        auto_now_add=True,
        validators=[validate_utc_datetime],
        editable=False,
    )
    display_name = case_insensitive_char_field(
        max_length=255,
        blank=True,  # Only allowed to be blank temporarily when creating a new instance in the Django admin form
        help_text=_(
            'The full name of this course. e.g. "Introduction to Calculus". '
            "This is required and will override the name of the catalog course. "
            "Leave blank to use the same name as the catalog course. "
        ),
    )

    # 🛑 Avoid adding additional fields here. This model will likely be loaded a lot, so we want to keep it small. Most
    #    course-level configuration should be stored on dedicated models like CourseSchedule with a relationship to
    #    this one.

    @property
    @admin.display(ordering="catalog_course__org__short_name")
    def org_code(self) -> str:
        """
        Get the org code (Organization short_name) of this course, e.g. "MITx"

        ⚠️ This may differ in case (capitalization) from the related
        Organization model's `short_name`, especially with old course runs from
        before the Organization model was introduced.
        """
        # This is not just called 'org' to distinguish it from loading the whole Organization model.
        # Note: 'self.catalog_course.org.short_name' may require a JOIN/query, but self.course_id.org does not.
        return self.course_id.org

    @property
    @admin.display(ordering="catalog_course__course_code")
    def course_code(self) -> str:
        """Get the course code of this course, e.g. "Math100" """
        # Note: 'self.catalog_course.course_code' may require a JOIN/query, but self.course_id.course does not.
        return self.course_id.course

    # Do we want mix in SoftDeletableModel from django-model-utils to make courses soft deletable?

    # In the future, either this model or CatalogCourse will have:
    # learning_package = models.ForeignKey(LearningPackage)

    # In the future, this model will likely have a relationship to the
    # OutlineRoot which would be an `openedx_content` `Container` instance that
    # holds the conten tree (Sections, Subsections, Units, etc.). For now, if
    # the content exists, it will be in modulestore instead (you can get the
    # `SplitModulestoreCourseIndex` using TODO: define API method).

    def clean(self):
        """Defaults and validation of model fields"""
        if self.catalog_course and not self.display_name:
            # For convenience, when creating a CourseRun, if the display_name is blank, copy it from the catalog course
            self.display_name = self.catalog_course.display_name
        if not self.course_id:
            # For now we can assume that the course ID is going to be a CourseLocator, so generate it if missing.
            try:
                self.course_id = CourseLocator(
                    org=self.catalog_course.org_code,
                    course=self.catalog_course.course_code,
                    run=self.run,
                )
            except InvalidKeyError as exc:
                raise ValidationError("Could not generate a valid course_id.") from exc

        if self.catalog_course.org.short_name != self.course_id.org:
            correct_short_name = self.catalog_course.org.short_name
            if correct_short_name.lower() == self.course_id.org.lower():
                log.warning(
                    'Course run "{self.course_id}" does not match case of its org short_name "{correct_short_name}"'
                )
            else:
                raise ValidationError("The CatalogCourse 'org' field should match the org in the course_id key.")

        if self.catalog_course.course_code != self.course_id.course:
            raise ValidationError("The CatalogCourse 'course_code' field should match the course in the course_id key.")

        # We assert "self.run == self.course_id.run" using a database constraint - see below.

        super().clean()

    def save(self, *args, **kwargs):
        """Save the model, with defaults and validation."""
        # Ensure that we run the validations/defaults defined in clean().
        # But don't validate_unique(); it just runs extra queries and the database enforces it anyways.
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.display_name} ({self.org_code} {self.course_code} {self.run})"

    class Meta:
        verbose_name = _("Course Run")
        verbose_name_plural = _("Course Runs")
        ordering = ("-created",)
        constraints = [
            # catalog_course (org+course_code) and run must be unique together:
            models.UniqueConstraint("catalog_course", "run", name="oex_catalog_courserun_catalog_course_run_uniq"),
            # course_id is case-sensitively unique but we also want it to be case-insensitively unique:
            models.UniqueConstraint(Lower("course_id"), name="oex_catalog_courserun_course_id_ci"),
            # Enforce at the DB level that these required fields are not blank:
            models.CheckConstraint(condition=models.Q(run__length__gt=0), name="oex_catalog_courserun_run_not_blank"),
            models.CheckConstraint(
                condition=models.Q(display_name__length__gt=0), name="oex_catalog_courserun_display_name_not_blank"
            ),
            # Enforce that the course ID must end with "+run" where "run" is an exact match for the "run" field.
            # This check may be removed or changed in the future if our course ID format ever changes
            models.CheckConstraint(
                # Note: EndsWith() on SQLite is always case-insensitive, so we code the constraint like this:
                condition=Exact(Right("course_id", Length("run") + 1), Concat(models.Value("+"), "run")),
                name="oex_catalog_courserun_courseid_run_match_exactly",
                violation_error_message=_("The CourseRun 'run' field should match the run in the course_id key."),
            ),
        ]
