.. _openedx-content-adr-0013:

13. Shared Static Assets
========================

Status
------

Proposed.

Context
-------

Today, a course's authored "Files" (previously known as "Files & Uploads") live in the legacy MongoDB contentstore, as a semi-flat, course-wide namespace of paths that OLX references as ``/static/{path}``. Semi-flat means that the UI only supports a flat list of files, but by editing course tarballs, it's possible to nest assets within subfolders. The current Course Files system tends to get very disorganized in large courses, as the UI lacks the ability to organize assets into folders, and the reporting of which assets are in use in the course is not reliable.

As a first step in migrating all content away from MongoDB, we need to define how to store such files in ``openedx_content`` (within a :class:`LearningPackage`).

The :class:`ComponentType` docstring mentions "a component type to represent packages of files for things like Files and Uploads". `openedx-learning issue #70 <https://github.com/openedx/openedx-learning/issues/70>`_ had proposed folders as a component type, with relative references resolving within a folder and its subdirectories.

Decisions
---------

1. An "Asset" is a new PublishableEntity type that holds a shared asset file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In both courses and libraries, shared/reusable assets (referenced by multiple components, or uploaded directly to a library and not to a particular component) will live in the learning package as instances of a new :class:`Asset` entity. An :class:`Asset` will be a :class:`PublishableEntity` and will be similar to :class:`Component`, except that it won't have a :class:`ComponentType`, won't store OLX, and will only hold a single file (it will only point to a single :class:`Media` file/row).

The usual name for these will be simply "Asset", but they can also be called "shared assets" for more clarity in cases where they may be confused with "Component assets" (the asset files/media attached to Components).

For example, ``syllabus.pdf`` could be an Asset (a "shared asset") that represents a PDF document uploaded to a course's "Files" page, and linked to from various parts of the course content and/or "about" pages.

Because Assets cannot hold more than one file, when several course files have some relationship to each other and need to be grouped together for organizational purposes, this can be achieved in one of two ways:

* By attaching all the related files to the same XBlock Component; or
* By organizing the related Assets (one file per Asset) into a ``Collection``

2. Assets are uniquely keyed by filename
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each Asset holds only a single file, e.g. ``solar-system.svg``, and within a given learning package (i.e. within a given course or library), each Asset's filename must be unique. (This is in contrast to asset files attached to XBlock Components, which allow multiple asset files per Component.) The filename is stored directly as a column on the ``Asset`` model, not on ``AssetVersion`` nor the ``Media`` row that any given ``AssetVersion`` points to.

Filenames should be case-sensitive, such that ``fig1.png`` and ``Fig1.png`` are both allowed. Since MySQL's default collation is case-insensitive, the ``filename`` column must explicitly use a case-sensitive collation (e.g. using ``case_sensitive_char_field``, as ``ref_field`` does), including for its uniqueness constraint. For backwards compatibility, the filename is also allowed to contain ``/``, such as ``images/image1.png``. By convention, an Asset's ``entity_ref`` is ``openedx-asset:{filename}``, but nothing (including asset URLs) should rely on that; look Assets up by ``filename`` using its explicit column.

If a file is uploaded with the same filename as an Asset that has been (soft) deleted, the deleted Asset is un-deleted: the upload becomes a new draft version of the existing Asset, rather than creating a new Asset. Its version history, flags and any existing references to it are retained.

3. Assets support relative links
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Within a single Learning Package, Assets can use relative references to each other. For example, an HTML file stored as an Asset can reference ``image.jpg`` (or ``./image.jpg``) which would be rendered if the HTML file was viewed in the browser and the image in question was another Asset in the same Learning Package.

