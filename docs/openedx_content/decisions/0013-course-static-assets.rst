.. _openedx-content-adr-0013:

13. Course Static Assets as "Upload" Components
===============================================

Status
------

Draft. Depends on :ref:`openedx-content-adr-0012` and :ref:`openedx-content-adr-0005`.

Context
-------

Today, a course's authored "Files" (previously known as "Files & Uploads") live in the legacy MongoDB contentstore, as a semi-flat, course-wide namespace of paths that OLX references as ``/static/{path}``. Semi-flat means that the UI only supports a flat list of files, but by editing course tarballs, it's possible to nest assets within subfolders. The current Course Files system tends to get very disorganized in large courses, as the UI lacks the ability to organize assets into folders, and the reporting of which assets are in use in the course is not reliable.

As a first step in migrating all content away from MongoDB, we need to define how to store such files in ``openedx_content`` (within a :class:`LearningPackage`).

:ref:`openedx-content-adr-0005` anticipates the shape of the answer when it refers to a special type of component that only holds assets and no XBlock, and the :class:`ComponentType` docstring mentions "a component type to represent packages of files for things like Files and Uploads". `openedx-learning issue #70 <https://github.com/openedx/openedx-learning/issues/70>`_ independently proposed folders as a component type, with relative references resolving within a folder and its subdirectories.

Two properties of the existing schema shape the details:

- :class:`ComponentVersionMedia` is a full snapshot of a component version's media, not a delta. Every new version of a component rewrites one row per media item it holds.
- ``component_code`` is a ``code_field``, which forbids slashes and restricts characters to ``[\w.-]``, whereas the ``key`` on :class:`ComponentVersionMedia` is a ``ref_field``: 500 characters and opaque, so slashes are fine.

Decisions
---------

1. An "Upload" is a new :class:`Component` type that holds one or more asset files.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In both courses and libraries, shared/reusable assets (referenced by multiple components, or uploaded directly to a library and not to a particular component) will live in the learning package as a new non-XBlock Component type, "Upload". This requires no schema change.

The type is a :class:`ComponentType` with namespace ``openedx.v1`` and name ``upload``.

An upload is technically most like a folder, and our preliminary thinking about this idea also used the terms "AssetSet" or "Folder" for what we are naming an "Upload" in this ADR. "Upload" is chosen because it matches the "Uploads" part of the page in the Studio UI which was previously known as "Files & Uploads", implies a generally singular thing without ruling out multiple files, doesn't conflict with any other names used in the platform, and is hopefully fairly self-evident to users, unlike a technical term like "AssetSet".

2. Uploads group related files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Uploads are anticipated to often contain only a single file. Grouping multiple files together into an Upload is supported but meant mostly for files that have a technical relationship to each other:

* Different resolutions of a single image file;
* Different video encodings and associated subtitle files;
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

6. Identifiers
~~~~~~~~~~~~~~

All Uploads (in fact, all PublishableEntities) have a mutable ``title`` which can be used to store a human-readable name for the entity, such as "Illustration of the Moon's Orbit (SVG)".

``component_code`` must be unique and is restricted in what special characters can be used, so ideally should be set to a slugified version of the title of the asset during initial upload, e.g. ``moon-orbit-illustration``.

:class:`ComponentVersionMedia` continues to hold the full path and filename of each asset.

7. Assets are not part of the learner-facing outline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Upload components are not children of the course container or any of its descendants. Putting them there would mean every asset upload creates a new version of an outline container.

8. Uploads cannot be referenced without an ``UploadUsageLink``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~



9. Per-asset metadata stays in openedx-platform
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``locked`` flag, ``displayname``, thumbnails and import paths that the legacy contentstore carries are not modelled here. :ref:`openedx-content-adr-0005` already makes permission checking the platform's responsibility. The Upload stays a dumb mapping of paths to :class:`Media`.

Rejected Alternatives
---------------------

One Upload component per course run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Treating the whole course's uploads as a single component is the simplest model, and is simpler to migrate to from contentstore's flat namespace. Rejected because :class:`ComponentVersionMedia` is a snapshot, so a single course-wide Upload would rewrite one row per asset on every upload. For a course with 5,000 assets, that would require updating 5,000 rows per upload, which is incredibly inefficient. Further, this big volume of data cannot be pruned while any draft or published version still references it.

The name "Folder" instead of "Upload"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Folder would be more accurate, but implies using "Upload" as an organizational tool, whereas we want to encourage authors to think of each Upload component as a singular thing (an Image, a PDF, an HTML interactive, a Video), regardless of how many files it technically consists of.
