.. _openedx-content-adr-0013:

13. Course Static Assets as "Upload" Components
===============================================

Status
------

Draft. Depends on :ref:`openedx-content-adr-0012` and :ref:`openedx-content-adr-0005`. How content references Uploads is decided in :ref:`openedx-content-adr-0014`.

Context
-------

Today, a course's authored "Files" (previously known as "Files & Uploads") live in the legacy MongoDB contentstore, as a semi-flat, course-wide namespace of paths that OLX references as ``/static/{path}``. Semi-flat means that the UI only supports a flat list of files, but by editing course tarballs, it's possible to nest assets within subfolders. The current Course Files system tends to get very disorganized in large courses, as the UI lacks the ability to organize assets into folders, and the reporting of which assets are in use in the course is not reliable.

As a first step in migrating all content away from MongoDB, we need to define how to store such files in ``openedx_content`` (within a :class:`LearningPackage`).

:ref:`openedx-content-adr-0005` anticipates the shape of the answer when it refers to a special type of component that only holds assets and no XBlock, and the :class:`ComponentType` docstring mentions "a component type to represent packages of files for things like Files and Uploads". `openedx-learning issue #70 <https://github.com/openedx/openedx-learning/issues/70>`_ independently proposed folders as a component type, with relative references resolving within a folder and its subdirectories.

Decisions
---------

1. An "Upload" is a new :class:`Component` type that holds one or more asset files.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In both courses and libraries, shared/reusable assets (referenced by multiple components, or uploaded directly to a library and not to a particular component) will live in the learning package as a new non-XBlock Component type, "Upload". This can be achieved without any modifications to the existing models (:class:`Component`, :class:`ComponentType`, :class:`Media`, etc.).

The type is a :class:`ComponentType` with namespace ``openedx.v1`` and name ``upload``.

An upload is technically most like a folder, and our preliminary thinking about this idea also used the terms "AssetSet" or "Folder" for what we are naming an "Upload" in this ADR. "Upload" is chosen because it matches the "Uploads" part of the page in the Studio UI which was previously known as "Files & Uploads", implies a generally singular thing without ruling out multiple files, doesn't conflict with any other names used in the platform, and is hopefully fairly self-evident to users, unlike a technical term like "AssetSet".

2. Uploads group related files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Uploads are anticipated to often contain only a single file. Grouping multiple files together into an Upload is supported but meant mostly for files that have a technical relationship to each other:

* Different resolutions of a single image file;
* Different video encodings and associated subtitle files (though videos are rarely if ever stored as Course Files);
* An HTML file and its associated CSS/JS/PNG resources; etc.

Grouping files together into a single ``Upload`` for organizational purposes only (e.g. "Images used in the course intro") is not recommended; ``Collection`` and tags are available for those purposes, as well as attaching files directly to the ``Component`` where they are referenced, rather than creating a separate ``Upload`` Component.

3. Uploads support relative links
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Within a single "Upload", files can use relative references to each other. For example, an HTML file can reference "./image.jpeg" which would be rendered if the HTML file was viewed in the browser and the image in question was part of the same ``Upload`` component.

4. Course Files assets are "Upload"-type Components within the run's learning package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For courses authored in the future, we want to encourage most static assets to be directly attached to the Component where they are used. But shared assets (referenced by multiple components), including every Course Files asset migrated from legacy MongoDB storage, will live in the run's learning package as Uploads.

5. One Upload per existing Course Files asset
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When migrating assets from MongoDB/contentstore to ``openedx_content``, each asset in a course's "Files" will become one ``Upload`` component in the LearningPackage. It would be nice to automatically group related files together, but this is likely not worth the effort it would require.

More details of this migration will be specified in the upcoming "contentstore migration" ADR.

6. Human readable titles
~~~~~~~~~~~~~~~~~~~~~~~~