This is largely for backwards compatibility. Existing courses have ``.html`` files in their Files that load ``.js``, ``.css`` and image files from the same course by relative URL, and are framed or linked from course content. Plain (unscripted) HTML and SVG documents work under the ``sandbox`` policy of :ref:`openedx-content-adr-0005`; HTML that relies on running scripts additionally needs the ``allow_scripts`` flag (decision 8). For new content, HTML interactives are better implemented as an HTML Component with the related files attached to it: the interactive's markup lives in the component's own HTML, which loads the attached ``.js``, ``.css`` and image files as subresources.

To achieve this, Assets are served under their own URL form, defined in :ref:`openedx-content-adr-0005` ("URLs"), in which the filename is the final part of the path and all Assets of a learning package share one directory::

    .../{context}/assets/{draft|published}/{filename}

For example, an HTML Asset served at ``.../assets/published/page.html`` that references ``image.png`` causes the browser to request ``.../assets/published/image.png``, which is the ``image.png`` Asset's own URL. Filenames containing ``/`` work the same way: ``images/diagram.html`` referencing ``../logo.png`` resolves to ``.../assets/published/logo.png``.

Historic versions: there is no current use case for learners to access previous versions of assets, so the LMS is expected to use the ``...assets/published/...`` URL form only (plus perhaps the ``draft`` form for staff previewing unpublished changes). Studio can use the ``...assets/draft/...`` URL to get the draft asset data, as well as optionally use the ``?v=num`` query string to access a previous version of a specific file (e.g. ``.../assets/draft/example.jpg?v=5``). Specific historical versions (e.g. ``?v=5``) are always served with ``Content-Disposition: attachment``, so they can be downloaded (e.g. from a version history UI) but are never rendered as documents (relative links are likely to be broken or inconsistent in such cases, since there's no way to correlate the version of the relatively referenced asset like "./image.jpg" with the specific version of a requesting asset like ``referencer.html``).

4. Course Files are Assets within the run's learning package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For courses authored in the future, we want to encourage most static assets to be directly attached to the Component where they are used. But shared assets (referenced by multiple components), including every Course Files asset migrated from legacy MongoDB storage, will live in the run's learning package as Assets.

When migrating assets from MongoDB/contentstore to ``openedx_content``, each asset in a course's "Files" will become one Asset in the LearningPackage, with the following exceptions and special cases:

* **Thumbnails** generated by contentstore are dropped and not migrated. Thumbnails will be handled separately, as described in an upcoming ADR.
* **Video transcripts** stored in contentstore (e.g. ``subs_*.srt.sjson``) become Assets like any other file. Video blocks reference transcripts by name rather than via ``/static/`` URLs, and during this migration they are still in modulestore with no :class:`Component`, so no dependency tracking is set up for transcript Assets at this stage. Dependencies between video components and their transcript Assets can be added when course content itself is migrated to ``openedx_content``.
* **python_lib.zip** automatically gets ``private`` mode (decision 7), so that it remains inaccessible to learners as it is today.
* **HTML files** get ``allow_scripts=True`` (decision 8) if the course currently relies on them being rendered with scripts enabled, so that they keep working.

Each course's Files are migrated in a single step, which also creates the run's learning package and ``CourseContent`` row (:ref:`openedx-content-adr-0012`, decision 3). A course is never in a mixed state: before the migration, all of its Files are in contentstore, and afterwards, contentstore is no longer consulted for that course.

5. Human readable titles
~~~~~~~~~~~~~~~~~~~~~~~~

All Assets (in fact, all PublishableEntities) have a mutable ``title`` which can be used to store a human-readable name for the entity, such as "Illustration of the Moon's Orbit (SVG)". The title is versioned (it's defined by :class:`PublishableEntityVersion`), so changing the title creates a new version.

The title of an asset can be changed at any time, but the filename cannot be changed. (Changing the filename would break references/links to the file from the course content.)

6. Assets are not part of the learner-facing outline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Assets are not children of the course container or any of its descendants. Putting them there would mean every asset upload creates a new version of an outline container.

7. Asset permissions mode: "public", "locked", "private"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``Asset`` model will have a permissions column with an enum that allows values of ``public`` (default), ``locked``, and ``private``. The permissions mode is on the unversioned model (not part of versioning), because often authors will wish to lock down all versions of an asset, not just lock the current version while still allowing access to previous versions.

* ``public`` is the default for now, as it was in contentstore. There are no permissions checks on such assets, and anyone who knows the filename/URL can access it. This was chosen as the default more for performance reasons (skipping expensive permissions checks) rather than product reasons, and we may change to ``locked`` as default in the future.
* ``locked`` mirrors the existing contentstore flag for course files. When True, it means that only enrolled users can access the given file.
* ``private`` means that only course staff can access the asset, e.g. for storing grading rubric PDFs. This is a new feature compared to contentstore, although it will be used to prevent learners from downloading ``python_lib.zip``, a special shared asset used by the problem XBlock that must not be accessible to learners (contentstore achieves this with a hard-coded rule).

In **libraries**, these permissions checks have little effect, but indicate what permissions an asset should have when copied into the course.

This information must be preserved round-trip on course exports/import. The attributes like ``locked``, ``private``, ``allow_scripts``, and ``title`` need to go into ``policies/assets.json``. Private shared assets other than ``python_lib.zip`` will have to be exported to a separate folder outside of ``static/`` and will be silently ignored if imported into an older version of Open edX that doesn't support private assets.

8. HTML Assets can be allowed to run scripts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some existing courses have HTML files in their Files (e.g. interactives, simulations, or games) that load JavaScript from other course files and are embedded as iframes within HTML XBlocks. These only work if the HTML document is allowed to run scripts, which the default ``Content-Security-Policy: sandbox`` header from :ref:`openedx-content-adr-0005` prevents. We must not break these courses when their Files are migrated.

A new boolean ``allow_scripts`` flag on the ``Asset`` model (defaulting to ``False``) will be used to support this use case. When a shared HTML Asset with ``allow_scripts=True`` is accessed, it will be served with the relaxed "Insecure Legacy Assets" policy described in :ref:`openedx-content-adr-0005`: ``Content-Security-Policy: sandbox allow-scripts allow-forms allow-popups``. This will allow ``.js`` files to be loaded and run by the browser, which the default ``sandbox`` header would otherwise prevent. The document still gets an opaque origin, so its scripts cannot read the LMS or Studio cookies, cannot read responses from LMS or Studio APIs, and cannot pass Django's CSRF check.

Additional rules:

- **The platform decides whether to honor the flag.** As required by :ref:`openedx-content-adr-0005`, ``openedx_content`` only stores the flag. The platform passes the relaxed policy into the serving API, and it can be disabled for a whole site (or course) with a platform-level setting, in which case flagged Assets are served with the normal ``sandbox`` policy.
- **Flagged Assets bypass the proxy's forced headers.** The reverse proxy forces ``Content-Security-Policy: sandbox`` on the internal ``X-Accel-Redirect`` location, so flagged Assets are served using the Django-streamed fallback (or a separate internal proxy location with the relaxed policy), as anticipated in :ref:`openedx-content-adr-0005`.
- **The flag is not limited to migrated Assets.** It is set automatically by the migration from contentstore (decision 4), but it can also be turned on later for any HTML Asset, including newly uploaded ones, and it can be cleared at any time.

We will likely continue to support this flag until, and unless, the platform has a way to prevent authors from putting arbitrary HTML (including scripts) directly into HTML components. See Consequences below.

Consequences
------------

**Course Files are now versioned.** Since an :class:`Asset` is a :class:`PublishableEntity`, which has full draft-publish and version history support, all shared files in a course will become versioned and support draft-publish as well. (For initial compatibility with contentstore, we'll necessarily auto-publish new/updated/deleted files as soon as they're modified, but this can be refined in the future.)

**Replaced files are not deleted**, and old versions of the replaced file will still exist; this follows from the fact that the files are now versioned.

**Assets cannot be renamed.** Because the filename *is* the identifier, new files can be uploaded, but renaming an Asset would break any existing usages that refer to it by its filename identifier.

**There will be two different ways to use files in course content**: by attaching them directly to XBlock Components, or by uploading them as shared assets. Content libraries already support the former (attached to Components), and courses support the latter. Note that the ``locked`` and ``private`` modes will only be supported for shared assets, as any files attached to a ``Component`` that are meant to be accessible to learners will share the same permissions as the ``Component`` they're attached to.

**Linking library components into courses will be simplified**, once we support XBlocks with files attached to their ``Component``, because part of the complexity in copying library components into courses involves analyzing their attached files and merging them into the course's shared Course Files. If we can instead just copy the ``Component``, including all its attached files, directly into the course's Learning Package, no analysis nor merging into shared files is necessary.

However, a library XBlock Component that in turn uses a library Asset still has to bring that Asset into the course.

**Many more rows per course.** Each course file goes from one Mongo document to about nine rows: ``PublishableEntity``, ``Asset``, ``PublishableEntityVersion``, ``AssetVersion``, ``Draft``, ``Published``, ``Media``, a ``DraftChangeLogRecord`` and a ``PublishLogRecord`` (plus the ``DraftChangeLog`` and ``PublishLog`` themselves, which can be shared by all files uploaded or migrated together). For a course with 2,000 assets that's about 18,000 rows. Per :ref:`openedx-content-adr-0012`, most of those rows would need to be copied each time the course is rerun.

**Legacy asset key URLs must be mapped to Assets.** Once a course's Files are migrated, contentstore is no longer consulted for that course, but its content (and external sites) will still contain asset key URLs like ``/asset-v1:{course}+type@asset+block@{name}`` (see :ref:`openedx-content-adr-0014`). We must implement a handler (e.g. in the contentserver middleware) that resolves such URLs for migrated courses to the Asset with the corresponding filename in the run's learning package, and serves or redirects to it. The mapping must account for how contentstore encodes paths in asset keys, so that files that were in subfolders still resolve.

**Tiny text-based course files can be stored within MySQL.** Just as OLX for XBlocks can be stored within MySQL rows in the ``text`` column instead of on object storage like S3, small uploads (under 50,000 characters) like .svg or .js files *could* be stored entirely within MySQL if we wanted to do so (although in general we probably don't).

How Assets are referenced from Components is specified in :ref:`openedx-content-adr-0014`. How the "Files" UI will work, and the details of the migration from contentstore, will be specified in follow-up ADRs.

The **risk trade-offs of the ``allow_scripts`` flag for HTML assets** are:

- **Compared to HTML components, the risk is lower.** Script in an HTML component runs with the full LMS origin, so it can read the learner's session and call any API on their behalf. A shared Asset's scripts run in an opaque origin and can do neither. As long as authors can put scripts in HTML components (XBlocks), forbidding scripts in HTML Assets adds little protection.
- **Phishing on the real domain.** With scripts and forms allowed, a flagged Asset opened directly shows the genuine LMS or Studio hostname in the address bar, and could present a convincing fake login form. (The default ``sandbox`` policy prevents this to some extent, because it blocks forms.)
- **Requests carry the viewer's cookies.** Because the platform defaults to ``SameSite=None`` cookies, requests that a flagged Asset's scripts send to the LMS or Studio include the viewer's cookies. The scripts cannot read the responses, and Django's CSRF check rejects their ``null`` origin, but they could still trigger GET endpoints with side effects, or views that are exempt from CSRF checks. With ``SameSite=Lax`` cookies, this risk would go away.
- **Probing and general abuse.** The scripts can infer some information about the viewer (e.g. whether a credentialed request succeeded), send data typed into the page to a third party, or redirect the viewer elsewhere.

Rejected Alternatives
---------------------

Keeping assets in Mongo/GridFS for now and migrating only structure
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is rejected because replacing MongoDB is one of our major goals.

A flat, unversioned CourseAsset table mapping a path to Media per learning package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the closest to a 1:1 port of contentstore, and would be both cheap in rows and trivial to migrate.

It's rejected because it doesn't provide the foundation we want that would allow us to improve the end-user experience. Specifically, it doesn't provide better tools for organizing files (like version history, draft-publish, Collections, and per-Component assets). It also introduces new, alternative primitives rather than building with the ones we have (e.g. ``PublishableEntity``).

However, if we find the architecture or implementation getting unreasonably complicated, it may make sense to revisit this option.

Pushing every legacy file into the components that reference it during migration, with no shared assets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is rejected because removing course-wide/shared Files would be a major product change, and likely receive strong pushback from users (course authors/instructors). Many files are referenced from outside any component (handouts, textbooks, external links). There would also be no way to update an image file that is used in many different components without replacing the image attached to each component separately.

Multiple files per Asset
~~~~~~~~~~~~~~~~~~~~~~~~

The number of use cases that require multiple files per Asset is expected to be very small:

* HTML Interactives (e.g. an ``.html``, several ``.js``, and a ``.css`` file)
* Images in multiple resolutions
* Converted documents, e.g. a PDF and .docx of the same document
* Videos, each with multiple chunks, multiple encodings, and multiple subtitle files

For each of these, there is usually a better option:

* HTML Interactives can be implemented as HTML XBlocks whose own HTML contains the interactive's markup and loads the required ``.js`` and ``.css`` files, attached to the same Component, as subresources.
* Images can use the thumbnail system to derive different resolutions, so authors only ever have to manage the "original" vector or full-resolution file.
* Document conversions are the same thing: it's often better for the author to upload and manage only a single authoritative document and have the system generate the derived version automatically. If the author needs full control of each version, they can just use two separate Assets.
* Videos are rarely if ever stored in Course Files anyways, and are best hosted on video-specific services like YouTube or edX.org's video platform.

A separate entity type for multiple files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We could have a separate "Asset Set" entity type that holds multiple files as a group, but it's unclear if there's any use case for this that would justify the complexity, both in terms of implementation and end user experience. As described above, related files can already be grouped by attaching them to a single Component, or by putting the related Assets into a ``Collection``.

Other names for Assets
~~~~~~~~~~~~~~~~~~~~~~

- **Asset Component** was used in earlier drafts of this ADR, when shared assets were modelled as a special type of :class:`Component`.
- **Upload** was used in earlier drafts of this ADR, to match the "Uploads" part of the old "Files & Uploads" page. Reviewers found it unclear, and that page is now just called "Files".
- **File** was used in a later draft, to match the Studio "Files" page. It was dropped because "File component" and "file" are too easily confused, since every Component can have files attached to it.
- **Folder** was considered when Assets could hold multiple files, but it implies using Assets as an organizational tool. Each Asset holds exactly one file and represents a singular thing (an image, a PDF, a document); grouping is done with Collections or by attaching files to a Component.

"locked" mode alternatives
~~~~~~~~~~~~~~~~~~~~~~~~~~

"Make all assets locked" and "don't implement locking at all" are rejected for lack of backwards compatibility and lack of the strong buy-in required for removing a feature. "Make new assets locked by default" is worth considering in the future; it was previously not done only for performance reasons which may or may not be as relevant in the future.

Only allow relative links for HTML files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

It seems that HTML files are the only ones that need relative link support, so we could implement static asset serving such that only HTML files are able to reference each other. It's unclear that this would provide any simplification, unless we moved HTML assets to a different endpoint or used something other than filename to identify assets in the URL. This "only HTML files can make relative references" restriction would also break CSS files with relative ``url(...)`` references and ``.m3u8`` playlists with relative references, though it's unlikely that these are used at all.
