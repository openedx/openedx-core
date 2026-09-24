"""
Models for Pathways: the versioned definition of what a learner must complete.

The model hierarchy is :class:`Pathway` → :class:`PathwayVersion` → :class:`PathwayVersionItem` → :class:`PathwayItem` →
:class:`PathwayItemVersion` → :class:`PathwayItemCourseRun`.

A Pathway is split in two (see the ``openedx_learning`` ADR 0007). The catalog half - display name, category,
description, enrollment - lives in ``openedx_catalog`` as :class:`~openedx_catalog.models.CatalogPathway`
and is not versioned. This module is the content half: the *definition* of the Pathway, which is versioned, so that
progress can always be judged against the definition that was in effect at the time.

The one link between the halves is :attr:`Pathway.catalog_pathway`, and it lives here rather than in
``openedx_catalog``, which stays unaware of everything above it (``openedx_catalog`` ADR 0001, decision 2).

The boundary between :class:`Pathway` and :class:`PathwayItem` (ADR 0005) is what keeps the Pathway level stable while
fulfillment evolves: a Pathway holds an ordered list of Items and computes its completion from theirs, and never reaches
into what fulfills each Item. The mapping from an Item to the things that fulfill it (ADR 0006) is deliberately confined
to :class:`PathwayItemCourseRun`, which is the natural extension point for everything post-MVP.
"""

from __future__ import annotations

from typing import NewType, cast

from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx_catalog.models_api import CatalogPathway, CourseRun
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

    A Pathway owns its Items (:attr:`PathwayItem.pathway`); they are never shared with another Pathway. What Pathways
    do share is course runs: the same run may fulfill Items in several of them.

    .. no_pii:
    """

    PathwayID = NewType("PathwayID", PublishableEntity.ID)
    type ID = PathwayID

    catalog_pathway = models.OneToOneField(
        CatalogPathway,
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("The learner-facing Catalog Pathway that this Pathway implements."),
    )
    """
    One-to-one, so each Catalog Pathway is implemented by at most one Pathway. The link is fixed for the Pathway's whole
    life, which is why it isn't versioned: the Catalog Pathway of any version is ``version.pathway.catalog_pathway``,
    and can't have been anything else when that version was current. ``PROTECT`` because deleting the catalog half out
    from under published content would leave learners enrolled in something with no definition.

    The link lives here, on the content side, because ``openedx_catalog`` must not know about anything above it. A
    :class:`CatalogPathway` therefore cannot tell you which Pathway implements it; ``get_pathway_for_catalog_pathway()``
    looks that up from this side.
    """

    learning_package = models.OneToOneField(
        LearningPackage,
        on_delete=models.CASCADE,
        related_name="+",
    )
    """
    The Learning Package holding this Pathway and its Items, and nothing else. It's also reachable through
    ``publishable_entity``; having it here, one-to-one, is what guarantees that no other Pathway shares it, so that
    publishing, backing up or deleting the package does exactly that to this Pathway.
    """

    @property
    def id(self) -> ID:
        return cast(Pathway.ID, self.publishable_entity_id)  # type: ignore

    def __str__(self) -> str:
        # The entity_ref is the same for every Pathway (each has a package of its own), so it can't identify one.
        return f"Pathway for {self.catalog_pathway}"

    class Meta:  # type: ignore
        verbose_name = _("Pathway")
        verbose_name_plural = _("Pathways")


class PathwayVersion(PublishableEntityVersionMixin):
    """
    A specific version of a :class:`Pathway`.

    A new version is created when the *definition* changes: an Item is added, removed, or reordered, or the Pathway's
    own metadata changes. Catalog edits never create one - that's the point of the split. Neither does anything change
    which Catalog Pathway is implemented: that is fixed on the :class:`Pathway`.

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

    A version may only list its own Pathway's Items. The database can't enforce that, because it would have to compare
    :attr:`PathwayItem.pathway` with ``pathway_version.pathway`` across tables, so the API does.

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

    pathway = models.ForeignKey(
        Pathway,
        on_delete=models.CASCADE,
        related_name="items",
    )
    """
    The Pathway this Item belongs to, for its whole life. ``pathway.items`` is therefore every Item the Pathway has ever
    had, including ones its current version no longer lists; for the current list, read the version's ``item_rows``.

    The Item lives in its Pathway's Learning Package.
    """

    item_code = code_field(unicode=True)
    """
    A slug-like identifier that is local to the ``pathway``.
    """

    @property
    def id(self) -> ID:
        return cast(PathwayItem.ID, self.publishable_entity_id)  # type: ignore

    class Meta:  # type: ignore
        verbose_name = _("Pathway Item")
        verbose_name_plural = _("Pathway Items")
        constraints = [
            models.UniqueConstraint(
                fields=["pathway", "item_code"],
                name="oel_pathways_item_uniq_pathway_code",
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
    The course runs that fulfill this version of the Item, in priority order. Use
    ``pathway_item_version.course_run_rows`` to read them in that order along with their ``enrollment_track`` values.
    """

    @property
    def preferred_course_run_row(self) -> PathwayItemCourseRun | None:
        """
        The highest-priority run: the one to enroll a learner in when they begin this Item.

        ``None`` if the author hasn't listed any runs yet, which is possible while an Item is still being drafted.

        To decide what to *show* a learner who is already enrolled in several of the Item's runs, take the
        highest-priority row among the ones they are enrolled in rather than this one.
        """
        return self.course_run_rows.first()  # type: ignore

    class Meta:  # type: ignore
        verbose_name = _("Pathway Item Version")
        verbose_name_plural = _("Pathway Item Versions")


class PathwayItemCourseRun(models.Model):
    """
    A course run whose passing fulfills a :class:`PathwayItemVersion`.

    Passing *any one* of an Item's runs fulfills it; the runs may belong to different catalog courses. The list is
    explicit, rather than "any run of this catalog course", because we don't necessarily want every older run of a
    course to count. The cost is that authors must update the list by hand when new runs are created.

    The list is **ordered by priority**, and that order matters only outside fulfillment: it picks the run to enroll a
    learner in when they begin the Item, and, for a learner already enrolled in several of them, the one to show on
    their dashboard. Adding a newer run ahead of an older one is how an author gives a learner who failed the older run
    another attempt. Fulfillment itself always considers every run in the list, in no particular order.

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
    Priority within the Item's list of runs, starting at 0; lower comes first. Priority decides which run a learner is
    enrolled in or shown, never whether the Item is fulfilled. Reordering means creating a new
    :class:`PathwayItemVersion`, like any other change to a version.
    """

    enrollment_track = case_sensitive_char_field(
        max_length=100,
        blank=True,
        default="",
        help_text=_(
            "If this run uses multiple enrollment tracks, the track to enroll the learner in. Leave blank otherwise."
        ),
    )

    def __str__(self) -> str:
        return str(self.course_run)

    class Meta:
        verbose_name = _("Pathway Item Course Run")
        verbose_name_plural = _("Pathway Item Course Runs")
        ordering = ["order_num"]
        constraints = [
            models.UniqueConstraint(
                fields=["pathway_item_version", "course_run"],
                name="oel_pathways_picr_uniq_version_run",
            ),
            # Priorities are a total order, so no two runs may share one.
            models.UniqueConstraint(
                fields=["pathway_item_version", "order_num"],
                name="oel_pathways_picr_uniq_version_order",
            ),
        ]
