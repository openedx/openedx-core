.. _openedx-content-adr-0013:

13. Shared Static Assets as Asset Components
============================================

Status
------

Draft. Depends on :ref:`openedx-content-adr-0012` and :ref:`openedx-content-adr-0005`. How content references Asset Components is decided in :ref:`openedx-content-adr-0014`.

Context
-------

Today, a course's authored "Files" (previously known as "Files & Uploads") live in the legacy MongoDB contentstore, as a semi-flat, course-wide namespace of paths that OLX references as ``/static/{path}``. Semi-flat means that the UI only supports a flat list of files, but by editing course tarballs, it's possible to nest assets within subfolders. The current Course Files system tends to get very disorganized in large courses, as the UI lacks the ability to organize assets into folders, and the reporting of which assets are in use in the course is not reliable.

As a first step in migrating all content away from MongoDB, we need to define how to store such files in ``openedx_content`` (within a :class:`LearningPackage`).

:ref:`openedx-content-adr-0005` anticipates the shape of the answer when it refers to a special type of component that only holds assets and no XBlock, and the :class:`ComponentType` docstring mentions "a component type to represent packages of files for things like Files and Uploads". `openedx-learning issue #70 <https://github.com/openedx/openedx-learning/issues/70>`_ independently proposed folders as a component type, with relative references resolving within a folder and its subdirectories.

Decisions
---------

1. An "Asset Component" is a new :class:`Component` type that holds a shared asset file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In both courses and libraries, shared/reusable assets (referenced by multiple components, or uploaded directly to a library and not to a particular component) will live in the learning package as instances of a new non-XBlock Component type. This can be achieved without any modifications to the existing models (:class:`Component`, :class:`ComponentType`, :class:`Media`, etc.).

The type is a :class:`ComponentType` with namespace ``openedx.v1`` and name ``asset``. An *Asset Component* is any :class:`Component` of this type; there is no separate model for it.

.. admonition:: "Asset Component" vs. "Component asset"

   - An **Asset Component** is a :class:`Component` whose type is ``openedx.v1:asset``. It exists to hold a single, shared asset file, and has no XBlock.
   - A **Component asset** (or just "asset file") is an individual file attached to any Component (usually an XBlock) via :class:`ComponentVersionMedia`, such as ``static/diagram-1.png`` on an HTML component. The file inside an Asset Component is a Component asset too.

   Throughout this ADR and in related code and documentation, "Asset Component" is always written in full and never shortened to "asset". This matches existing ``openedx_content`` code, where e.g. ``get_redirect_response_for_component_asset()`` uses "asset" for any file attached to a :class:`ComponentVersion`.

"Asset Component" is chosen because "Shared Asset Component" is too long, although that would be clearer. It also matches the names already used for these things elsewhere: the legacy ``asset-v1:...+type@asset+block@...`` keys of the Course Files being migrated, and the "assets" APIs behind Studio's "Files" page.

2. Asset Components are uniquely keyed by filename
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each Asset Component holds only a single file, e.g. ``solar-system.svg``, and within a given learning package (i.e. within a given course or library), each Asset Component's filename must be unique. (This is in contrast to asset files attached to XBlock Components, which allow multiple asset files per Component.)

When assets (of any type) have some relationship to each other and need to be grouped together for organizational purposes, this can be achieved in one of two ways:

* By attached all the related asset files to the same XBlock Component; or
* By organizing the related Asset Components (one file per Asset Component) into a ``Collection``

Note: due to decision 7 below, the actual requirement is slightly stricter than this: the derived ``component_code`` must be unique, which means that ``images_a.png`` and ``images/a.png`` would conflict with each other; this is already the case in ``contentstore`` today.

3. Asset Components support relative links
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Within a single Learning Package, Asset Components can use relative references to each other. For example, an HTML file stored as a shared Asset Component can reference "./image.jpeg" which would be rendered if the HTML file was viewed in the browser and the image in question was another Asset Component in the same Learning Package.

This is largely for backwards compatibility, and the main use case (HTML interactives) is better served by attaching all the related files to a single HTML Component.

