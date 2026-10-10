.. _openedx-content-adr-0015:

15. Shadow Components
=====================

Status
------

Draft. Depends on :ref:`openedx-content-adr-0005`, :ref:`openedx-content-adr-0012`, :ref:`openedx-content-adr-0013` and :ref:`openedx-content-adr-0014`.

Context
-------

Moving course content out of MongoDB will happen in stages. :ref:`openedx-content-adr-0013` moves a course's shared "Files" into the run's :class:`LearningPackage` as Assets, and that will happen well before the course's XBlocks (OLX, fields, and outline) move out of modulestore. For some period of time, which may be long, a course run will have a learning package that holds its shared assets, while its XBlocks still live in split modulestore.

:ref:`openedx-content-adr-0013` and :ref:`openedx-content-adr-0014` also want authors to attach asset files directly to the component that uses them, rather than putting everything into the course-wide Files. Content libraries already work this way. Attached assets keep the course's Files tidy, make copying components between learning contexts much simpler, and avoid filename collisions.

An asset can only be attached to a :class:`ComponentVersion` via :class:`ComponentVersionMedia`, but while the course's XBlocks are still in modulestore, there is no :class:`Component` for any of them. So either authors cannot attach assets to course components until the full content migration is done, or we need some :class:`Component` to attach them to.

Decisions
---------

1. A "Shadow Component" holds assets for a modulestore XBlock
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A course run whose content is still in modulestore may have *Shadow Components* in its learning package. A Shadow Component is an ordinary :class:`Component` that corresponds to one XBlock in the modulestore course, and is used **only** to store asset files that are attached directly to that XBlock. It holds no OLX and no XBlock field data. Modulestore remains the sole source of truth for the XBlock itself.

There is no new model. A Shadow Component is built entirely from the existing :class:`Component`, :class:`ComponentVersion`, :class:`ComponentVersionMedia` and :class:`Media` models.

2. Shadow Components use the same type and code as the real Component will
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A Shadow Component's :class:`ComponentType` is the XBlock's real type (e.g. ``xblock.v1:html`` for an ``html`` block), and its ``component_code`` is the modulestore block ID, which is what :ref:`openedx-content-adr-0012` (decision 1) says the ``component_code`` will be after migration.

In other words, a Shadow Component is exactly the :class:`Component` that the content migration would have created anyway, only created early and missing its ``block.xml``. This means its key, and the asset URLs derived from it (:ref:`openedx-content-adr-0005`), will not change when the course's content is migrated.

Only leaf XBlocks (those that become Components) get Shadow Components. Containers such as units, subsections and sections do not, since only Components have attached assets.

3. A Shadow Component is identified by having no ``block.xml``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An XBlock Component always has a ``block.xml`` entry in its :class:`ComponentVersionMedia`. A Shadow Component's versions only contain ``static/...`` entries. So "is this a Shadow Component?" is answered by "is this an XBlock-type Component whose version has no ``block.xml``?", and no flag needs to be stored.

Note that this is a property of a version, not of the Component, which is what lets a Shadow Component turn into a real one (decision 6).

4. Shadow Components are created on demand
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A Shadow Component is created the first time an author attaches an asset file to the modulestore XBlock, or when the XBlock is created with attached assets (e.g. pasted from the clipboard, or synced from a library component that has attached assets). XBlocks with no attached assets never get one. Creating a Shadow Component also creates the course run's learning package and ``CourseContent`` row (:ref:`openedx-content-adr-0012`, decision 3) if they don't exist yet.

The Shadow Component's ``title`` may be set from the XBlock's ``display_name`` when created, as a convenience for admin and debugging tools, but it is not kept in sync and nothing should rely on it.

5. Shadow Components follow the XBlock's lifecycle in modulestore
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Studio code that changes modulestore must keep the Shadow Components consistent with it:

- **Edit:** Adding, replacing or removing an attached asset creates a new draft version of the Shadow Component. Editing the XBlock's fields does not.
- **Publish:** When an XBlock is published in modulestore (usually by publishing its unit), the draft of its Shadow Component, if any, is published too. Asset URLs from the Studio preview resolve to the ``draft`` version and from the LMS resolve to the ``published`` version, as for any other Component.
- **Delete:** Deleting the XBlock soft-deletes its Shadow Component.
- **Duplicate, copy/paste, library sync:** The new XBlock gets a new Shadow Component with copies of the source's attached assets (the bytes are deduplicated per :ref:`openedx-content-adr-0012`, decision 5).
- **Rerun:** Shadow Components are copied into the new run's learning package along with its shared assets.

Version numbers of a Shadow Component only count changes to its attached assets, and have no relationship to modulestore versions.

