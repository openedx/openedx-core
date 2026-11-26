"""
Models for Pathways: the versioned definition of what a learner must complete.

The model hierarchy is :class:`Pathway` → :class:`PathwayVersion` → :class:`PathwayVersionItem` → :class:`PathwayItem` →
:class:`PathwayItemVersion` → :class:`FulfillmentOption` (e.g. :class:`CourseRunFulfillmentOption`).

A Pathway is split in two (see the ``openedx_learning`` ADR 0007). The catalog half - display name, category,
description, enrollment - lives in ``openedx_catalog`` as :class:`~openedx_catalog.models.CatalogPathway`
and is not versioned. This module is the content half: the *definition* of the Pathway, which is versioned, so that
progress can always be judged against the definition that was in effect at the time.

The one link between the halves is :attr:`Pathway.catalog_pathway`, and it lives here rather than in
``openedx_catalog``, which stays unaware of everything above it (``openedx_catalog`` ADR 0001, decision 2).

The boundary between :class:`Pathway` and :class:`PathwayItem` (ADR 0005) is what keeps the Pathway level stable while
fulfillment evolves: a Pathway holds an ordered list of Items and computes its completion from theirs, and never reaches
into what fulfills each Item. The mapping from an Item to the things that fulfill it (ADR 0006) is deliberately confined
to :class:`FulfillmentOption` and its concrete types, which are the natural extension point for everything post-MVP.
"""

from __future__ import annotations

from typing import NewType, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx_catalog.models_api import CatalogPathway, CourseRun
from openedx_content.models_api import (
    LearningPackage,
    PublishableEntity,
    PublishableEntityMixin,
    PublishableEntityVersionMixin,
)
from openedx_django_lib.fields import TypedBigAutoField, case_sensitive_char_field, code_field, code_field_check

