.. _openedx-content-adr-0012:

12. Course Learning Packages
============================

Status
------

Proposed.

Context
-------

``openedx_catalog`` models courses with two models: :class:`CatalogCourse`, a set of course runs (e.g. "Math 100"), and :class:`CourseRun`, an individual run of that catalog course (e.g. "Math 100 Fall2026").

This ADR decides how those two models map onto :class:`LearningPackage`, the top-level grouping for authored content in ``openedx_content``. This has important implications for how course [run] content and assets will be stored and organized as we transition from MongoDB to ``openedx_content``, especially for course re-runs. The two major alternatives are "one LearningPackage per course run" and "one LearningPackage per catalog course".

Constraints
~~~~~~~~~~~

Several properties of the existing ``openedx_content`` architecture constrain the design:

**Codes are unique within a learning package.** ``component_code`` is unique per ``(learning_package, component_type)``, and ``container_code`` is unique per learning package across *all* container types. Split modulestore preserves block IDs when a course is rerun, so two runs of the same course will systematically attempt to use the same codes.

**There is no copy-on-write.** As decided in :ref:`openedx-content-adr-0011`, a version belongs to exactly one entity, so two course runs cannot reference the same :class:`Component` with independently editable drafts, nor can two :class:`Component` instances share a :class:`ComponentVersion`.

**Media is deduplicated per learning package, in two independent ways.** :class:`Media` *rows* are unique on ``(learning_package, media_type, hash_digest)``, and :class:`Media` *blobs* are stored at ``content/{learning_package.uuid}/{hash_digest}``. Identical bytes in two learning packages are therefore two rows and two stored objects. The row scoping is deliberate: it supports per-package storage accounting, cleanup of unused data, and the principle that a learning package is self-contained and cannot be broken by the deletion of another one. The blob scoping was never separately justified and appears to have simply inherited the shape of the row constraint.

**Publishing is fine-grained within a learning package.** ``publish_all_drafts`` operates on a whole learning package, but ``publish_from_drafts`` accepts an arbitrary queryset of drafts, and publishing part of a package is in fact the most common approach.

The problem of content volume during reruns
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Course reruns are routine and frequent, and a rerun typically changes only a small fraction of the course. **We do not want reruns to produce a huge volume of new content that uses up more storage space than necessary**, especially expensive MySQL storage space. Three kinds of storage need to be considered separately, because they have different solutions and different costs:

- **Media file bytes**: uploaded asset files (images, PDFs, sometimes videos), stored externally on object storage such as S3. For an asset-heavy course this can potentially run to gigabytes.
- **Raw OLX bytes**: for performance reasons, the raw OLX of Components is stored in the MySQL database, in the ``Media.text`` column. On the order of a few megabytes per course including edit history: significant across many reruns, but small compared to asset files.
- **Rows**: the database rows (component, container, version, draft, published, media, relationships, ...) that describe the course structure and its edit history.

Decisions
---------

1. One LearningPackage per CourseRun
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each :class:`CourseRun` that has content in ``openedx_content`` gets its own :class:`LearningPackage`. A rerun gets a new learning package containing its own copy of the course's entities.

This is the decision from which most of the others follow. It is chosen for simplicity, predictability, strong isolation, and by rejecting the alternatives.

- Code collisions cannot occur, because each run's package is its own namespace. ``component_code`` can be the modulestore block ID directly, without any need for a prefixing scheme.
- The absence of copy-on-write is irrelevant, because no entity is ever referenced by two runs. Editing a component in one run cannot affect another.
- Drafts, publishing, deletion, and backup and restore work unchanged, per run.
- When a rerun is created, we can choose whether or not to copy the full edit history of the entities.

The cost is that reruns duplicate rows. This is accepted; see "Consequences".

2. A CatalogCourse has no LearningPackage of its own
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:class:`CatalogCourse` is a grouping of course runs for catalog, marketing and enrollment purposes. It holds no authored content and gains no relationship to ``openedx_content``.

Content shared deliberately between runs of a catalog course is expressed the same way as content shared between any two learning contexts: by copying it, optionally with an upstream link back to its source.

3. CourseRun holds the relationship
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:class:`CourseRun` gains a nullable, unique foreign key to :class:`LearningPackage`. It is nullable because a course run may exist purely as a marketing or enrollment placeholder, or may still have its content in modulestore. It is unique because the relationship is one-to-one. This is also exactly analogous to how the ``ContentLibrary`` model in openedx-platform stores a relationship to :class:`LearningPackage`.

This aligns with the `Proposed Catalog Models ADR`_, which states that the dependency runs from catalog to content, and the content applets are deliberately ignorant of what a package represents.

