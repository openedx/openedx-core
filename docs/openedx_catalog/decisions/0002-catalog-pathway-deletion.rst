.. _openedx-catalog-adr-0002:

2. Catalog Pathway Deletion and Visibility
==========================================

Status
------

Accepted

To be partially implemented in Willow, fully implemented in Xylon.

Context
-------

``CatalogPathway`` is the learner-browsable, enrollable side of a Pathway
(:ref:`openedx-learning-adr-0007`), and ``PathwayEnrollment`` hangs off it. That raises two questions
that the MVP has to answer:

- Can a ``CatalogPathway`` be deleted, and what happens to its enrollments?
- Is there a way to stop advertising a Pathway without deleting it?

Cascading deletion into enrollments would mean that deleting one catalog row silently destroys student state,
which we don't want. Forbidding deletion outright is safe, but authors do make mistakes, and a Pathway created
in error should be removable.

The visibility question has a natural answer that we don't have time to build for the MVP. We expect to
eventually introduce a **Catalog** model, many-to-many with ``CatalogCourse`` and ``CatalogPathway``:
each site would have a default Catalog (its public catalog), and could define additional, narrower Catalogs
(think "enterprise catalogs"). Un-advertising a Pathway would then be deleting a Catalog-to-CatalogPathway join
row, not deleting the Pathway itself.

Decisions
---------

1. Deletion is protected, not cascaded
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``PathwayEnrollment`` references ``CatalogPathway`` with ``on_delete=PROTET``, consistent with the
rest of the catalog app. A ``CatalogPathway`` that nobody has enrolled in can be deleted; one with any
enrollment cannot be deleted at all. Deleting a Pathway never destroys enrollment records.

2. In Willow, every Catalog Pathway is public
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The MVP ships no visibility toggle and no soft-deletion flag on ``CatalogPathway``. Every
``CatalogPathway`` row is advertised.

3. We intend to add a Catalog model in Xylon
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Our intent, as of Willow, is to add the Catalog model described above in Xylon and backfill it: a default
Catalog per site, joined to every existing ``CatalogCourse`` and ``CatalogPathway``, so that the
Willow behavior ("everything is public") is preserved on upgrade. Visibility would then be managed through
Catalog membership. This is a statement of intent rather than a commitment; it may be reshaped or deferred.

This is a statement of intent from the engineering team, based on what we expect the product will need. The whole area of catalog deletion, visibility, curation, etc. is in need of product definition, so this is subject to change.

Consequences
------------

- Student state is never lost as a side effect of a catalog edit.
- Until the Catalog model exists, an operator who wants to stop advertising a Pathway that has enrollments has
  no supported way to do it. We accept that gap for the MVP; it is the main reason we expect to prioritize the
  Catalog model in Xylon.
- Attempting to delete an enrolled-in Pathway surfaces a database-level ``ProtectedError``. Django admin and any
  authoring UI should present that as a clear message rather than a traceback.
- Product expectations should be checked against this before Willow ships, since "I made a mistake and want this
  gone" is a plausible request that decision 1 does not fully satisfy.

Rejected Alternatives
---------------------

**Cascade deletion into enrollments.** Deleting a ``CatalogPathway`` would wipe out every enrollment for it.
This makes a routine-looking authoring action destroy learner records, with no way back.

**Forbid deletion entirely.** Safe, but it leaves no way to clean up a Pathway created by mistake, and every
instance would accumulate junk rows.

**A visibility flag or soft-deletion on CatalogPathway.** This would solve the un-advertising problem now, but it
duplicates what Catalog membership is meant to express, and we would have to carry both forever once Catalogs
exist.