6. Migrating content turns Shadow Components into real Components
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When the course's XBlocks are eventually migrated from modulestore into the learning package, the migrator does not create a new :class:`Component` for an XBlock that already has a Shadow Component. Instead, it creates a new version of the Shadow Component that adds the ``block.xml`` and keeps the existing ``static/...`` entries. From that version on it is an ordinary XBlock Component, and the asset version history from its time as a Shadow Component is kept.

No OLX needs to be rewritten during migration: a ``/static/filename.ext`` reference in the XBlock resolves to the attached asset before any shared asset of the same name (:ref:`openedx-content-adr-0014`, decision 2), whether the XBlock is rendered from modulestore or from ``openedx_content``.

7. ``/static/`` references from modulestore XBlocks check the Shadow Component first
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When rewriting ``/static/`` URLs for a modulestore XBlock in a course that has a learning package, the rewriting code resolves each reference to the first match among:

1. files attached to the XBlock's Shadow Component, if it has one;
2. shared assets in the run's learning package (:ref:`openedx-content-adr-0013`);
3. legacy contentstore, for courses whose Files have not been migrated yet.

This is the same order defined in :ref:`openedx-content-adr-0014` (decision 2), extended with the legacy fallback.

Consequences
------------

**Authors can attach assets to course components before the content migration is done**, and the content migration later becomes a smaller job, because those assets are already in place under their final keys.

**Library components with attached assets can be used in modulestore courses without merging their assets into the course's Files.** This removes much of the complexity in today's library-to-course copy and sync code, which has to rename and merge each component's files into the course-wide namespace.

**We can implement dependency tracking for all XBlocks, even those currently in modulestore.** We can detect ``/static/...`` references in regular modulestore XBlocks, and then record which shared Assets are in use using :class:`PublishableEntityVersionDependency`, exactly the same way as we can for XBlocks/Components that are fully stored in ``openedx_content``. Whether and when we should do this is left to decide at implementation time.

**There are temporarily two sources of truth for a single XBlock**: modulestore for its fields, and ``openedx_content`` for its attached assets. Every Studio code path that creates, copies, publishes or deletes blocks needs to update both, and a missed code path will leave orphaned Shadow Components or XBlocks with missing assets. Orphaned Shadow Components are harmless apart from storage, and could be found and cleaned up by a periodic task that compares them to modulestore.

**Code that lists or renders Components must handle Shadow Components.** They must not be treated as renderable XBlocks. Any code that loads a Component's ``block.xml`` must handle it being absent, and any listing of a course's Components (search indexing, tagging, etc.) should exclude Shadow Components.

**The publish state of an XBlock and its attached assets can drift** if a code path publishes one without the other. Since an XBlock and its assets are published by different systems, this cannot be made atomic.

**Exporting a course with Shadow Components to OLX needs a format for attached assets.** This is the same open question as :ref:`openedx-content-adr-0014` decision 8, but it becomes urgent sooner, because every export of a modulestore course that uses Shadow Components must include them.

Rejected Alternatives
---------------------

Waiting until course content is fully migrated
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We could support attached assets in courses only once XBlocks are stored in ``openedx_content``. This avoids the dual-source-of-truth problem, but delays a significant improvement for authors (and a large simplification of library sync) by what may be a long time, and means that course files created in the meantime all go into the shared Files.

Storing attached assets as shared assets with a naming convention
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each attached asset could be stored as a shared asset named after its XBlock, e.g. ``{block_id}/diagram.png``. This needs no new concept, but it mixes per-component files into the course's Files, which is exactly what attached assets are meant to avoid, and it requires rewriting OLX references (or a separate resolution rule) both now and again at migration time.

A separate model mapping usage keys to Media
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A dedicated table could map a modulestore usage key and a path to a :class:`Media` row. This is cheap in rows, but it introduces a temporary new primitive with its own versioning, publishing and serving logic, all of which :class:`Component` already provides, and its data would have to be migrated again into :class:`ComponentVersionMedia` later.

A separate ``openedx.v1:shadow`` component type
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Using a dedicated component type would make Shadow Components trivial to filter out. But a Component's type cannot change, so the migrator would have to create a new Component and copy the assets across, and the Component's key, and so every asset URL, would change at migration time. It would also lose the asset version history.

An explicit ``is_shadow`` flag
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A boolean on :class:`Component` (or a side table like ``AssetMetadata``) would make Shadow Components easier to query than checking for ``block.xml``. It was rejected to avoid adding a column to a heavily used table for a temporary transition state, and because the flag could disagree with the actual content of the latest version. If querying for the absence of ``block.xml`` turns out to be too slow, a side table can be added later.

Open Questions
--------------

- Should the content migrator also convert legacy Files assets that are used by exactly one XBlock into attached assets on its Shadow Component, or leave them as shared assets?
- How do Shadow Components interact with modulestore's draft/published branches for blocks outside of units (e.g. static tabs and course handouts)?