4. The package_ref of a course learning package is the course key
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``LearningPackage.package_ref`` is set to the string form of the run's course key, e.g. ``course-v1:MITx+Math100+2026Fall``. This mirrors content libraries, which already use ``package_ref=str(library_key)``. Both ``package_ref`` and course keys are globally unique, so this is consistent.

5. Reruns copy content but de-duplicate asset file storage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Creating a rerun copies the source run's entities into a new learning package. To avoid also duplicating the asset *bytes*, :class:`LearningPackage` will gains an immutable ``media_file_namespace`` string field, and :meth:`Media.path` becomes::

    content/{learning_package.media_file_namespace}/{hash_digest}

``media_file_namespace`` defaults to the **org ID** of the org that owns the content in the learning package, if it was known at the time the learning package was created. Existing rows are backfilled with the UUID of the learning package, so every existing :class:`Media` row computes an identical storage path after the migration. **No blobs move and no downtime is required.** The ``content/`` prefix is retained for the same backwards-compatibility reason.

When a learning package is created for a rerun, it copies the ``media_file_namespace`` of the previous run's learning package rather than generating a new one. All runs of a catalog course therefore share one namespace, and identical asset files are stored once across all of them. :meth:`Media.write_file` already returns without writing when a file of matching size exists at the target path, so deduplication happens automatically on write with no change to the media API.

In fact, as we are using the org ID as the default ``media_file_namespace`` moving forward, file storage will be de-duplicated on a per-org basis, not just a catalog course basis.

:class:`Media` *rows* remain scoped to a learning package: the ``(learning_package, media_type, hash_digest)`` constraint is unchanged, and each package has its own rows even when they resolve to a shared blob. This preserves per-package accounting, cascading cleanup on delete, and the borrowing-by-copy model. **No code may depend on two learning packages sharing a blob namespace; it is a storage optimization only.**

This addresses the "media file bytes" part of the volume problem in full. It does not address rows, and it does not cover OLX, which is stored as row text rather than as a file (see "Consequences").

6. Publishing, drafts and deletion are per run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Because a run owns its learning package outright, all of the existing package-level operations mean "this run" with no further qualification: ``publish_all_drafts``, ``get_all_drafts``, the draft and publish change logs, pruning, and backup and restore. No API in ``openedx_content`` needs to become aware of course runs.

7. Course structure and static assets are specified separately
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This ADR provides only the infrastructure needed to begin moving course content and assets from MongoDB into ``openedx_content``. How a course's files and uploads are represented within the run's learning package will be the subject of a future ADR. How the course outline (sections, subsections, units) maps onto containers, e.g. via an ``OutlineRoot`` and/or "selectors", will be described in a future ADR.

Consequences
------------

**Reruns duplicate rows, and this is accepted.** Each rerun creates its own entity, component, version, draft and published rows, plus media joins, for every component: on the order of tens of thousands of narrow rows for a large course. This is comparable to what split modulestore's structure documents already cost, and an acceptable price for the simplification it achieves. To minimize the duplication, in general the historic versions should not be copied, and only the latest version copied into the rerun of each course.

**Entity-level sharing across runs is permanently foreclosed.** Because a :class:`PublishableEntity` belongs to exactly one :class:`LearningPackage`, this decision cannot later be partially walked back to share entities between runs without revisiting the learning package boundary itself.

**OLX text is still duplicated per rerun.** ``media_file_namespace`` deduplicates file-backed media only; OLX is stored in the ``Media.text`` column with no file (``create_file=False``), so a rerun duplicates every component's OLX as row text. Deduplicating it would mean moving ``text`` into a separate table shared across a ``media_file_namespace`` and keyed on ``hash_digest`` alone. That is a substantial change with a potentially slow migration, and warrants its own ADR.

**Cross-run content identity is not established.** Two runs' copies of a component are unrelated rows that happen to resolve to the same blobs. Answering "is this run's copy still unmodified relative to the run it came from?" requires comparing media hashes rather than following a relation. If that capability is wanted (e.g. for "sync changes from the source run"), the ``PublishableEntityLink`` model that ``openedx-platform`` already uses for library-to-course sync extends naturally to run-to-run.

**No learning package becomes unusually large.** A catalog course with forty reruns produces forty ordinary packages rather than one very large one, so package-wide queries, exports and admin tooling stay within the size range that content libraries already exercise.

**Per-package storage usage becomes approximate.** Summing ``Media.size`` within a learning package over-counts real disk usage, because some bytes are shared with any other runs in the same org.

