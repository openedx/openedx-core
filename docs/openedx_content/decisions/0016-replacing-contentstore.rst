.. _openedx-content-adr-0016:

16. Replacing Contentstore
==========================

Status
------

Draft.

Context
-------

Course "Files" are stored in the legacy MongoDB contentstore (GridFS). :ref:`openedx-content-adr-0013` defines how course Files can now be stored in ``openedx_content`` instead. This ADR is the high-level plan for migrating every course from contentstore to the new system, and then removing contentstore.

Goals
~~~~~

Replacing contentstore gives us:

* One less use case for MongoDB, which we are working to remove from our stack.
* One storage system (``openedx_content``) for all authored files, unified across courses and libraries.
* Versioned files with draft/publish and history.
* More reliable "where is this file used?" reporting, and "missing file" detection (:ref:`openedx-content-adr-0014`).
* Files attached directly to components, which simplifies copying and syncing library content into courses.
* File bytes deduplicated within orgs, for more efficient storage.
* More efficient file serving - content served by the reverse proxy, rather than streamed from Mongo by Django.
* A new ``private`` mode for staff-only course files.

Decisions
---------

1. Initial roll-out will be a per-course migration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For the next release or two, each course run will be in one of two states: **legacy** (course files in contentstore) or **migrated** (course files in ``openedx_content``). A course is migrated when it has a ``CourseContent`` / ``CourseRunLearningPackage`` row (:ref:`openedx-content-adr-0012`). There is no mixed state, nor write-through.

Rollout:

* **Willow:** opt-in, with a system-level setting controlling whether new courses should use the legacy or migrated mode, plus a tool/command for migrating courses either individually or in bulk.
* **Xylon:** new courses (and reruns of migrated courses) are created as migrated by default. Operators are told to migrate existing courses.
* **Yucca:** contentstore is removed, and any remaining courses must be migrated.

2. The initial implementation will replicate the contentstore API
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The initial implementation of ADRs 13-15 will focus on implementing the existing ``contentstore`` API, by wrapping it in a routing layer that directs all requests to either (A) the existing ``contentstore`` GridFS implementation, or (B) a compatibility shim that implements the same ``contentstore`` python API using new ``openedx_content`` shared assets (:ref:`openedx-content-adr-0013`) instead of GridFS.

This means that the initial implementation will not support draft/publish (everything will be auto-published as part of any CRUD operation on shared assets), nor :ref:`openedx-content-adr-0015`.

The OLX tarball format stays exactly the same initially.

3. Migration is a per-course command
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A management command migrates **one course run at a time**:

1. Mark the course read-only for file writes (at the new routing layer per decision 2). During this time, any contentstore write operations will result in an error. While read-only, a warning should be shown in Studio on the course Files page, and importing over that course should also be blocked.
2. Create a new Learning Package for the course assets.
3. Copy every file to an Asset (following the special cases in :ref:`openedx-content-adr-0013`, decision 4), and auto-publish it.
4. Verify the result (count, sizes and hashes match).
5. Create the ``CourseContent`` / ``CourseRunLearningPackage`` entry to point to the new Learning Package, which switches the routing layer over. Remove the read-only restriction.

The command should be idempotent and can be re-run after a failure. It should accept a list of one or more course runs and then migrate each of them in sequence.

The original contentstore/GridFS data for migrated courses is left in place (but unused) until contentstore is removed. If necessary, a course can be rolled back, but doing so will lose any asset changes made in the new system following the migration. (If necessary, we *could* implement a reverse command that copies newer assets from the new system back to contentstore, or a write-through system that applies edits to both, but it is hoped that this will not be necessary.)

4. Phase 2 (Xylon): Publishing, usage, and per-component assets with shadow components
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Once the first phase (decisions 1-3) is stable and working well, the course Studio UI can be upgraded to support new features in migrated courses and libraries, using the ``openedx_content`` APIs directly:

* Storing shared assets in libraries, in a similar manner to Components
* Referencing shared assets from Components in libraries
* Better tracking of asset usage and missing asset references on the course files page
* Per-component files attached to XBlocks in courses (using :ref:`openedx-content-adr-0015`)

The course OLX export format must be expanded to support these new features in a backwards and forwards compatible way:

* Shared Assets are still exported to ``static/``, with their metadata in ``policies/assets.json``. New attributes (``allow_scripts``, ``title``) are extra keys there, which older importers ignore.
* Shared Assets marked as ``private`` (other than ``python_lib.zip``) go into a separate top-level folder outside of ``static/``, so that they will be ignored when imported to older versions of the platform that don't support private assets.
* Content references stay as ``/static/...`` (:ref:`openedx-content-adr-0014`).
* Files attached to components (via Shadow Components) are exported under ``static/_component_assets/``, so older versions import them as ordinary Files, and newer versions import them back as attached files. The file name should be derived from a hash of the contents, so that an asset attached to many components is included only once (gzip's 32 KB window will not deduplicate large files on its own). OLX references will necessarily be rewritten on export, e.g. to ``static/_component_assets/<hash>.ext``, and restored on import (the original filenames will have to be mapped out in ``assets.json``).

5. Phase 3 (Yucca): Complete Removal of contentstore
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The third phase will be removal of all ``contentstore`` mutation APIs (create, update, delete), the routing layer, and the compatibility shim, leaving only enough code to support the migration command for any instances that still have course data in GridFS. All other code will then use the new ``openedx_content`` APIs directly.

6. What gets deprecated and removed
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We will file and shepherd `DEPR tickets <https://github.com/openedx/public-engineering/issues?q=label%3Adepr>`_, following `OEP-21 <https://docs.openedx.org/projects/openedx-proposals/en/latest/processes/oep-0021-proc-deprecation.html>`_:

* **Modulestore assetstore** (``AssetMetadata``, ``save_asset_metadata_list``, ``get_all_asset_metadata``, and the unused ``assets/assets.xml`` part of exports). It has no UI and overlaps with ``policies/assets.json``. It can be deprecated right away and removed independently of the rest of this work; import keeps ignoring the file.
* **Contentstore trash can** (the ``trashcan`` store and its ``empty_asset_trashcan`` and ``restore_asset_from_trashcan`` commands). This feature was only half-implemented, and cannot be used from the Authoring MFE anyways. In migrated courses, deleted Assets can be restored from their version history. It can be deprecated right away and removed with contentstore.
* **Contentstore** itself (``MongoContentStore``/GridFS, ``CONTENTSTORE`` settings, the legacy contentserver code path, and the routing layer). Removed once all courses are migrated. Legacy ``asset-v1:`` and ``/c4x/`` URLs keep working, mapped to Assets (:ref:`openedx-content-adr-0013`).

Consequences
------------

* For two or more releases, the platform supports two storage backends for course Files, and every file-related feature must be tested in both modes.
* Operators must run (or allow the upgrade to run) the migration before contentstore is removed. Very large instances may need to migrate in batches over several days.
* Until a course's XBlocks also move out of modulestore, it still needs MongoDB. This ADR only removes MongoDB's role for files.
* During the migration period, twice as much storage will be required for migrated course assets as the originals will be kept in GridFS while the working copy is stored in object storage (e.g. S3).

Rejected Alternatives
---------------------

* **Dual-writing to both systems during a transition.** This avoids a cut-over but makes every write path twice as complex and leaves the two copies able to drift. A per-course switch with a rollback command is simpler.
* **Migrating every course at once, in a single upgrade.** This is simpler, but the risk is high and there's no way back for an individual course. The opt-in period lets problems surface on a few courses first.