All Uploads (in fact, all PublishableEntities) have a mutable ``title`` which can be used to store a human-readable name for the entity, such as "Illustration of the Moon's Orbit (SVG)". The title is versioned (it's defined by :class:`PublishableEntityVersion`), so changing the title creates a new version.

7. Codes based on filename
~~~~~~~~~~~~~~~~~~~~~~~~~~

``component_code`` must be unique among all Upload components in the same :class:`LearningPackage`, and is restricted in what special characters can be used (alphanumeric characters, underscores, hyphens, and periods are allowed but nothing else). While any slug-style ID can be used, a simple option is to set the ``component_code`` to match the filename of the "main" file of the asset during initial upload, without a file extension, e.g. ``moon-orbit-illustration``. (Special characters would need to be replaced, and subdirectories ignored.) Leaving off the file extension is recommended to avoid confusion between the ``component_code`` of the Upload itself, and the actual filename(s) of the individual asset file(s) associated with it.

:class:`ComponentVersionMedia` continues to hold the full path and filename of each asset.

8. Assets are not part of the learner-facing outline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Upload components are not children of the course container or any of its descendants. Putting them there would mean every asset upload creates a new version of an outline container.

9. "locked" flag is a separate, unversioned model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``locked`` flag is the only piece of metadata used in the existing CourseFiles feature that cannot be directly modelled within the existing :class:`Component` / :class:`Media` models. To provide this functionality, we will consider all Uploads to be "unlocked" by default, unless a row in the new ``LockedUpload`` table exists, which is a trivial model that has only one field, a ``OneToOneField(primary_key=True)`` referencing :class:`Component`.

Locking is not part of versioning, because often authors will wish to lock down all versions of an asset, not just lock the current version while still allowing access to previous versions.

TODO: it is unclear how we can ensure that a model like ``LockedUpload`` will be correctly copied whenever the associated ``Component`` gets copied, such as during re-runs and import/export.

Open question: do we care about setting ``locked`` in a library context? Not directly, since learners cannot usually access libraries, but authors may wish to specify that e.g. a certain PDF should always be locked in any course where it is used.

10. Image metadata will be in separate models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

As mentioned in the :class:`Media` docstring, we can use a separate model called ``ImageMedia`` to capture metadata like image dimensions (assuming it can be purely derived from the image byte data itself). We can also use a separate model like ``ImageComponentVersion`` to store *editable* metadata about an image, such as its default alt text and whether or not the image is purely decorative; saving edits of such metadata would create a new version of the Upload.

TODO: We do not yet have a mechanism to ensure that extension models like ``ImageComponentVersion`` get duplicated appropriately every time a new ``ComponentVersion`` is created.

Consequences
------------

**Course Files are now versioned.** Since we're building on :class:`Component`, which has full draft-publish and version history support, all shared files in a course will become versioned and support draft-publish as well. (For initial compatibility, we'll likely only use the published versions and auto-publish new files as soon as they're uploaded, but this can be refined in the future.)

**Replaced files are not deleted**, and old versions of the replaced file will still exist; this follows from the fact that the files are now versioned.

**There will be two different ways to use files in course content**: by attaching them directly to XBlock Components, or by uploading them as shared Course File Uploads. Content libraries already supports the former (attached to Components). Note that "locking" assets will only be support for shared Course File uploads, as any public files attached to a ``Component`` that are meant to be accessible to learners will share the same permissions as the ``Component`` they're attached to.

**Linking library components into courses will be simplified**, once we support XBlocks with files attached to their ``Component`` because part of the complexity in copying library components into courses involves analyzing their attached files and merging them into the course's shared Course Files. If we can instead just copy the ``Component``, including all its attached files, directly into the course's Learning Package, no analysis nor merging into shared files is necessary.

However, a library XBlock Component that in turn uses a library Upload Component still has to bring that Upload Component into the course.

**Many more rows per course.** Each course file goes from one Mongo document to about eight rows: ``PublishableEntity``, ``Component``, a version, ``Draft``, ``Published``, ``Media``, ``ComponentVersionMedia``, and a publish log entry. For a course with 200 assets that's about 1,600 rows. Per :ref:`openedx-content-adr-0012`, most of those rows would need to be copied each time the course is rerun.

**Code that assumes every component is an XBlock needs auditing.** We'll have to review library search indexing, "list all components" APIs, collections API/UI, etc, and either handle the new Upload Component type or filter out non-XBlocks as needed. This is a good thing to do in any case, as we always wanted to keep "Component" flexible to support non-XBlock use cases in the future.

**Tiny text-based course files can be stored within MySQL.** Just as OLX for XBlocks can be stored within MySQL rows in the ``text`` column instead of on object storage like S3, small uploads (under 50,000 characters) like .svg or .js files *could* be stored entirely within MySQL if we wanted to do so (although in general we probably don't).

How the "Files" UI will work, how Upload assets will be referenced from Components, and how the migration from contentstore will work will be specified in follow-up ADRs.

Rejected Alternatives
---------------------

Keeping assets in Mongo/GridFS for now and migrating only structure
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is rejected because replacing MongoDB is one of our major goals.

One Upload component per course run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Treating the whole course's uploads as a single component is the simplest model, and is simpler to migrate to from contentstore's flat namespace. Rejected because :class:`ComponentVersionMedia` is a snapshot, so a single course-wide Upload would rewrite one row per asset on every upload. For a course with 5,000 assets, that would require updating 5,000 rows per upload, which is incredibly inefficient. Further, this big volume of data cannot be pruned while any draft or published version still references it.

A flat, unversioned CourseAsset table mapping a path to Media per learning package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the closest to a 1:1 port of contentstore, and would be both cheap in rows and trivial to migrate.

It's rejected because it doesn't provide the foundation we want that would allow us to improve the end-user experience. Specifically, it doesn't provide better tools for organizing files (like version history, draft-publish, Collections, and per-Component assets).

Pushing every legacy file into the components that reference it during migration, with no shared Uploads
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is rejected because removing course-wide/shared Files would be a major product change, and likely receive strong pushback from users (course authors/instructors). Many files are referenced from outside any component (handouts, textbooks, external links). There would also be no way to update an image file that is used in many different components without replacing the image attached to each component separately.

Strictly one file per Upload component
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is an appealing alternative, especially when you consider that the number of use cases that require multiple files per Upload component is likely very, very small. Videos require multiple files but are rarely if ever stored in Course Files (instead hosted on video-specific services like YouTube or edX.org's video platform). This is rejected primarily to support the use case of HTML "interactive" files that in turn reference image, JavaScript, and/or CSS files.

TODO: can we quantify how much this feature would be used?

Strictly one file per Upload component, with an UploadSet component type for multiple files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We could have two different component types, e.g. ``Upload`` and ``UploadSet`` which support only one or multiple files respectively. Tentatively rejected in order to keep the implementation simpler.

The name "Folder" instead of "Upload"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Folder would be more accurate, but implies using "Upload" as an organizational tool, whereas we want to encourage authors to think of each Upload component as a singular thing (an Image, a PDF, an HTML interactive, a Video), regardless of how many files it technically consists of.

"locked" flag alternatives
~~~~~~~~~~~~~~~~~~~~~~~~~~

Instead of a separate ``LockedUpload`` table/model, locking an Upload could be implemented by adding a ``locked`` field to ``Component``; we rejected this in order to keep ``Component`` as simple and efficient as possible, and because most Components are XBlocks, not Uploads, and not subject to locking. Locking could also be implemented as a single per-course list of locked file codes/IDs maintained and enforced elsewhere in the system; that is a perfectly reasonable alternative, still open for consideration, especially if we don't need ``locked`` in a library context.

"Make all assets locked" and "don't implement locking at all" are rejected for lack of backwards compatibility and lack of the strong buy-in required for removing a feature.