**Per-org storage usage can be easily tracked**: Since the underlying asset file data will be generally organized by org going forward, it should be easy to track asset storage usage on a per-org basis.

**Blob deletion requires reference counting within the namespace.** Deleting a learning package can no longer imply deleting everything under its storage prefix. Before deleting a blob, a future cleanup process must confirm that no other learning package sharing that ``media_file_namespace`` holds a :class:`Media` row with the same ``hash_digest``. (No blob cleanup has been implemented yet, so this does not affect any existing workflow.)

**Blast radius for blob storage bugs is bounded to the organization**: a corrupted or wrongly written object can affect other courses/content within the same organization, but never other organizations.

Rejected Alternatives
---------------------

A new model: LearningPackageFamily
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Conceptually, parts of :class:`LearningPackage` align with the ``LearningContext`` concept (a course or a library), while others align better with :class:`CatalogCourse`. Instead of picking one (as this ADR does), the concept could be split in two: a :class:`LearningPackage` (possibly renamed :class:`LearningContextPackage` or :class:`LearningContextContent`?) that is 1:1 with a learning context, and a :class:`LearningPackageFamily` analogous to :class:`CatalogCourse`, which could also group related libraries.

This is a compelling option with arguably more clarity, but it is a bigger change and likely not backwards-compatible in terms of API. It also slightly increases cognitive load. If we need to hang metadata off of the ``media_file_namespace`` in the future, it could make sense to implement :class:`LearningPackageFamily`.

Separate LearningPackage per run, with flexibility to combine them
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In this alternative approach, we would still normally have one LearningPackage per run as defined in this ADR. But the system would be designed to meet two goals:

- First, **related courses (catalog courses or runs of the same course, or both), pathways, and other things can be grouped into one LearningPackage**. This would be optional, but possible. For example, when a small team is working on a small set of courses and corresponding pathway(s), or when a developer wants to provide a single package to import onto a devstack that will unpack a set of courses, pathways, etc.

Secondly, either one of the following goals:

- **Multiple Content Libraries can also be backed by the same LearningPackage**, potentially alongside courses and pathways. Or:
- **Any LearningPackage can be treated as a content library**, allowing advanced users to inspect the course outline, components, pathway items, static assets, and any other content within the package using the content library UI. This is a cool option and much more compatible with the existing content libraries implementation but it is not compatible with the alternative goal of allowing multiple libraries in a shared learning package (alongside courses etc.).

Regardless of which approach to libraries is taken, the implementation details required for this include:

- Some sort of ``Course`` / ``OutlineRoot`` entity which exists in the :class:`LearningPackage` for each course run.
- The catalog's :class:`CourseRun` no longer has a ForeignKey to :class:`LearningPackage`, but instead to the ``OutlineRoot`` object, which can be used to determine the :class:`LearningPackage`. Runs and learning packages are no longer necessarily 1:1.
- The ``component_code`` and ``container_code`` for each course run's entities would not directly match the code in the user-visible usage key. Some kind of "usage table" or prefixing scheme would be required. If it's a usage table, it would have to be considered part of the content and exported along with the learning package content, to avoid breakage via import/export cycles.

We are trying to leave this open as a future option, but we are rejecting it for now because:

- The representation of each course run's outline, settings, grading policy, pages, etc. within the LearningPackage has yet to be defined (that will be the subject of future ADRs). We cannot properly plan namespacing and isolation without knowing what the entities involved are, or introducing a new ``scope`` concept (see next rejected alternative).
- Isolation of each LearningPackage is very strong at the moment, but changing to this system requires all content-related APIs to be modified to prevent bugs where edits in one course run inadvertently affect another, or users with permission to edit one course run can deliberately edit another in the same LearningPackage. (Since permissions are defined at the Learning Context level, not the Learning Package level.)
- There are a lot of open questions around this idea.

One LearningPackage per CatalogCourse, with run-scoped entities
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In this alternative, all runs of a catalog course share one learning package; a new ``scope`` column on :class:`PublishableEntity` records which run each entity belongs to, with a null scope meaning "shared across runs". A rerun creates only the container spine, pointing at the previous run's components pinned to their published versions, and forks a component into its own scope on first edit.

This addresses row duplication as well as byte duplication, but the cost is spread across the whole system:

- It adds a column to :class:`PublishableEntity`, the most heavily used table in the schema, and changes its uniqueness constraints.
- Cross-run references must be *pinned*, or one run's edits would silently alter another. That is a new invariant to enforce in entity list construction, and a subtle and damaging bug class if it is ever violated.
- Fork-on-write machinery is needed in the components and containers APIs.
- Publishing, pruning and deletion all become scope-aware.
- A catalog course with many reruns produces a single very large package.
- CCX courses, which share an org, code and run, do not fit the scoping model cleanly.

