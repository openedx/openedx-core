Pathways Applet
===============

A Pathway is a set of requirements a learner works through to earn some larger achievement. Each requirement is a
**Pathway Item**. The ADRs behind this applet are in ``docs/openedx_learning/decisions/``.

Where things live
-----------------

A Pathway is split in two, for the same reasons courses are.

The **catalog half** is ``openedx_catalog.models.CatalogPathway``: display name, ``PathwayCategory``, description,
and enrollment. It is *not* versioned, because marketing copy is revised frequently and casually and is
typically maintained by different people than the definition.

The **content half** is this applet: ``Pathway`` and ``PathwayItem``, both ``PublishableEntity`` subclasses, so the
definition is versioned and can always be inspected as of a given moment.

The link between them, ``Pathway.catalog_pathway``, lives here and never in ``openedx_catalog`` - that app must not know
about anything above it. It is one-to-one, so a Catalog Pathway is implemented by at most one Pathway, and it is fixed
for the Pathway's whole life, so it isn't versioned. A ``CatalogPathway`` cannot tell you what implements it; ask
``get_pathway_for_catalog_pathway()``, which looks it up from this side. To list Pathways, list their Catalog Pathways
with ``openedx_catalog.api.get_catalog_pathways()``, which filters by org and category.

Each Pathway has a Learning Package of its own, named after its Catalog Pathway's key and never shared with another
Pathway, so that publishing, backing up, or deleting the package does exactly that to the one Pathway and its Items.

The Pathway/Item boundary
-------------------------

``Pathway`` holds an ordered list of ``PathwayItem`` objects through ``PathwayVersionItem``. The order is author-defined
and is the presentation order; it does not yet constrain the order of completion.

A Pathway owns its Items: each ``PathwayItem`` belongs to exactly one Pathway (``PathwayItem.pathway``) and is never
listed in another. What Pathways share is course runs - the same run may fulfill Items in several Pathways. Because an
Item needs its Pathway to exist first, a new Pathway starts with an empty version; its Items are created afterwards and
listed in the next version.

Item completion and Pathway completion are separate concerns. An Item is complete or not, decided by its own fulfillment
rules. A Pathway's completion is computed from its Items' completion and never reaches past that boundary. In the MVP a
Pathway is complete when all of its Items are; configurable criteria are expected later.

That boundary is why new fulfillment types - section completion, competency attainment, admin override - can be added
without restructuring Pathways or rewriting how Pathway completion is computed.

Fulfillment, today
------------------

A ``PathwayItemVersion`` holds a list of ``FulfillmentOption`` rows: the ways to fulfill the Item. Fulfilling *any one*
of them fulfills the Item. ``FulfillmentOption`` is generic - it records only the Item version and the option's
priority - and what an option requires lives in a concrete subclass, through multi-table inheritance. An Item version has
no direct relationship to any concrete type, so code that reads an Item has to ask what kind of option it is looking at
(``get_concrete_option()``) instead of assuming course runs. New types, such as section completion or a nested Pathway,
are new subclasses, which ``FulfillmentOption`` finds by itself. This layer may need reworking as those types arrive.

The list is ordered by **priority**, and priority matters only outside fulfillment. It picks the option a learner starts
on when they begin the Item, and, for a learner already working on several of them, the one to show on their dashboard.
Fulfillment itself ignores the order and considers every option in the list.

For now, the only type is ``CourseRunFulfillmentOption``: passing a course run. An Item's runs may belong to different
catalog courses. They are listed explicitly rather than as "any run of this catalog course", because not every older run
should necessarily count - the trade-off being that authors update the list by hand as new runs appear. Each run also
carries the track to enroll a learner in if that run uses several, and putting a newer run ahead of an older one is how
an author gives a learner who failed the older run another attempt.

Pathways define no grading. Whether a learner passed a run is decided by that course's own grading policy and is read
from the course; nothing here stores a copy, so there are no grades to keep in sync.

What isn't here yet
-------------------

**Evaluating fulfillment for a learner.** ADR 0006 describes three moments at which it should happen:

1. A course passing-status signal, re-evaluating the Items whose run list includes that run, for the Pathways that
   learner is enrolled in.
2. Pathway enrollment, a one-time pass over everything the learner has already passed - this is what makes prior work
   count.
3. Publishing a change to a Pathway Item, which fans out asynchronously over the learners enrolled in the Pathway
   containing it. This trigger exists because the first two miss the case where a learner passes a run, enrolls, and
   *then* an author edits an Item so that run fulfills it.

Only the third needs an async task; the first two act on a single learner. Retroactive evaluation only ever grants -
narrowing an Item by dropping a run does not revoke a credential already awarded.

The read-only lookups those triggers need already exist. For the first, ``get_pathways_containing_course_run()`` finds
every Pathway the run counts towards, and ``get_pathway_items_fulfilled_by_course_run()`` the Items to re-evaluate. For
the third, the Item's own ``pathway`` is the only Pathway affected.

**Reading what a learner has passed.** ``openedx-core`` has no grades app, and ADR 0006 rules out duplicating grades on
the Pathway side. So the evaluation code should reach course passing state through a pluggable provider, resolved from a
Django setting, whose real implementation is backed by ``PersistentCourseGrade``. That implementation belongs in
``openedx-platform`` or in a standalone plugin, not here.

**Learner-state models.** No fulfillment or completion records exist yet; the credentials design is not settled. Nothing
in the current models has to be revisited to add them - Item completion is a yes/no contract that can be extended
additively, and a record of how a learner fulfilled an Item can point at the ``FulfillmentOption`` they took, whatever
its type.
