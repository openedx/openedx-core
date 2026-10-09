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

Guiding principle: no 1:1 assumptions between contexts and packages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The decisions below establish one :class:`LearningPackage` per :class:`CourseRun` as the *convention* for regular courses. They deliberately do not make it a guarantee that the rest of the system may rely on.

It will always be true that any given course resolves to a single :class:`LearningPackage`, and a single XBlock usage in a course resolves to a single :class:`PublishableEntity`, but the opposite must not be assumed. A single :class:`LearningPackage` may be used by several different learning contexts, and a single :class:`PublishableEntity` may likewise appear in different learning contexts. In other words, these relationships must not be assumed to be 1:1.

We want to support use cases where each :class:`LearningPackage` provides one set of content, but different "views" into that content can be realized as different learning contexts:

- Standalone enrollable sections, subsections, and units, which would be learning contexts in their own right but whose content is a subset of the content in a larger course / :class:`LearningPackage`.
- Library items served as an LTI tool, where the LTI launch is the learning context and the content lives in the library's :class:`LearningPackage`.
- CCX, rebuilt more simply on top of ``openedx_content``, where each CCX is a learning context that reuses its parent course's content, and shares the same :class:`LearningPackage`.

We are not currently planning to allow multiple course runs or content from unrelated courses to coexist in one learning package (see "Rejected Alternatives"). However, we want to keep even that open as a long-term option if we decide to implement the required additional layer of indirection between Usage and :class:`PublishableEntity` (also between Course Run and :class:`LearningPackage`) in the future.

1. One LearningPackage per CourseRun, by convention
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each :class:`CourseRun` that has content in ``openedx_content`` gets its own corresponding :class:`LearningPackage`. A rerun gets a new learning package containing its own copy of the course's entities.

This is the decision from which most of the others follow. It is chosen for simplicity, predictability, strong isolation, and by rejecting the alternatives.

- Code collisions cannot occur, because each run's package is its own namespace. ``component_code`` can be the modulestore block ID directly, without any need for a prefixing scheme. (Usage keys are nonetheless obtained through the mapping API rather than composed from the code; see decision 5.)
- The absence of copy-on-write is irrelevant, because no entity is ever referenced by two runs. Editing a component in one run cannot affect another.
- Drafts, publishing, deletion, and backup and restore work unchanged, per run.
- When a rerun is created, we can choose whether or not to copy the full edit history of the entities.

The cost is that reruns duplicate rows. This is accepted; see "Consequences".

2. A CatalogCourse has no LearningPackage of its own
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:class:`CatalogCourse` is a grouping of course runs for catalog, marketing and enrollment purposes. It holds no authored content and gains no relationship to ``openedx_content``.

Content shared deliberately between runs of a catalog course is expressed the same way as content shared between any two learning contexts: by copying it, optionally with an upstream link back to its source.

3. CourseContent holds the relationship
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A django model in ``openedx_content``, called ``CourseContent``, will hold the mapping between ``CourseRun`` and ``LearningPackage``. The ``CourseRun`` field will be unique, such that each run is associated with zero or one ``LearningPackage``, never multiple.

The ``LearningPackage`` field will **not** be unique: the data model permits multiple ``CourseContent`` rows, and therefore multiple course runs, to point at the same ``LearningPackage``. This ADR does not define what such sharing would mean for publishing, permissions or usage keys, and the course rerun workflow will not produce it. It exists so that nothing (including the schema) quietly comes to depend on the relationship being 1:1. A unit test will create two ``CourseContent`` rows referencing one ``LearningPackage``, to keep that possibility from being constrained away accidentally.

To emphasize that ``CourseContent`` should not be used as the canonical "Course" model nor a target for foreign keys (``CourseRun`` should be used), the foreign key to ``CourseRun`` can double as the primary key for ``CourseContent``.