The row savings are valuable, but the complexity and potential for cross-run data problems (reduced isolation) don't seem worth the cost.

One LearningPackage per CatalogCourse, with run-prefixed codes and full copies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This approach would be by far the simplest way to use one learning package for all runs of each catalog course, and seems to be the model that some of our earlier documentation anticipated. There is no scope column and no fork-on-write: a rerun makes a full copy of the source run's containers and components inside the same package, and code collisions are avoided by prefixing every ``component_code`` and ``container_code`` with the run, e.g. ``2026Fall.problem_abc123`` (``code_field`` forbids ``:`` and ``/``, so the separator must be chosen from ``[\w.-]`` and escaped in both the run codes and block IDs).

Compared to decision 1 this requires no ``media_file_namespace`` field, because :class:`Media` rows and blobs are already deduplicated within a package, so identical assets and OLX text blobs across runs are stored once at both the row and the blob level. It does not save any entity rows: a rerun creates exactly the same entity, version, draft and published rows as under decision 1, just in a shared package.

We rejected it because it pays most of the costs of the run-scoped alternative for a smaller benefit than ``media_file_namespace`` delivers on its own:

- Every package-level operation that decision 6 gets for free becomes run-aware by prefix filtering: publishing one run, listing its drafts, reading its change logs, pruning it, exporting it, and deleting it. Deleting a run is a filtered bulk delete rather than a cascade, and ``backup_restore`` cannot export a single run without new filtering support.
- Codes stop being opaque. Every lookup, URL (see :ref:`openedx-content-adr-0005`), import, export and upstream link must compose and parse the prefix, which is contrary to the identifier conventions in :ref:`openedx-content-adr-0003` and leaks the run into every entity reference.
- A catalog course with many reruns produces a single very large package, and anything keyed on a package (collections, selectors, package-wide queries, admin tooling) spans all of its runs.
- The chance of changes to one run impacting another run due to isolation bugs in the code are significantly increased when all runs share a LearningPackage.

One LearningPackage per CatalogCourse, with entities shared unpinned
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Runs referencing the same components without pinning is not viable at all. A component has one draft and one published version, so editing a component while working on the Autumn run would immediately change the Spring run. Sharing version rows between components instead is copy-on-write, rejected in :ref:`openedx-content-adr-0011`.

Global content-addressed storage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The simplest way to deduplicate blobs is to drop the namespace entirely and store everything at ``content/{hash_digest}``. We rejected this in favor of org-level deduplication for several reasons:

- Different orgs are unlikely to have byte-identical asset files, so there is not much to gain from de-duplicating asset file bytes across organizations.
- Isolating each tenant's data is expected for multi-tenant systems and provides better robustness, isolation, and performance.
- Having an org-level separation makes it more obvious to operators when a certain org is using a disproportionate amount of resources.
- Garbage collecting unused asset files is more performant when each org's data can be considered separately, instead of needing to perform a global reference count over every learning package on the site.

Accepting duplicate asset bytes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Doing nothing is defensible on the grounds that object storage is inexpensive, especially compared with MongoDB or MySQL storage. We rejected it because the duplication is unbounded in the number of reruns and entirely avoidable, and because avoiding it requires no data migration.

Open Questions
--------------

**CCX courses.** CCX course runs share an org, course code and run with their parent, and use ``ccx-v1:`` keys; :class:`CourseRun` already carries an exception in its uniqueness constraint for them. Whether a CCX gets its own learning package, shares its parent's, or is not represented in ``openedx_content`` at all is not settled by this ADR.

**Courses in libraries.** The idea of having courses as top-level objects that exist within libraries has been proposed. Assuming the courses cannot be accessed by learners while they live only within the library, this proposal doesn't rule that out, but certainly complicates it.

**Block ID stability through migration.** Decision 1 is satisfied regardless of block IDs, but blob deduplication (and any future OLX deduplication) depends on the modulestore migrator producing byte-identical output for unchanged content across runs. Content libraries strip the ``url_name`` attribute before storing OLX, precisely because instance identity is carried by the component key; the future course content migrator will need to do the same.

**Where run-level course metadata lives.** :class:`CourseRun`'s docstring anticipates models such as ``CourseSchedule`` and ``CourseGradingPolicy``, versioned either as publishable entities or with ``django-simple-history``. Whether any of those become publishable entities inside the run's learning package, and therefore participate in its publishing lifecycle, is left to a later decision.

.. _Proposed Catalog Models ADR: https://github.com/openedx/openedx-core/pull/818
