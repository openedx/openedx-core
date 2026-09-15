"""
Models for Pathways: the versioned definition of what a learner must complete.

The model hierarchy is :class:`Pathway` → :class:`PathwayVersion` → :class:`PathwayVersionItem` → :class:`PathwayItem` →
:class:`PathwayItemVersion` → :class:`PathwayItemCourseRun`.

A Pathway is split in two (see the ``openedx_learning`` ADR 0007). The catalog half - display name, category,
description, enrollment - lives in ``openedx_catalog`` as :class:`~openedx_catalog.models.CatalogPathway`
and is not versioned. This module is the content half: the *definition* of the Pathway, which is versioned, so that
progress can always be judged against the definition that was in effect at the time.

The one link between the halves is ``CatalogPathway.content_entity``, which points at a :class:`Pathway`'s
``publishable_entity``. It lives on the catalog side because a context points at its content, never the reverse
(``openedx_catalog`` ADR 0001). ``openedx_catalog`` sits below this app, so it can only type that field as a bare
``PublishableEntity``; the API in this applet is what sets the link and resolves it to a :class:`Pathway`.

The boundary between :class:`Pathway` and :class:`PathwayItem` (ADR 0005) is what keeps the Pathway level stable while
fulfillment evolves: a Pathway holds an ordered list of Items and computes its completion from theirs, and never reaches
into what fulfills each Item. The mapping from an Item to the things that fulfill it (ADR 0006) is deliberately confined
to :class:`PathwayItemCourseRun`, which is the natural extension point for everything post-MVP.
"""

from __future__ import annotations

from typing import NewType, cast

from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx_catalog.models_api import CourseRun
from openedx_content.models_api import (
    LearningPackage,
    PublishableEntity,
    PublishableEntityMixin,
    PublishableEntityVersionMixin,
)
from openedx_django_lib.fields import case_sensitive_char_field, code_field, code_field_check

__all__ = [
    "Pathway",
    "PathwayVersion",
    "PathwayVersionItem",
    "PathwayItem",
    "PathwayItemVersion",
    "PathwayItemCourseRun",
]


class Pathway(PublishableEntityMixin):
    """
    The versioned content half of a Pathway: an ordered list of requirements.

    A :class:`Pathway` is 1:1 with a :class:`PublishableEntity` and has matching primary key values, so it is versioned,
    published, and reverted through the ordinary publishing machinery.

    A Pathway is *not* a :class:`~openedx_content.models_api.Container`. The parent-child relation here breaks no new
    ground structurally, but Pathways don't need Container's full complexity - pinning children to specific versions,
    dynamic membership, OLX serialization - so they use their own simple through-model, :class:`PathwayVersionItem`.

    Completion: in the MVP, a Pathway is complete when *all* of its Items are complete. That isn't configurable yet.
    Configurable criteria are expected later, and are meant to be expressed in terms of Item completion rather than
    in terms of what fulfills each Item - so adding them should not require touching :class:`PathwayItemCourseRun`.

    The learner-facing :class:`~openedx_catalog.models.CatalogPathway` that a Pathway serves points at it through
    ``CatalogPathway.content_entity``. That link is unversioned - the catalog entry follows whichever version of the
    Pathway is published - and optional in both directions: a Pathway may exist without a catalog entry while it is
    being drafted, and a catalog entry may exist before its Pathway does. Use ``get_catalog_pathway_for_pathway()`` and
    ``get_pathway_for_catalog_pathway()`` to cross between the two.

    .. no_pii:
    """

    PathwayID = NewType("PathwayID", PublishableEntity.ID)
    type ID = PathwayID

    learning_package = models.ForeignKey(LearningPackage, on_delete=models.CASCADE)
    """
    Technically redundant - we're already locked to a single LearningPackage through ``publishable_entity`` - but having
    the foreign key directly lets us index efficiently by other Pathway fields within a given LearningPackage. This
    mirrors what :class:`~openedx_content.models_api.Container` does.
    """

    pathway_code = code_field(unicode=True)
    """
    A slug-like identifier that is local to the ``learning_package``.
    """

    @property
    def id(self) -> ID:
        return cast(Pathway.ID, self.publishable_entity_id)  # type: ignore

    class Meta:  # type: ignore
        verbose_name = _("Pathway")
        verbose_name_plural = _("Pathways")
        constraints = [
            models.UniqueConstraint(
                fields=["learning_package", "pathway_code"],
                name="oel_pathways_pathway_uniq_lp_code",
            ),
            code_field_check("pathway_code", name="oel_pathways_pathway_code_regex", unicode=True),
        ]


