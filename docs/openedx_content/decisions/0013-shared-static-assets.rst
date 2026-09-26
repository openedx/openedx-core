.. _openedx-content-adr-0013:

13. Shared Static Assets as File Components
===========================================

Status
------

Draft. Depends on :ref:`openedx-content-adr-0012` and :ref:`openedx-content-adr-0005`. How content references File components is decided in :ref:`openedx-content-adr-0014`.

Context
-------

Today, a course's authored "Files" (previously known as "Files & Uploads") live in the legacy MongoDB contentstore, as a semi-flat, course-wide namespace of paths that OLX references as ``/static/{path}``. Semi-flat means that the UI only supports a flat list of files, but by editing course tarballs, it's possible to nest assets within subfolders. The current Course Files system tends to get very disorganized in large courses, as the UI lacks the ability to organize assets into folders, and the reporting of which assets are in use in the course is not reliable.

As a first step in migrating all content away from MongoDB, we need to define how to store such files in ``openedx_content`` (within a :class:`LearningPackage`).

:ref:`openedx-content-adr-0005` anticipates the shape of the answer when it refers to a special type of component that only holds assets and no XBlock, and the :class:`ComponentType` docstring mentions "a component type to represent packages of files for things like Files and Uploads". `openedx-learning issue #70 <https://github.com/openedx/openedx-learning/issues/70>`_ independently proposed folders as a component type, with relative references resolving within a folder and its subdirectories.

Decisions
---------

1. A "File component" is a new :class:`Component` type that holds one or more asset files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In both courses and libraries, shared/reusable assets (referenced by multiple components, or uploaded directly to a library and not to a particular component) will live in the learning package as a new non-XBlock Component type. This can be achieved without any modifications to the existing models (:class:`Component`, :class:`ComponentType`, :class:`Media`, etc.).

The type is a :class:`ComponentType` with namespace ``openedx.v1`` and name ``file``. A *File component* is any :class:`Component` of this type; there is no separate model for it.

Throughout this ADR and in related code and documentation, "File component" is always written in full. A plain "file" means an individual asset file, whether it belongs to a File component or is attached directly to another Component, never the File component itself.

"File component" is chosen because it matches the Studio page where these assets are managed, now called "Files" (previously "Files & Uploads"), so a File component is simply "a thing on the Files page". It reflects the most common use case, a single file.

2. File components can group related files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

File components are anticipated to usually contain only a single file. Grouping multiple files together into a File component is supported but meant mostly for files that have a technical relationship to each other:

* Different resolutions of a single image file;
* Different video encodings and associated subtitle files (though videos are rarely if ever stored as Course Files);
* An HTML file and its associated CSS/JS/PNG resources; etc.

Grouping files together into a single File component for organizational purposes only (e.g. "Images used in the course intro") is not recommended; ``Collection`` and tags are available for those purposes, as well as attaching files directly to the ``Component`` where they are referenced, rather than creating a separate File component.

3. File components support relative links
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Within a single File component, files can use relative references to each other. For example, an HTML file can reference "./image.jpeg" which would be rendered if the HTML file was viewed in the browser and the image in question was part of the same File component.

4. Course Files assets are File components within the run's learning package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For courses authored in the future, we want to encourage most static assets to be directly attached to the Component where they are used. But shared assets (referenced by multiple components), including every Course Files asset migrated from legacy MongoDB storage, will live in the run's learning package as File components.

5. One File component per existing Course Files asset
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When migrating assets from MongoDB/contentstore to ``openedx_content``, each asset in a course's "Files" will become one File component in the LearningPackage.

In order to avoid breaking migrated Files that depend on other files (mostly HTML files that load assets using relative paths), however, it is necessary to mark migrated Files as "legacy" File components that can use relative references and old-style `/static/filename` references in the OLX. More details of this migration will be specified in the upcoming "contentstore migration" ADR.

6. Human readable titles
~~~~~~~~~~~~~~~~~~~~~~~~