Code that needs a course run's learning package must look it up through ``CourseContent``, and code that needs the course run(s) for a learning package must query ``CourseContent`` and handle receiving more than one.

4. The package_ref of a course learning package is built using the course key
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When a learning package is created for a course run, its ``LearningPackage.package_ref`` should be set to a human readable string that includes the run's course key, e.g. ``MATH 100 (course-v1:MITx+Math100+2026Fall)``. Because ``package_ref`` must be globally unique, it makes sense to encode the course key to ensure uniqueness. But we don't want code to assume that the ``package_ref`` is 1:1 with the course key, so the course's title or some random hash should be included as well. Details are left to the implementation or future ADRs.

**No code may derive a course key from a** ``package_ref`` **or look up a course's learning package by** ``package_ref``; it must go through ``CourseContent`` (decision 3).

5. Usage keys are mapped by an API, not derived from codes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Usage keys are **not** to be derived directly from learning-package-local identifiers such as ``entity_ref``, ``component_code`` or ``container_code``, even when, under the conventions above, a straightforward derivation would give the right answer. Instead, there will be an API function along the lines of::

    get_entity_usage_key(learning_context, publishable_entity) -> UsageKey

which owns the mapping between ``(learning context, publishable entity)`` and usage key, along with its inverse for resolving a usage key to an entity. For course runs created per decision 1, its implementation can initially be the obvious one (the course key plus the entity's type and code). Because callers never compose the key themselves, logic can later be added there for standalone containers, LTI, CCX, or multiple runs in one package (e.g. a prefix, a collection-scoped code, or a usage table) without changing other code.

Note that the function takes the learning context as an argument: the same entity may have different usage keys in different learning contexts, and not every entity in a package necessarily has a usage key in a given context.

6. Reruns copy content but de-duplicate asset file storage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Creating a rerun copies the source run's entities into a new learning package. To avoid also duplicating the asset *bytes*, :class:`LearningPackage` will gain an immutable ``media_file_namespace`` string field, and :meth:`Media.path` becomes::

    content/{learning_package.media_file_namespace}/{hash_digest}

``media_file_namespace`` defaults to the **org code** of the org that owns the content in the learning package (e.g. `MITx`), if it was known at the time the learning package was created. Existing rows are backfilled with the UUID of the learning package, so every existing :class:`Media` row computes an identical storage path after the migration. **No blobs move and no downtime is required.** The ``content/`` prefix is retained for the same backwards-compatibility reason.

When a learning package is created for a rerun, it copies the ``media_file_namespace`` of the previous run's learning package rather than generating a new one. All runs of a catalog course therefore share one namespace, and identical asset files are stored once across all of them. :meth:`Media.write_file` already returns without writing when a file of matching size exists at the target path, so deduplication happens automatically on write with no change to the media API.

In fact, as we are using the org code as the default ``media_file_namespace`` moving forward, file storage will be de-duplicated on a per-org basis, not just a catalog course basis.

:class:`Media` *rows* remain scoped to a learning package: the ``(learning_package, media_type, hash_digest)`` constraint is unchanged, and each package has its own rows even when they resolve to a shared blob. This preserves per-package accounting, cascading cleanup on delete, and the borrowing-by-copy model. **No code may depend on two learning packages sharing a blob namespace; it is a storage optimization only.**

This addresses the "media file bytes" part of the volume problem in full. It does not address rows, and it does not cover OLX, which is stored as row text rather than as a file (see "Consequences").

7. Publishing, drafts and deletion are per run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Because each run created by the rerun workflow has a learning package of its own, all of the existing package-level operations mean "this run" with no further qualification: ``publish_all_drafts``, ``get_all_drafts``, the draft and publish change logs, pruning, and backup and restore. No API in ``openedx_content`` needs to become aware of course runs in order to preserve isolation and avoid cross-run write bugs.

If a learning package is ever shared by multiple learning contexts (see decision 3), these package-level operations would no longer be scoped to a single context, and the workflow that creates such sharing would be responsible for defining appropriate scoping. That is out of scope for this ADR.

8. Course structure and static assets are specified separately
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This ADR provides only the infrastructure needed to begin moving course content and assets from MongoDB into ``openedx_content``. How a course's files and uploads are represented within the run's learning package will be the subject of a future ADR. How the course outline (sections, subsections, units) maps onto containers, e.g. via an ``OutlineRoot`` and/or "selectors", will be described in a future ADR.

Consequences
------------

**Reruns duplicate rows, and this is accepted.** Each rerun creates its own entity, component, version, draft and published rows, plus media joins, for every component: on the order of tens of thousands of narrow rows for a large course. This is comparable to what split modulestore's structure documents already cost, and an acceptable price for the simplification it achieves. To minimize the duplication, in general the historic versions should not be copied, and only the latest version copied into the rerun of each course.

**Entity-level sharing across runs is not provided, but not foreclosed.** Runs created by the rerun workflow never share entities. Because the data model permits several course runs (or other learning contexts) to reference one learning package (decision 3), and usage keys are mapped by an API rather than derived from codes (decision 5), a future ADR could introduce such sharing without migrating existing data or changing callers that follow these rules.

**Developers must not take the 1:1 shortcuts.** The conventions in decisions 1 and 4 hold for every course run this ADR creates, so code that assumes them will appear to work. Reviewers should treat composing a usage key from an entity's code, or mapping between ``package_ref`` and course key, as a bug.

**OLX text is still duplicated per rerun.** ``media_file_namespace`` deduplicates file-backed media only; OLX is stored in the ``Media.text`` column with no file (``create_file=False``), so a rerun duplicates every component's OLX as row text. Deduplicating it would mean moving ``text`` into a separate table shared across a ``media_file_namespace`` and keyed on ``hash_digest`` alone. That is a substantial change with a potentially slow migration, and warrants its own ADR.

**Cross-run content identity is not established.** Two runs' copies of a component are unrelated rows that happen to resolve to the same blobs. Answering "is this run's copy still unmodified relative to the run it came from?" requires comparing media hashes rather than following a relation. If that capability is wanted (e.g. for "sync changes from the source run"), the ``PublishableEntityLink`` model that ``openedx-platform`` already uses for library-to-course sync extends naturally to run-to-run.

**No learning package becomes unusually large.** A catalog course with forty reruns produces forty ordinary packages rather than one very large one, so package-wide queries, exports and admin tooling stay within the size range that content libraries already exercise.

**Per-package storage usage becomes approximate.** Summing ``Media.size`` within a learning package over-counts real disk usage, because some bytes are shared with any other runs in the same org.

**Per-org storage usage can be easily tracked**: Since the underlying asset file data will be generally organized by org going forward, it should be easy to track asset storage usage on a per-org basis.

**Blob deletion requires reference counting within the namespace.** Deleting a learning package can no longer imply deleting everything under its storage prefix. Before deleting a blob, a future cleanup process must confirm that no other learning package sharing that ``media_file_namespace`` holds a :class:`Media` row with the same ``hash_digest``. (No blob cleanup has been implemented yet, so this does not affect any existing workflow.)

**Blast radius for blob storage bugs is bounded to the organization**: a corrupted or wrongly written object can affect other courses/content within the same organization, but never other organizations.

**The Library Restore workflow may need changes.** With the current "restore library" from backup UI flow, we stage the content (including media) before the user chooses a target ``org`` and ``library_code``. That means that we cannot set the ``media_file_namespace`` of the new ``LearningPackage`` correctly. We'll need to either: (a) ask for the org before staging the content (would be a UX change); (b) stage media file under a temporary path, and then rename and de-dupe the staged media files after the user picks an org; or (c) just continue to use the old-style ``content/{package_uuid}`` media namespace for restored libraries, at the cost of no org-wide deduplication.

Rejected Alternatives
---------------------

A new model: LearningPackageFamily
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Conceptually, parts of :class:`LearningPackage` align with the ``LearningContext`` concept (a course or a library), while others align better with :class:`CatalogCourse`. Instead of picking one (as this ADR does), the concept could be split in two: a :class:`LearningPackage` (possibly renamed :class:`LearningContextPackage` or :class:`LearningContextContent`?) that is 1:1 with a learning context, and a :class:`LearningPackageFamily` analogous to :class:`CatalogCourse`, which could also group related libraries.

This is a compelling option with arguably more clarity, but it is a bigger change and likely not backwards-compatible in terms of API. It also slightly increases cognitive load. If we need to hang metadata off of the ``media_file_namespace`` in the future, it could make sense to implement :class:`LearningPackageFamily`.

Separate LearningPackage per run, with flexibility to combine them
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In this alternative approach, we would still normally have one LearningPackage per run as defined in this ADR. But the system would be designed to meet two goals:

- First, **related courses (with different content, e.g. catalog courses or runs of the same course, or both), pathways, and other things can be grouped into one LearningPackage**. This would be optional, but possible. For example, when a small team is working on a small set of courses and corresponding pathway(s), or when a developer wants to provide a single package to import onto a devstack that will unpack a set of courses, pathways, etc.

Secondly, either one of the following goals:

- **Multiple Content Libraries can also be backed by the same LearningPackage**, potentially alongside courses and pathways. Or:
- **Any LearningPackage can be treated as a content library**, allowing advanced users to inspect the course outline, components, pathway items, static assets, and any other content within the package using the content library UI. This is a cool option and much more compatible with the existing content libraries implementation but it is not compatible with the alternative goal of allowing multiple libraries in a shared learning package (alongside courses etc.).

Regardless of which approach to libraries is taken, the implementation details required for this include:

- Some sort of ``Course`` / ``OutlineRoot`` entity which exists in the :class:`LearningPackage` for each course run.
- Each catalog's :class:`CourseRun` is no longer mapped 1:1 with :class:`LearningPackage`, but instead to the ``OutlineRoot`` object, which can be used to determine the :class:`LearningPackage`.
- The ``component_code`` and ``container_code`` for each course run's entities would not directly match the code in the user-visible usage key. Some kind of "usage table" or prefixing scheme would be required. If it's a usage table, it would have to be considered part of the content and exported along with the learning package content, to avoid breakage via import/export cycles.
- We would likely also leverage **Collections**, such that each course run within a learning packages has all of its content within a collection. This would work especially well with the ability to view the whole package as a library, as it would keep each course run's content separate in the library UI. The run-specific usage codes could be stored in a new field on ``CollectionPublishableEntity``, although a prefixing scheme or usage table could also be used.

We are rejecting this for now, but deliberately leaving it open as a future option: decisions 3, 4 and 5 ensure that the data model, ``package_ref`` conventions, and usage key lookups do not rule it out. We are not adopting it now because:

- We don't yet have a clear definition of any user-facing feature that would necessitate this.
- The representation of each course run's outline, settings, grading policy, pages, etc. within the LearningPackage has yet to be defined (that will be the subject of future ADRs). We cannot properly plan namespacing and isolation without knowing what the entities involved are, or introducing a new ``scope`` concept (see next rejected alternative).
- Isolation of each LearningPackage is very strong at the moment, but changing to this system requires all content-related APIs to be modified to prevent bugs where edits in one course run inadvertently affect another, or users with permission to edit one course run can deliberately edit another in the same LearningPackage. (Since permissions are defined at the Learning Context level, not the Learning Package level.)

One LearningPackage per CatalogCourse, with run-scoped entities
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In this alternative, all runs of a catalog course share one learning package; a new ``scope`` column on :class:`PublishableEntity` records which run each entity belongs to.

This approach is rejected because:
- It adds a column to :class:`PublishableEntity`, the most heavily used table in the schema, and changes its uniqueness constraints.
- All APIs require a breaking change anywhere they previously assumed that (``learning_package``, ``entity_ref``) was sufficient as a unique identifier.
- Publishing, pruning and deletion all need to become scope-aware, and care must be taken throughout all content-related APIs to avoid cross-scope bugs (where editing one run affects another).
- A catalog course with many reruns produces a very large package in terms of rows.
- CCX courses, which share an org, code and run, do not fit the scoping model cleanly.

The row savings are valuable, but the complexity and potential for cross-run data problems (reduced isolation) don't seem worth the cost.

In a variant of this idea, a rerun creates only the container spine, pointing at the previous run's components pinned to their published versions. When any component is first edited within the new run, the component must be forked into its own scope. This is a somewhat different mechanism than those rejected in :ref:`openedx-content-adr-0011`, but has similar issues and is of limited value; it saves rows but increases complexity significantly. It is an interesting use of "pinned containers", however, which are otherwise not really used at all.

One LearningPackage per CatalogCourse, with run-prefixed codes and full copies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This approach would be by far the simplest way to use one learning package for all runs of each catalog course, and seems to be the model that some of our earlier documentation anticipated. There is no scope column and no fork-on-write: a rerun makes a full copy of the source run's containers and components inside the same package, and code collisions are avoided by prefixing every ``component_code`` and ``container_code`` with the run, e.g. ``2026Fall.problem_abc123`` (``code_field`` forbids ``:`` and ``/``, so the separator must be chosen from ``[\w.-]`` and escaped in both the run codes and block IDs).

Compared to decision 1 this requires no ``media_file_namespace`` field, because :class:`Media` rows and blobs are already deduplicated within a package, so identical assets and OLX text blobs across runs are stored once at both the row and the blob level. It does not save any entity rows: a rerun creates exactly the same entity, version, draft and published rows as under decision 1, just in a shared package.

We rejected it because it pays most of the costs of the run-scoped alternative for a smaller benefit than ``media_file_namespace`` delivers on its own:

- Every package-level operation that decision 7 gets for free becomes run-aware by prefix filtering: publishing one run, listing its drafts, reading its change logs, pruning it, exporting it, and deleting it. Deleting a run is a filtered bulk delete rather than a cascade, and ``backup_restore`` cannot export a single run without new filtering support.
- Codes stop being opaque. Every lookup, URL (see :ref:`openedx-content-adr-0005`), import, export and upstream link must compose and parse the prefix, which is contrary to the existing assumption that these identifiers are opaque.
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

**CCX courses.** CCX course runs share an org, course code and run with their parent, and use ``ccx-v1:`` keys; :class:`CourseRun` already carries an exception in its uniqueness constraint for them. Whether a CCX gets its own learning package, shares its parent's, or is not represented in ``openedx_content`` at all is not settled by this ADR. Sharing the parent's package is permitted by the data model (decision 3), and the usage key mapping (decision 5) is the place where CCX-specific keys would be produced.

**Courses in libraries.** The idea of having courses as top-level objects that exist within libraries has been proposed. Assuming the courses cannot be accessed by learners while they live only within the library, this proposal doesn't rule that out, but certainly complicates it.

**Block ID stability through migration.** Decision 1 is satisfied regardless of block IDs, but blob deduplication (and any future OLX deduplication) depends on the modulestore migrator producing byte-identical output for unchanged content across runs. Content libraries strip the ``url_name`` attribute before storing OLX, precisely because instance identity is carried by the component key; the future course content migrator will need to do the same.

**Where run-level course metadata lives.** :class:`CourseRun`'s docstring anticipates models such as ``CourseSchedule`` and ``CourseGradingPolicy``, versioned either as publishable entities or with ``django-simple-history``. Whether any of those become publishable entities inside the run's learning package, and therefore participate in its publishing lifecycle, is left to a later decision.
