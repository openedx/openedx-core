from django.conf import settings
from django.db import models

from openedx_learning.lib.fields import (
    case_insensitive_char_field,
    immutable_uuid_field,
    key_field,
    manual_date_time_field,
)


class PathwayType(models.Model):
    """
    Labeled PathwayTypes may be set at a system or maybe even org level.

    Examples: Tier, Group, Pathway
    """
    label = models.CharField()


class PathwayCompletionCriteria(models.Model):
    """
    How do we determine if a Pathway is complete?

    Can we encode rules in CEL? https://cel.dev/

    Example:
      // each item is a StudentPathwayItem
      all(items, item.complete && item.grade > 0.8)

    It probably makes sense to make more than one type of
    PathwayCompletionCriteria, but I hope CEL can do a lot of the work.

    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    cel = models.TextField(blank=True)


class Pathway(models.Model):
    """
    The top level Pathway model.

    We could make this a PublishableEntity and make versions.

    In that case, we'd probably want to imitate parent-child relations, i.e.
    store floating references to PathwayItems and allow each of those to publish
    updates separately, like we do for Container types.
    """
    pathway_type = models.ForeignKey(PathwayType, on_delete=models.RESTRICT)
    completion_criteria = models.ForeignKey(PathwayCompletionCriteria, on_delete=models.SET_NULL)
    key = key_field(db_column="_key")


class PathwayItem(models.Model):
    """
    A single step in a pathway.

    Examples: "Intro CS Course", "HW Assignment 20", etc.
    """
    pathway = models.ForeignKey(Pathway, on_delete=models.CASCADE)
    key = key_field(db_column="_key")
    title = case_insensitive_char_field()
    description = models.TextField()



class StudentPathwayProgress(models.Model):
    """
    TODO: This needs some status indicator of their completion, but also
    potentially things like DEMONSTRATED_MASTERY, or more granular
    categories of competency/mastery...

    Or is that separate? Is the thing that decides "what is your progress and
    when are you done with the Pathway" actually different from "what does your
    performance in the Pathway equate to in terms of your credential?"
    """
    pathway = models.ForeignKey(Pathway, on_delete=models.RESTRICT)
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )


class PathwayItemCriteria(models.Model):
    """
    This represents a potential way to fulfill a PathwayItem.

    It is abstract.

    It is not strictly necessary. A CoursePathwayItemAttempt might point
    to a CoursePathwayItemCriteria, but
    """
    item = models.ForeignKey(PathwayItem)
    required_completion_level = models.FloatField(null=True)
    required_grade = models.FloatField(null=True)


class StudentPathwayItemStatus(models.Model):
    """
    Possible statuses:

    - Unavailable
    - Available
    - Started
    - Succeeded
    - Failed

    State change note: Content may change
    """
    id = models.AutoField()
    name = models.CharField(100)


class StudentPathwayItem(models.Model):
    """
    The status for this Student on a particular Item in a Learning Pathway.

    For example, if a PathwayItem represents, "Must pass one of the
    following course runs with a grade of at least 80%", then there might be a
    StudentPathwayItem that represents, "Student A passed Course Run C
    with a grade of 84%".

    A student may require multiple attempts to achieve a PathwayItem's
    requirements. We capture those attempts in StudentPathwayItemAttempt. Note
    that StudentPathwayItemAttempt -> StudentPathwayItem is NOT for the purposes
    of aggregation. If we want to model something like, "The student must pass
    these four Course Runs with grades of > 80%," that is a Pathway with four
    PathwayItems that can each be satisfied by one of those Course Runs.

    For a given StudentPathwayItem, we should be able to point to exactly one
    StudentPathwayItemAttempt that represents the "active" one. So if someone
    failed a previous Course Run and is trying again, the active_attempt will
    shift to that new CoursePathwayItemAttempt.
    """
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    item = models.ForeignKey(PathwayItem, on_delete=models.RESTRICT)
    status = models.ForeignKey(StudentPathwayItemStatus, on_delete=models.PROTECT)
    active_attempt = models.ForeignKey(
        'PathwayItemAttempt',
        null=True,
    )


class PathwayItemAttempt(models.Model):
    """
    This follows the status of a given attempt to fulfill a pathway item.

    For instance, this could represent a student's grade on a particular Course
    Run as it changes over time, or it could represent the grade for a
    particular subsection in a CBE context.

    This does not give a full history of progress within a given attempt. So
    there will only be one of these rows for a given student's progress in a
    given course run. If we had to create a new row for every change, the size
    of this table would explode, and that kind of data collection is better
    handled by eventing/analytics.
    """
    student_pathway_item = models.ForeignKey(
        StudentPathwayItem,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    grade = models.FloatField(default=0.0)
    completion_level = models.FloatField(default=0.0)

    created = manual_date_time_field()
    updated = manual_date_time_field()


#################### This is specific to Courses  ####################
class CoursePathwayItemCriteria(PathwayItemCriteria):
    """
    This is a hypothetical PathwayItemCriteria type that can be satisifed by
    a catalog course (as opposed to a specific run).
    """
    course = key_field()  # This should eventually be an fkey to Course


class CoursePathwayItemAttempt(PathwayItemAttempt):
    """
    Docstring for CoursePathwayItemAttempt
    """
    criteria = models.ForeignKey(CoursePathwayItemCriteria, on_delete=models.RESTRICT)
    course_run = key_field()  # This should eventually be an fkey to CourseRun (or learning context?)


#################### Admin/Manual Override  ####################

class ManualOverridePathwayItemAttempt(PathwayItemAttempt):
    overridden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='+',
    )

#################### Competencies  ####################

class SubsectionPathwayItemCriteria(PathwayItemCriteria):
    # Should probably be a fkey to a Usage model
    usage_key = models.CharField()

class SubsectionPathwayItemAttempt(PathwayItemAttempt):
    # I'm not sure it would need a separate attempt type.
    pass