class PathwayVersion(PublishableEntityVersionMixin):
    """
    A specific version of a :class:`Pathway`.

    A new version is created when the *definition* changes: an Item is added, removed, or reordered, or the Pathway's
    own metadata changes. Catalog edits never create one - that's the point of the split.

    .. no_pii:
    """

    pathway = models.ForeignKey(
        Pathway,
        on_delete=models.CASCADE,
        related_name="versions",
    )

    items: models.ManyToManyField[PathwayItem, PathwayVersionItem] = models.ManyToManyField(
        "PathwayItem",
        through="PathwayVersionItem",
        related_name="pathway_versions",
    )
    """
    The Items in this version of the Pathway, in author-defined order. Use ``pathway_version.item_rows`` to read them in
    order along with their ``order_num``.
    """

    class Meta:  # type: ignore
        verbose_name = _("Pathway Version")
        verbose_name_plural = _("Pathway Versions")


class PathwayVersionItem(models.Model):
    """
    One :class:`PathwayItem`'s place in the ordered list of a :class:`PathwayVersion`.

    The order is author-defined and is the order in which Items are presented to learners. It does **not** currently
    constrain the order of *completion*; enforcing that is planned for a later iteration (ADR 0005, decision 1).

    References to Items are always unpinned - an Item row points at the :class:`PathwayItem`, not at one of its
    versions - so a Pathway always reflects the current state of its Items. Items are declared as dependencies of the
    :class:`PathwayVersion` (see the publishing app's ``set_version_dependencies``) so that changes to an Item still
    register as changes to the Pathway that contains it.

    .. no_pii:
    """

    id = models.BigAutoField(primary_key=True)

    pathway_version = models.ForeignKey(
        PathwayVersion,
        on_delete=models.CASCADE,
        related_name="item_rows",
    )
    pathway_item = models.ForeignKey(
        "PathwayItem",
        on_delete=models.RESTRICT,
        related_name="version_rows",
    )
    order_num = models.PositiveIntegerField()
    """
    Position within the Pathway, starting at 0. Immutable for a given :class:`PathwayVersion`: reordering means creating
    a new PathwayVersion.
    """

    def __str__(self) -> str:
        return f"{self.pathway_version} #{self.order_num}: {self.pathway_item}"

    class Meta:
        verbose_name = _("Pathway Version Item")
        verbose_name_plural = _("Pathway Version Items")
        ordering = ["order_num"]
        constraints = [
            models.UniqueConstraint(
                fields=["pathway_version", "order_num"],
                name="oel_pathways_pvi_uniq_version_order",
            ),
            # An Item appears at most once in a given version of a Pathway. Listing it twice would make "3 of 6 Items
            # complete" ambiguous for no benefit.
            models.UniqueConstraint(
                fields=["pathway_version", "pathway_item"],
                name="oel_pathways_pvi_uniq_version_item",
            ),
        ]


class PathwayItem(PublishableEntityMixin):
    """
    A single requirement within a Pathway, with its own identity and lifecycle.

    An Item may be fulfilled by one thing today - passing a course - and by something else tomorrow - a competency
    attainment, an admin override - without changing its identity, and therefore without changing the Pathway that
    contains it. That stability is the whole reason Items are modeled separately from what fulfills them (ADR 0005,
    decision 2).

    An Item is complete or not; that is the entire contract for now. It can be extended later to carry grades or other
    metadata additively, if Pathway-level criteria or learner-facing displays need it.

    :class:`PathwayItem` is a :class:`PublishableEntity` in its own right, so an author can revise an Item repeatedly in
    draft and have only the published result reach learners.

    .. no_pii:
    """

    PathwayItemID = NewType("PathwayItemID", PublishableEntity.ID)
    type ID = PathwayItemID

    learning_package = models.ForeignKey(LearningPackage, on_delete=models.CASCADE)
    """
    Redundant with ``publishable_entity.learning_package``, for the same indexing reasons as
    :attr:`Pathway.learning_package`.
    """

    item_code = code_field(unicode=True)
    """
    A slug-like identifier that is local to the ``learning_package``.
    """

    @property
    def id(self) -> ID:
        return cast(PathwayItem.ID, self.publishable_entity_id)  # type: ignore

    class Meta:  # type: ignore
        verbose_name = _("Pathway Item")
        verbose_name_plural = _("Pathway Items")
        constraints = [
            models.UniqueConstraint(
                fields=["learning_package", "item_code"],
                name="oel_pathways_item_uniq_lp_code",
            ),
            code_field_check("item_code", name="oel_pathways_item_code_regex", unicode=True),
        ]