__all__ = [
    "CourseRunFulfillmentOption",
    "FulfillmentOption",
    "Pathway",
    "PathwayItem",
    "PathwayItemVersion",
    "PathwayVersion",
    "PathwayVersionItem",
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
    in terms of what fulfills each Item - so adding them should not require touching :class:`FulfillmentOption`.

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

    This is where fulfillment is defined: the ways to fulfill the Item, held as :class:`FulfillmentOption` rows in
    ``pathway_item_version.fulfillment_options``. A version has no direct relationship to any concrete type of option,
    such as a course run, so nothing that reads an Item has to assume what fulfills it. Versioning the options here is
    what makes "publishing changes to a Pathway Item" a meaningful event - the third moment at which fulfillment is
    evaluated (ADR 0006, decision 5).

    .. no_pii:
    """

    pathway_item = models.ForeignKey(
        PathwayItem,
        on_delete=models.CASCADE,
        related_name="versions",
    )

    @property
    def preferred_fulfillment_option(self) -> FulfillmentOption | None:
        """
        The highest-priority option, as its concrete type: for a course run, the one to enroll a learner in when they
        begin this Item.

        ``None`` if the author hasn't listed any options yet, which is possible while an Item is still being drafted.

        To decide what to *show* a learner who is already working on several of the Item's options, take the
        highest-priority option among those rather than this one.
        """
        option = self.fulfillment_options.first()  # type: ignore # pylint: disable=no-member
        return option.get_concrete_option() if option else None

    class Meta:  # type: ignore
        verbose_name = _("Pathway Item Version")
        verbose_name_plural = _("Pathway Item Versions")


class FulfillmentOption(models.Model):
    """
    One way to fulfill a :class:`PathwayItemVersion`.

    This is the generic half of an option: the Item version it belongs to and its priority. What the option requires
    lives in a concrete subclass, through multi-table inheritance - for now only :class:`CourseRunFulfillmentOption`,
    with types such as section completion or a nested Pathway expected later. Every option has exactly one concrete
    type, so never create a bare ``FulfillmentOption``; querying this model returns the generic half only, and
    `get_concrete_option()` gets the rest. A new type is a new subclass and nothing else: this model finds its
    subclasses by itself.

    Fulfilling *any one* of an Item's options fulfills it. The options are **ordered by priority**, and that order
    matters only outside fulfillment: it picks the option a learner starts on when they begin the Item - for a course
    run, the run to enroll them in - and, for a learner already working on several of them, the one to show on their
    dashboard. Fulfillment itself always considers every option, in no particular order.

    Edge cases are resolved at this layer and never leak upward: several fulfilled options of the same Item just mean
    the Item is fulfilled, and one passed course run listed in several Items means each of those Items is fulfilled
    independently.

    This layer is what ADR 0006 expects to iterate on, so it is expected that these models will change. Future
    fulfillment types plug in as new concrete subclasses, without touching Item identity, Pathway structure, or Pathway
    completion criteria. And because every option has a row here whatever its type, a record of how a learner fulfilled
    an Item can point at the option they took.

    .. no_pii:
    """

    FulfillmentOptionID = NewType("FulfillmentOptionID", int)
    type ID = FulfillmentOptionID

    class IDField(TypedBigAutoField[ID]):  # Boilerplate for fully-typed ID field.
        pass

    id = IDField(primary_key=True)

    pathway_item_version = models.ForeignKey(
        PathwayItemVersion,
        on_delete=models.CASCADE,
        related_name="fulfillment_options",
    )
    order_num = models.PositiveIntegerField()
    """
    Priority within the Item's list of options, starting at 0; lower comes first. Priority decides which option a
    learner starts on or is shown, never whether the Item is fulfilled. Reordering means creating a new
    :class:`PathwayItemVersion`, like any other change to a version.
    """

    @staticmethod
    def concrete_type_relations() -> list[str]:
        """
        Get the names of the relations leading from an option to each of its concrete types, e.g. ``course_run_option``.

        Pass them to ``select_related()`` to fetch options along with their concrete halves in one query.
        """
        # Each concrete type links back to this model with a parent link.
        relations = FulfillmentOption._meta.related_objects
        return [name for relation in relations if relation.parent_link and (name := relation.get_accessor_name())]

    def get_concrete_option(self) -> FulfillmentOption:
        """
        Get this option as an instance of its concrete type, e.g. :class:`CourseRunFulfillmentOption`.

        Costs a query per concrete type unless the option was fetched with ``select_related()`` on
        `concrete_type_relations()`, as `get_fulfillment_options_for_item()` does.

        An option with no concrete half comes back as it is. That can't be created through the API; it would be a bare
        ``FulfillmentOption``, or an option of a type whose code is no longer installed. Callers have to check the type
        of what they get anyway, and can't do anything with this one.
        """
        if type(self) is not FulfillmentOption:  # pylint: disable=unidiomatic-typecheck
            return self  # Already concrete.
        for relation in self.concrete_type_relations():
            try:
                return getattr(self, relation)
            except ObjectDoesNotExist:
                continue
        return self

    def __str__(self) -> str:
        return f"{self.pathway_item_version} option #{self.order_num}"

    class Meta:
        verbose_name = _("Fulfillment Option")
        verbose_name_plural = _("Fulfillment Options")
        ordering = ["order_num"]
        constraints = [
            # Priorities are a total order, so no two options may share one.
            models.UniqueConstraint(
                fields=["pathway_item_version", "order_num"],
                name="oel_pathways_fo_uniq_version_order",
            ),
        ]


class CourseRunFulfillmentOption(FulfillmentOption):
    """
    A :class:`FulfillmentOption` met by passing a course run.

    An Item's course runs may belong to different catalog courses. They are listed explicitly, rather than as "any run
    of this catalog course", because we don't necessarily want every older run of a course to count. The cost is that
    authors must update the list by hand when new runs are created. Adding a newer run ahead of an older one is how an
    author gives a learner who failed the older run another attempt.

    "Passing" is determined by each course's own grading policy. Pathways define no grading of their own and store no
    copy of grades, so there is nothing here to keep in sync.

    A run is listed at most once per Item version, so that its priority is unambiguous. The database can't enforce that,
    because the run lives on this table and the version on :class:`FulfillmentOption`'s, so the API does.

    .. no_pii:
    """

    fulfillment_option = models.OneToOneField(
        FulfillmentOption,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
        related_name="course_run_option",
    )
    course_run = models.ForeignKey(
        CourseRun,
        on_delete=models.RESTRICT,
        related_name="+",
    )
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

    class Meta:  # type: ignore
        verbose_name = _("Course Run Fulfillment Option")
        verbose_name_plural = _("Course Run Fulfillment Options")
        # The priority order is inherited from FulfillmentOption.