In order to achieve this, **the asset serving URL scheme from decision 0005** must be updated, so that when serving any component's file assets from a path like ``.../{component_key}/{version}/{filepath}``, if the ``{filepath}`` part does not resolve within the referenced component version, it will fall back to any Asset Component in the learning package that has that file name. For example, an HTML Asset Component accessed via the URL ``.../openedx.v1:asset:page.html/published/page.html`` may reference an image belonging to another Asset Component which would normally have the URL ``.../openedx.v1:asset:image.png/published/image.png`` but in this case may be accessed as ``.../openedx.v1:asset:page.html/published/image.png``.

4. Course Files assets are Asset Components within the run's learning package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For courses authored in the future, we want to encourage most static assets to be directly attached to the Component where they are used. But shared assets (referenced by multiple components), including every Course Files asset migrated from legacy MongoDB storage, will live in the run's learning package as Asset Components.

5. One Asset Component per existing Course Files asset
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When migrating assets from MongoDB/contentstore to ``openedx_content``, each asset in a course's "Files" will become one Asset Component in the LearningPackage.

6. Human readable titles
~~~~~~~~~~~~~~~~~~~~~~~~

All Asset Components (in fact, all PublishableEntities) have a mutable ``title`` which can be used to store a human-readable name for the entity, such as "Illustration of the Moon's Orbit (SVG)". The title is versioned (it's defined by :class:`PublishableEntityVersion`), so changing the title creates a new version.

7. Codes based on filename
~~~~~~~~~~~~~~~~~~~~~~~~~~

``component_code`` must be unique among all Asset Components in the same :class:`LearningPackage`, and is restricted in what special characters can be used (alphanumeric characters, underscores, hyphens, and periods are allowed but nothing else).

For simplicity and backwards compatibility, we will set the ``component_code`` to (almost) the same value as the ``contentstore`` ``path`` value: all characters other than alphanumerics, hyphens, underscores, and periods are converted to underscores. (Note: the ``contentstore`` algorithm also allowed ``%`` in the result, which ``component_code`` will not.)

Examples:

* ``image 001.png`` would become code ``image_001.png``
* ``subfolder/jane & todd.orig.jpeg`` would become code ``subfolder_jane___todd.orig.jpeg``

:class:`ComponentVersionMedia` continues to hold the full path and filename of the actual asset file.

8. Assets are not part of the learner-facing outline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Asset Components are not children of the course container or any of its descendants. Putting them there would mean every asset upload creates a new version of an outline container.

9. "locked" flag is a separate, unversioned metadata model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``locked`` flag is the only piece of metadata used in the existing Course Files feature that cannot be directly modelled within the existing :class:`Component` / :class:`Media` models. To provide this functionality, we will consider all Asset Components to be "unlocked" by default, unless a corresponding row in the new ``AssetComponentMetadata`` table exists and has its ``locked`` column set to "true". The ``AssetComponentMetadata`` table will have a ``OneToOneField(primary_key=True)`` referencing :class:`Component`.

Locking is not part of versioning, because often authors will wish to lock down all versions of an asset, not just lock the current version while still allowing access to previous versions.

TODO: it is unclear how we can ensure that a model like ``AssetComponentMetadata`` will be correctly copied whenever the associated ``Component`` gets copied, such as during re-runs and import/export. For both "locked" and "private" (see next decision), this represents a potential security lapse, if the copied asset drops its restrictions.

Open question: do we care about setting ``locked`` in a library context? Not directly, since learners cannot usually access libraries, but authors may wish to specify that e.g. a certain PDF should always be locked in any course where it is used.

10. Some Asset Components must be private
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

One use case for Asset Components will be the "code library" feature of [CAPA] Problem components, where python code in a centralized ``python_lib.zip`` asset file is available for use in python scripts in all Problem components in a given course. Authors might put grading functions, answer tables, and solution generators in a ``python_lib.zip`` asset file, so it should not be downloadable by learners. In the current platform, this is achieved using a hard-coded rule, optionally bypassed using a temporary waffle flag (``course_assets.allow_download_code_library``, due for removal back in 2025).

Although the platform does not currently support "private"/"staff-only" course Files other than a rule to block access to ``python_lib.zip``, it seems like this could be useful functionality, to allow authors to store instructor guides, answer keys and solution sets, TA notes, and more as part of the course data.

Thus, we need to have support for some Asset Components or some assets within them being restricted to staff only, and it makes sense for this to be a general mechanism rather than just hard-coding an exception for ``python_lib.zip``.

For asset files attached to regular XBlock components, this is achieved by file name conventions: any files in the ``static/`` "folder" of assets attached to a component are accessible by learners (if they know the URL), whereas files not under the ``static/`` prefix (such as the OLX file for the Component itself) are restricted to course staff only. (Note: the UI only allows authors to download/upload files in the ``static/`` prefix anyways, so only the backend is really aware of any non-public files.)

For Asset Components (shared among multiple components in a course), the ``static/`` prefix convention is likely to be too noisy or confusing. Instead, we will implement a ``private`` flag that means "restricted to staff only". Like ``locked``, it will be unversioned and stored as a boolean column on the ``AssetComponentMetadata`` table. If ``AssetComponentMetadata`` doesn't exist for a particular asset, it is treated as public.

11. Image metadata will be in separate models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

As mentioned in the :class:`Media` docstring, we can use a separate model called ``ImageMedia`` to capture metadata like image dimensions (assuming it can be purely derived from the image byte data itself). We can also use a separate model like ``ImageComponentVersion`` to store *editable* metadata about an image, such as its default alt text and whether or not the image is purely decorative; saving edits of such metadata would create a new version of the Asset Component.

TODO: We do not yet have a mechanism to ensure that extension models like ``ImageComponentVersion`` get duplicated appropriately every time a new ``ComponentVersion`` is created.

Consequences
------------

**Course Files are now versioned.** Since we're building on :class:`Component`, which has full draft-publish and version history support, all shared files in a course will become versioned and support draft-publish as well. (For initial compatibility, we'll likely only use the published versions and auto-publish new files as soon as they're uploaded, but this can be refined in the future.)

**Replaced files are not deleted**, and old versions of the replaced file will still exist; this follows from the fact that the files are now versioned.

**Asset Components cannot be renamed.** Because the filename *is* the identifier, new files can be uploaded, but renaming a file would break any existing usages as well as possibly require a changing the ``component_code``, so must be disallowed.

**There will be two different ways to use files in course content**: by attaching them directly to XBlock Components, or by uploading them as shared Asset Components. Content libraries already support the former (attached to Components). Note that "locking" assets will only be supported for shared Asset Components, as any public files attached to a ``Component`` that are meant to be accessible to learners will share the same permissions as the ``Component`` they're attached to.

**Linking library components into courses will be simplified**, once we support XBlocks with files attached to their ``Component`` because part of the complexity in copying library components into courses involves analyzing their attached files and merging them into the course's shared Course Files. If we can instead just copy the ``Component``, including all its attached files, directly into the course's Learning Package, no analysis nor merging into shared files is necessary.

However, a library XBlock Component that in turn uses a library Asset Component still has to bring that Asset Component into the course.

**Many more rows per course.** Each course file goes from one Mongo document to about eight rows: ``PublishableEntity``, ``Component``, a version, ``Draft``, ``Published``, ``Media``, ``ComponentVersionMedia``, and a publish log entry. For a course with 2,000 assets that's about 16,000 rows. Per :ref:`openedx-content-adr-0012`, most of those rows would need to be copied each time the course is rerun.

**Code that assumes every component is an XBlock needs auditing.** We'll have to review library search indexing, "list all components" APIs, collections API/UI, etc, and either handle the new Asset Component type or filter out non-XBlocks as needed. This is a good thing to do in any case, as we always wanted to keep "Component" flexible to support non-XBlock use cases in the future.

**Tiny text-based course files can be stored within MySQL.** Just as OLX for XBlocks can be stored within MySQL rows in the ``text`` column instead of on object storage like S3, small uploads (under 50,000 characters) like .svg or .js files *could* be stored entirely within MySQL if we wanted to do so (although in general we probably don't).

How the "Files" UI will work, how Asset Components will be referenced from other Components, and how the migration from contentstore will work will be specified in follow-up ADRs.

Rejected Alternatives
---------------------

Keeping assets in Mongo/GridFS for now and migrating only structure
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is rejected because replacing MongoDB is one of our major goals.

One Asset Component per course run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Treating the whole course's files as a single component is the simplest model, and is simpler to migrate to from contentstore's flat namespace. Rejected because :class:`ComponentVersionMedia` is a snapshot, so a single course-wide Asset Component would rewrite one row per asset on every upload. For a course with 2,000 assets, that would require updating 2,000 rows per upload, which is incredibly inefficient. Further, this big volume of data cannot be pruned while any draft or published version still references it.

A flat, unversioned CourseAsset table mapping a path to Media per learning package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the closest to a 1:1 port of contentstore, and would be both cheap in rows and trivial to migrate.

It's rejected because it doesn't provide the foundation we want that would allow us to improve the end-user experience. Specifically, it doesn't provide better tools for organizing files (like version history, draft-publish, Collections, and per-Component assets). It also introduces new, alternative primitives rather than building with the ones we have (e.g. ``Component``).

However, if we find the architecture or implementation getting unreasonably complicated, it may make sense to revisit this option.

Pushing every legacy file into the components that reference it during migration, with no shared Asset Components
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is rejected because removing course-wide/shared Files would be a major product change, and likely receive strong pushback from users (course authors/instructors). Many files are referenced from outside any component (handouts, textbooks, external links). There would also be no way to update an image file that is used in many different components without replacing the image attached to each component separately.

Multiple files per Asset Component
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The number of use cases that require multiple files per Asset Component is expected to be very small:

* HTML Interactives (e.g. an ``.html``, several ``.js``, and a ``.css`` file)
* Images in multiple resolutions
* Converted documents, e.g. a PDF and .docx of the same document
* Videos, each with multiple chunks, multiple encodings, and multiple subtitle files

For each of these, there is usually a better option:

* HTML Interactives can be implemented as HTML XBlocks with the required ``.js`` and ``.css`` files attached.
* Images can use the thumbnail system to derive different resolutions, so authors only ever have to manage the "original" vector or full-resolution file.
* Document conversions are the same thing: it's often better for the author to upload and manage only a single authoritative document and have the system generate the derived version automatically. If the author needs full control of each version, they can just use two separate Asset Components.
* Videos are rarely if ever stored in Course Files anyways, and are best hosted on video-specific services like YouTube or edX.org's video platform.

A separate component type for multiple files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We could have a separate "Asset Set" Component which holds multiple files, but it's unclear if there's any use case for this that would justify the complexity, both in terms of implementation and end user experience.

Other names for Asset Components
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Upload** was used in earlier drafts of this ADR, to match the "Uploads" part of the old "Files & Uploads" page. Reviewers found it unclear, and that page is now just called "Files".
- **File** was used in a later draft, to match the Studio "Files" page. It was dropped because "File component" and "file" are too easily confused: an Asset Component can hold several files, and every Component can have files attached to it.
- **Folder** would be more accurate for multi-file cases, but implies using Asset Components as an organizational tool, whereas we want to encourage authors to think of each Asset Component as a singular thing (an Image, a PDF, an HTML interactive, a Video), regardless of how many files it technically consists of.

"locked" flag alternatives
~~~~~~~~~~~~~~~~~~~~~~~~~~

Instead of a separate ``AssetComponentMetadata`` table/model, locking an Asset Component could be implemented by adding a ``locked`` field to ``Component``; we rejected this in order to keep ``Component`` as simple and efficient as possible, and because most Components are XBlocks, not Asset Components, and not subject to locking. Locking could also be implemented as a single per-course list of locked file codes/IDs maintained and enforced elsewhere in the system; that is a perfectly reasonable alternative, still open for consideration, especially if we don't need ``locked`` in a library context.

"Make all assets locked" and "don't implement locking at all" are rejected for lack of backwards compatibility and lack of the strong buy-in required for removing a feature.