class PathwayItemVersion(PublishableEntityVersionMixin):
    """
    A specific version of a :class:`PathwayItem`.

    This is where fulfillment is defined: the list of course runs that fulfill the Item, held as
    :class:`PathwayItemCourseRun` rows. Versioning it here is what makes "publishing changes to a Pathway Item" a
    meaningful event - the third moment at which fulfillment is evaluated (ADR 0006, decision 5).

    .. no_pii:
    """

    pathway_item = models.ForeignKey(
        PathwayItem,
        on_delete=models.CASCADE,
        related_name="versions",
    )

    course_runs: models.ManyToManyField[CourseRun, PathwayItemCourseRun] = models.ManyToManyField(
        CourseRun,
        through="PathwayItemCourseRun",
        related_name="fulfilled_pathway_item_versions",
    )
    """
    The course runs that fulfill this version of the Item. Use ``pathway_item_version.course_run_rows`` to read them in
    order along with their ``is_default`` and ``enrollment_track`` values.
    """

    @property
    def default_course_run_row(self) -> PathwayItemCourseRun | None:
        """
        The course run a learner is enrolled in when they begin this Item.

        ``None`` if the author hasn't designated one, which is possible while an Item is still being drafted.
        """
        return self.course_run_rows.filter(is_default=True).first()  # type: ignore

    class Meta:  # type: ignore
        verbose_name = _("Pathway Item Version")
        verbose_name_plural = _("Pathway Item Versions")


class PathwayItemCourseRun(models.Model):
    """
    A course run whose passing fulfills a :class:`PathwayItemVersion`.

    Passing *any one* of an Item's runs fulfills it; the runs may belong to different catalog courses. The list is
    explicit, rather than "any run of this catalog course", because we don't necessarily want every older run of a
    course to count. The cost is that authors must update the list by hand when new runs are created.

    "Passing" is determined by each course's own grading policy. Pathways define no grading of their own and store no
    copy of grades, so there is nothing here to keep in sync.

    Edge cases are resolved at this layer and never leak upward: several passed runs fulfilling the same Item just means
    the Item is fulfilled, and one passed run fulfilling several Items means each of those Items is fulfilled
    independently.

    This model is the mapping layer that ADR 0006 expects to iterate on. Future fulfillment types - section completion,
    competency attainment, admin override - plug in here as alternative ways to fulfill an Item, without touching Item
    identity, Pathway structure, or Pathway completion criteria.

    .. no_pii:
    """

    id = models.BigAutoField(primary_key=True)

    pathway_item_version = models.ForeignKey(
        PathwayItemVersion,
        on_delete=models.CASCADE,
        related_name="course_run_rows",
    )
    course_run = models.ForeignKey(
        CourseRun,
        on_delete=models.RESTRICT,
        related_name="+",
    )
    order_num = models.PositiveIntegerField()
    """
    Author-defined display order, starting at 0. It carries no meaning for fulfillment - passing any one run fulfills
    the Item.
    """

    is_default = models.BooleanField(
        default=False,
        help_text=_("The run a learner is enrolled in when they begin this Item. At most one per Item version."),
    )
    """
    Modeling the default as a flag on the list, rather than as a foreign key on :class:`PathwayItemVersion`, is what
    guarantees the default is always one of the runs that actually fulfills the Item. The default can change over time,
    which means a new :class:`PathwayItemVersion`.
    """

    enrollment_track = case_sensitive_char_field(
        max_length=100,
        blank=True,
        default="",
        help_text=_(
            "If the default run uses multiple enrollment tracks, the track to enroll the learner in. "
            "Only meaningful on the default run; leave blank otherwise."
        ),
    )

    def __str__(self) -> str:
        suffix = " (default)" if self.is_default else ""
        return f"{self.course_run}{suffix}"

    class Meta:
        verbose_name = _("Pathway Item Course Run")
        verbose_name_plural = _("Pathway Item Course Runs")
        ordering = ["order_num"]
        constraints = [
            models.UniqueConstraint(
                fields=["pathway_item_version", "course_run"],
                name="oel_pathways_picr_uniq_version_run",
            ),
            models.UniqueConstraint(
                fields=["pathway_item_version", "order_num"],
                name="oel_pathways_picr_uniq_version_order",
            ),
            # At most one default run per Item version.
            # MySQL and MariaDB ignore conditional unique constraints, so this would only be enforced by the database
            # on backends that support partial indexes. However, the API already enforces it on every backend.
            models.UniqueConstraint(
                fields=["pathway_item_version"],
                condition=models.Q(is_default=True),
                name="oel_pathways_picr_one_default",
            ),
            models.CheckConstraint(
                condition=models.Q(is_default=True) | models.Q(enrollment_track=""),
                name="oel_pathways_picr_track_only_on_default",
                violation_error_message=_("An enrollment track can only be set on the default course run."),
            ),
        ]
