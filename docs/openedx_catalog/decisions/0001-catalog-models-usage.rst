.. _openedx-catalog-adr-0001:

1. Role of Catalog
==================

Status
------

Draft

Context
-------

``openedx_catalog`` holds the core models for tracking enrollable things (and eventually, enrollments as well).

Specifically, its main models are:

- :class:`CourseRun` (one course run, e.g. "Math 100 2026Fall").
- :class:`CatalogCourse` (a set of course runs, e.g. "Math 100")
- :class:`CatalogPathway` (a pathway that learners can enroll in)
- :class:`PathwayCategory` (learner-facing label for pathway types, e.g. "Masters Degree")
- :class:`PathwayEnrollment` (tracks enrollment into pathways)

This ADR clarifies how the catalog models are meant to be used.

``openedx_content`` holds the authored, versioned material itself, grouped into :class:`LearningPackage` instances. Until now the direction of the relationship between the two apps has been left open.

Decisions
---------

1. Catalog entries may be placeholders with no content
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A :class:`CatalogCourse` or :class:`CourseRun` may exist with no content behind it: as a marketing or enrollment placeholder, as a planned future run, or because its content still lives in modulestore.

The converse guarantee does hold: if a course exists anywhere in the system, it exists as a :class:`CatalogCourse` and :class:`CourseRun` row.

2. The catalog app is not aware of content
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``openedx_content`` will have a table(s) for tracking the relationship between a :class:`CourseRun` and its content. But the catalog app itself is not aware of content, and does not maintain any relationship between enrollable things (course runs, pathways) and their content.

``openedx_content`` may import and hold foreign keys to ``openedx_catalog``. But ``openedx_catalog`` must never import ``openedx_content``.

``openedx_learning`` (Pathways, Competency-Based Education, and more) and other parts of the platform sit above both.

The resulting order, enforced by the ``src_layering`` contract in ``.importlinter``, is::

    openedx_learning > openedx_content > openedx_catalog > openedx_tagging

The general principle behind this is that changes in how content is represented should not require changes to the catalog app. For example, if we were to change from associating each course run with a :class:`LearningPackage` to associating each course run with an ``OutlineRoot`` in a :class:`LearningPackage` that contains multiple runs, that should not require changes to the catalog app, which would be the case if we used foreign keys from :class:`CourseRun` to :class:`LearningPackage` within the catalog app.

3. Catalog models are the canonical target for course foreign keys
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For performance and correctness, any Django model in this repository or in ``openedx-platform`` that needs to reference a course should do so with a foreign key to :class:`CourseRun` (or, rarely, :class:`CatalogCourse`), rather than by storing a course key string or pointing at ``CourseOverview`` (although much existing code does not yet follow this new convention).

On the other hand, public REST APIs and events should continue to identify courses by their full string course key and never expose the integer primary keys.

4. Catalog models stay minimal, unversioned, and extended by related models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Catalog's models like :class:`CatalogCourse` and :class:`CourseRun` carry only identity and a title. They are not versioned, unlike content. Additional course metadata (schedules, grading policy, enrollment options, pricing) will live in dedicated models with a ``ForeignKey`` or ``OneToOneField`` to :class:`CourseRun`, in this app or in others, following the same progressive-enhancement pattern as :ref:`openedx-content-adr-0002`. Whether a given metadata model is versioned, and how, is decided per model and is out of scope here.

Consequences
------------

- ``openedx_content`` will need a table(s) that associates course content with catalog's course runs, and which ensures that no more than one content outline can be associated with the same :class:`CourseRun`.
- Code must never assume that content, a ``CourseOverview``, or any other related model exists just because a catalog row does. Content-dependent behavior must check for the relationship and degrade gracefully.
- Deleting a learning package can never cascade into catalog entries, enrollments, or anything else that hangs off the catalog.
- A :class:`LearningPackage` can still be created and populated without yet being associated with a course/library/etc.
- Import Linter will fail any change that makes ``openedx_catalog`` import ``openedx_content``.

Rejected Alternatives
---------------------

**Another app joins content and catalog.** In this case, ``catalog`` and ``content`` would be wholly independent, prohibited from referencing each other. Another app, like ``openedx_learning``, ``openedx_courses``, or ``cms.contentstore`` would be layered on top and hold the records that associate each catalog with each course. This is a perfectly viable option, and is currently how content libraries are implemented. However, for now it seems simpler and more useful to put the mapping into the ``content`` app directly.

**Peer layering with cross-references.** In this case, we'd state that in general, :class:`LearningPackage` is context agnostic, and catalog models point to :class:`LearningPackage` rather than vice versa, but *within* ``openedx_content`` a new ``PathwayItem`` model allows references to ``CourseRun``. This is probably workable, but lacks the clean separation that we're looking for. It is also a package cycle: ``openedx_catalog`` imports ``openedx_content`` for :class:`LearningPackage` while ``openedx_content`` imports ``openedx_catalog`` for :class:`CourseRun`, which a ``layers`` contract in Import Linter cannot express at all. What's more, ``PathwayItem`` is only useful for the ``pathways`` app, which is presumably optional, so it's not as generic or reusable as the other models offered by ``openedx_content``.

**Catalog layers above the content.** In this case, ``openedx_catalog`` would hold a foreign key from :class:`CourseRun` to :class:`LearningPackage`, but any refactors to how content is stored (e.g. relationship to ``OutlineRoot`` instead of ``LearningPackage``) would require changing this foreign key, which shouldn't be the case.