All File components (in fact, all PublishableEntities) have a mutable ``title`` which can be used to store a human-readable name for the entity, such as "Illustration of the Moon's Orbit (SVG)". The title is versioned (it's defined by :class:`PublishableEntityVersion`), so changing the title creates a new version.

7. Codes based on filename
~~~~~~~~~~~~~~~~~~~~~~~~~~

``component_code`` must be unique among all File components in the same :class:`LearningPackage`, and is restricted in what special characters can be used (alphanumeric characters, underscores, hyphens, and periods are allowed but nothing else). While any slug-style ID can be used, a simple option is to set the ``component_code`` to match the filename of the "main" file of the asset during initial upload, without a file extension, e.g. ``moon-orbit-illustration``. (Special characters would need to be replaced, and subdirectories ignored.) Leaving off the file extension is recommended to avoid confusion between the ``component_code`` of the File component itself, and the actual filename(s) of the individual asset file(s) associated with it.

:class:`ComponentVersionMedia` continues to hold the full path and filename of each asset.

8. Assets are not part of the learner-facing outline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

File components are not children of the course container or any of its descendants. Putting them there would mean every asset upload creates a new version of an outline container.

9. "locked" flag is a separate, unversioned metadata model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``locked`` flag is the only piece of metadata used in the existing Course Files feature that cannot be directly modelled within the existing :class:`Component` / :class:`Media` models. To provide this functionality, we will consider all File components to be "unlocked" by default, unless a corresponding row in the new ``FileComponentMetadata`` table exists and has its ``locked`` column set to "true". The ``FileComponentMetadata`` table will have a ``OneToOneField(primary_key=True)`` referencing :class:`Component`.

Locking is not part of versioning, because often authors will wish to lock down all versions of an asset, not just lock the current version while still allowing access to previous versions.

TODO: it is unclear how we can ensure that a model like ``FileComponentMetadata`` will be correctly copied whenever the associated ``Component`` gets copied, such as during re-runs and import/export. For both "locked" and "private" (see next decision), this represents a potential security lapse, if the copied asset drops its restrictions.

Open question: do we care about setting ``locked`` in a library context? Not directly, since learners cannot usually access libraries, but authors may wish to specify that e.g. a certain PDF should always be locked in any course where it is used.

10. Some File components must be private
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

One use case for File components will be the "code library" feature of [CAPA] Problem components, where python code in a centralized ``python_lib.zip`` asset file is available for use in python scripts in all Problem components in a given course. Authors might put grading functions, answer tables, and solution generators in a ``python_lib.zip`` asset file, so it should not be downloadable by learners. In the current platform, this is achieved using a hard-coded rule, optionally bypassed using a temporary waffle flag (``course_assets.allow_download_code_library``, due for removal back in 2025).

Although the platform does not currently support "private"/"staff-only" course Files other than a rule to block access to ``python_lib.zip``, it seems like this could be useful functionality, to allow authors to store instructor guides, answer keys and solution sets, TA notes, and more as part of the course data.

Thus, we need to have support for some File components or some assets within them being restricted to staff only, and it makes sense for this to be a general mechanism rather than just hard-coding an exception for ``python_lib.zip``.

For asset files attached to regular XBlock components, this is achieved by file name conventions: any files in the ``static/`` "folder" of assets attached to a component are accessible by learners (if they know the URL), whereas files not under the ``static/`` prefix (such as the OLX file for the Component itself) are restricted to course staff only. (Note: the UI only allows authors to download/upload files in the ``static/`` prefix anyways, so only the backend is really aware of any non-public files.)

For File components (shared among multiple components in a course), the ``static/`` prefix convention is likely to be too noisy or confusing. Instead, we will implement a ``private`` flag that means "restricted to staff only". Like ``locked``, it will be unversioned and stored as a boolean column on the ``FileComponentMetadata`` table. If ``FileComponentMetadata`` doesn't exist for a particular asset, it is treated as public.

11. Image metadata will be in separate models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

As mentioned in the :class:`Media` docstring, we can use a separate model called ``ImageMedia`` to capture metadata like image dimensions (assuming it can be purely derived from the image byte data itself). We can also use a separate model like ``ImageComponentVersion`` to store *editable* metadata about an image, such as its default alt text and whether or not the image is purely decorative; saving edits of such metadata would create a new version of the File component.

TODO: We do not yet have a mechanism to ensure that extension models like ``ImageComponentVersion`` get duplicated appropriately every time a new ``ComponentVersion`` is created.

Consequences
------------

**Course Files are now versioned.** Since we're building on :class:`Component`, which has full draft-publish and version history support, all shared files in a course will become versioned and support draft-publish as well. (For initial compatibility, we'll likely only use the published versions and auto-publish new files as soon as they're uploaded, but this can be refined in the future.)

**Replaced files are not deleted**, and old versions of the replaced file will still exist; this follows from the fact that the files are now versioned.

**There will be two different ways to use files in course content**: by attaching them directly to XBlock Components, or by uploading them as shared File components. Content libraries already support the former (attached to Components). Note that "locking" assets will only be supported for shared File components, as any public files attached to a ``Component`` that are meant to be accessible to learners will share the same permissions as the ``Component`` they're attached to.

**Linking library components into courses will be simplified**, once we support XBlocks with files attached to their ``Component`` because part of the complexity in copying library components into courses involves analyzing their attached files and merging them into the course's shared Course Files. If we can instead just copy the ``Component``, including all its attached files, directly into the course's Learning Package, no analysis nor merging into shared files is necessary.

However, a library XBlock Component that in turn uses a library File component still has to bring that File component into the course.

**Many more rows per course.** Each course file goes from one Mongo document to about eight rows: ``PublishableEntity``, ``Component``, a version, ``Draft``, ``Published``, ``Media``, ``ComponentVersionMedia``, and a publish log entry. For a course with 2,000 assets that's about 16,000 rows. Per :ref:`openedx-content-adr-0012`, most of those rows would need to be copied each time the course is rerun.

**Code that assumes every component is an XBlock needs auditing.** We'll have to review library search indexing, "list all components" APIs, collections API/UI, etc, and either handle the new File component type or filter out non-XBlocks as needed. This is a good thing to do in any case, as we always wanted to keep "Component" flexible to support non-XBlock use cases in the future.

**Tiny text-based course files can be stored within MySQL.** Just as OLX for XBlocks can be stored within MySQL rows in the ``text`` column instead of on object storage like S3, small uploads (under 50,000 characters) like .svg or .js files *could* be stored entirely within MySQL if we wanted to do so (although in general we probably don't).

How the "Files" UI will work, how File components will be referenced from other Components, and how the migration from contentstore will work will be specified in follow-up ADRs.

Rejected Alternatives
---------------------

Keeping assets in Mongo/GridFS for now and migrating only structure
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is rejected because replacing MongoDB is one of our major goals.

One File component per course run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Treating the whole course's files as a single component is the simplest model, and is simpler to migrate to from contentstore's flat namespace. Rejected because :class:`ComponentVersionMedia` is a snapshot, so a single course-wide File component would rewrite one row per asset on every upload. For a course with 2,000 assets, that would require updating 2,000 rows per upload, which is incredibly inefficient. Further, this big volume of data cannot be pruned while any draft or published version still references it.

A flat, unversioned CourseAsset table mapping a path to Media per learning package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the closest to a 1:1 port of contentstore, and would be both cheap in rows and trivial to migrate.

It's rejected because it doesn't provide the foundation we want that would allow us to improve the end-user experience. Specifically, it doesn't provide better tools for organizing files (like version history, draft-publish, Collections, and per-Component assets). It also introduces new, alternative primitives rather than building with the ones we have (e.g. ``Component``).

However, if we find the architecture or implementation getting unreasonably complicated, it may make sense to revisit this option.

Pushing every legacy file into the components that reference it during migration, with no shared File components
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is rejected because removing course-wide/shared Files would be a major product change, and likely receive strong pushback from users (course authors/instructors). Many files are referenced from outside any component (handouts, textbooks, external links). There would also be no way to update an image file that is used in many different components without replacing the image attached to each component separately.

Strictly one file per File component
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is an appealing alternative, especially when you consider that the number of use cases that require multiple files per File component is likely very small. Videos require multiple files but are rarely if ever stored in Course Files (instead hosted on video-specific services like YouTube or edX.org's video platform). This is rejected primarily to support the use case of HTML "interactive" files that in turn reference image, JavaScript, and/or CSS files. Examples of this can be seen in the `Studio Advanced course <https://github.com/HarvardX/studio-advanced/tree/5cf820c29e3439c9f45cb88e5cf22fd4ab269739/course/static>`_.

TODO: can we quantify how much this feature would be used?

Strictly one file per File component, with a FileSet component type for multiple files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We could have two different component types, e.g. ``file`` and ``fileset`` which support only one or multiple files respectively. Tentatively rejected in order to keep the implementation simpler.

Other names for File components
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Upload** was used in earlier drafts of this ADR, to match the "Uploads" part of the old "Files & Uploads" page. Reviewers found it unclear, and that page is now just called "Files".
- **Folder** would be more accurate for multi-file cases, but implies using File components as an organizational tool, whereas we want to encourage authors to think of each File component as a singular thing (an Image, a PDF, an HTML interactive, a Video), regardless of how many files it technically consists of.
- **AssetSet** is a technical term that is not self-evident to users, and like "Folder" emphasizes the rare multi-file case.
- **Asset** would match the platform's existing ``asset-v1:`` keys, but files attached directly to a component are assets too, and "asset" is not the word authors see in Studio.

"locked" flag alternatives
~~~~~~~~~~~~~~~~~~~~~~~~~~

Instead of a separate ``FileComponentMetadata`` table/model, locking a File component could be implemented by adding a ``locked`` field to ``Component``; we rejected this in order to keep ``Component`` as simple and efficient as possible, and because most Components are XBlocks, not File components, and not subject to locking. Locking could also be implemented as a single per-course list of locked file codes/IDs maintained and enforced elsewhere in the system; that is a perfectly reasonable alternative, still open for consideration, especially if we don't need ``locked`` in a library context.

"Make all assets locked" and "don't implement locking at all" are rejected for lack of backwards compatibility and lack of the strong buy-in required for removing a feature.
