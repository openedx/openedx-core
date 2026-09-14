.. _openedx-catalog-adr-0001:

1. Role of Catalog's CourseRun and CatalogCourse Models
=======================================================

Status
------

Draft

Context
-------

``openedx_catalog`` holds the core models that say which courses exist in an instance: :class:`CatalogCourse` (a set of runs, e.g. "Math 100") and :class:`CourseRun` (one run, e.g. "Math 100 2026Fall"). ``openedx_content`` holds the authored, versioned material itself, grouped into :class:`LearningPackage` instances. This ADR clarifies how the catalog models are meant to be used.

Until now the direction of the relationship between the two apps has been left open ("TBD" in the catalog architecture diagram), and two proposals have pulled in opposite directions:

- The proposed `Course Learning Packages ADR`_ gives :class:`CourseRun` a foreign key to :class:`LearningPackage`, which requires the catalog to import content.
- :ref:`openedx-learning-adr-0007` stated that ``openedx_content`` knows about ``openedx_catalog`` and never the reverse, so that Pathway Items can reference course runs directly.

Meanwhile, ``ContentLibrary`` in ``openedx-platform`` already points at :class:`LearningPackage` from the outside, and ``openedx_learning`` is already layered above ``openedx_content`` in ``.importlinter``.

Separately, admins can now create catalog courses and course runs before any content exists, and the rest of the system needs a clear rule about what that implies.

Decisions
---------

1. The catalog layers above content
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``openedx_catalog`` may import and hold foreign keys to ``openedx_content``. But ``openedx_content`` must never import ``openedx_catalog``.

``openedx_learning`` (Pathways, Competency-Based Education, and more) sits above both.

The resulting order, enforced by the ``src_layering`` contract in ``.importlinter``, is::

    openedx_learning > openedx_catalog > openedx_content > openedx_tagging

The general rule behind this ordering is: **a context model points at its content; content never points at contexts.** ``openedx_content`` is generic infrastructure used by courses, libraries, pathways and future context types, and its applets are deliberately ignorant of what a learning package represents. A course run, a library, or a pathway is the thing that knows which package (or which container within a package) holds its content, in exactly the way ``ContentLibrary`` already does.

This does not contradict the intent of :ref:`openedx-learning-adr-0007`, whose real requirements are that the versioned Pathway definition holds the references to the unversioned catalog objects. Those definition models live in ``openedx_learning``, above the catalog, so they can reference :class:`CourseRun` and content freely. Decision 4 of that ADR has been amended to name ``openedx_learning`` rather than ``openedx_content`` as the side that knows about the catalog.

2. Catalog entries may be placeholders with no content
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A :class:`CatalogCourse` or :class:`CourseRun` may exist with no content behind it: as a marketing or enrollment placeholder, as a planned future run, or because its content still lives in modulestore.

The converse guarantee does hold: if a course exists anywhere in the system, it exists as a :class:`CatalogCourse` and :class:`CourseRun` row.

3. Catalog models are the canonical target for course foreign keys
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For performance and correctness, any Django model in this repository or in ``openedx-platform`` that needs to reference a course should do so with a foreign key to :class:`CourseRun` (or, rarely, :class:`CatalogCourse`), rather than by storing a course key string or pointing at ``CourseOverview`` (although much existing code does not yet follow this new convention).

On the other hand, public APIs and events should continue to identify courses by their full string course key and never expose the integer primary keys.

4. Catalog models stay minimal, unversioned, and extended by related models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:class:`CatalogCourse` and :class:`CourseRun` carry only identity and a title. They are not versioned, unlike content. Additional course metadata (schedules, grading policy, enrollment options, pricing) will live in dedicated models with a ``ForeignKey`` or ``OneToOneField`` to :class:`CourseRun`, in this app or in others, following the same progressive-enhancement pattern as :ref:`openedx-content-adr-0002`. Whether a given metadata model is versioned, and how, is decided per model and is out of scope here.

Consequences
------------

- Models related to pathway contents will not be added to ``openedx_content`` but rather will live in ``openedx_learning``. This makes sense, as Pathway details are only useful for implementing Pathways, and are not a generic primitive like ``Component`` that is used in multiple contexts.
- Every relationship from the catalog to content is nullable.
- Code must never assume that content, a ``CourseOverview``, or any other related model exists just because a catalog row does. Content-dependent behavior must check for the relationship and degrade gracefully.
- ``openedx_content`` needs no course-, library- or pathway-aware code, and stays reusable by any context type.
- Looking up a run's content is a direct key lookup from the catalog side. Looking up which course/library/etc. a package belongs to is a reverse query (potentially checking multiple tables, e.g. both ``CourseRun`` and ``ContentLibrary``), which is acceptable because it is an uncommon use case.
- Deleting a learning package can never cascade into catalog entries, enrollments, or anything else that hangs off the catalog.
- A :class:`LearningPackage` can be created and populated without yet being associated with a course/library/etc.
- Import Linter will fail any change that makes ``openedx_content`` import ``openedx_catalog``, including a Pathways applet that references :class:`CourseRun` if it is placed inside ``openedx_content``. Such models belong in ``openedx_learning``.

Rejected Alternatives
---------------------

**Peer layering with cross-references.** In this case, we'd state that in general, :class:`LearningPackage` is context agnostic, and catalog models point to :class:`LearningPackage` rather than vice versa, but *within* ``openedx_content`` a new ``PathwayItem`` model allows references to ``CourseRun``. This is probably workable, but lacks the clean separation that we're looking for. It is also a package cycle: ``openedx_catalog`` imports ``openedx_content`` for :class:`LearningPackage` while ``openedx_content`` imports ``openedx_catalog`` for :class:`CourseRun`, which a ``layers`` contract in Import Linter cannot express at all. What's more, ``PathwayItem`` is only useful for the ``pathways`` app, which is presumably optional, so it's not as generic or reusable as the other models offered by ``openedx_content``.

**Content layers above the catalog.** In this case, ``openedx_content`` would need to hold some mechanism for mapping from :class:`LearningPackage` (or a root container) to :class:`CourseRun` (and presumably to :class:`ContentLibrary`), either hard-coding awareness of "courses", "libraries" and "pathways", or using a polymorphic context registry. This makes the generic content layer aware of one specific context type, and offers no way to treat libraries or pathways the same way without also moving them below content, which is impossible for ``ContentLibrary`` in ``openedx-platform``.

.. _Course Learning Packages ADR: https://github.com/openedx/openedx-core/pull/812
