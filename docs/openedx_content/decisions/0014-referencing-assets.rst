.. _openedx-content-adr-0014:

14. Referencing Assets
======================

Status
------

Draft. Depends on :ref:`openedx-content-adr-0005`, :ref:`openedx-content-adr-0012` and :ref:`openedx-content-adr-0013`.

Context
-------

:ref:`openedx-content-adr-0013` introduces "File components" to hold shared asset files within a learning package. This ADR decides how content refers to those files, and to the files attached directly to a component.

Today, course content references assets in one of two ways:

**Portable URLs**, e.g. ``/static/Time_medium_icon.png``.
  These resolve against a single course-wide namespace of files, and must be rewritten before they can be served. They technically share a namespace with Studio's built-in assets (e.g. ``/static/studio/css/studio-main-v1.css``). However, since user-authored content generally does not reference Studio's built-in JS/CSS files, any usage of ``/static/...`` in XBlock OLX is assumed to be referring to course Files, and will be rewritten to the form shown below before being served to the user.

**Asset key URLs**, e.g. ``https://courses.example.com/asset-v1:HarvardX+StudioAdv1+2T2019+type@asset+block@Time_medium_icon.png`` or sometimes just ``/asset-v1:HarvardX+StudioAdv1+2T2019+type@asset+block@Time_medium_icon.png``
  These are unambiguous, but they encode the course run key and sometimes the hostname, so they break when content is copied to a new run (every rerun), to another course, or to another instance. Technically, the ``asset-v1:...`` opaque key part could be used as an identifier on its own, but this rarely occurs in practice.

For backwards compatibility, both of these formats must continue to be supported indefinitely.

However, neither format is a good fit for the new model:

- :ref:`openedx-content-adr-0013` gives each shared asset its own File component, rather than a single course-wide namespace. A reference by bare filename has to search every File component in the course, and the data model doesn't prevent several different File components from having file assets with the same file name (``path``), so conflicts and ambiguity can occur.
- As with the contentstore/MongoDB backend, the system has no reliable way to report which assets are in use, or by which components. Asset usage reporting is a long-standing pain point in Studio, and clipboard copy/paste and library sync both have to work out which files a piece of content needs.
- Library components that use shared assets need to reference them the same way before and after being copied into a course.

Decisions
---------

1. File components can have a "legacy path" which provides full backwards compatibility
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``legacy_path`` column will be present in the ``FileComponentMetadata`` table mentioned in :ref:`openedx-content-adr-0013`. When course Files are migrated to ``openedx_content``, the ``legacy_path`` will be set to the filename from the course's global namespace, e.g. ``example.png``. Any asset with a ``legacy_path`` defined will have these properties:

- It is limited to one file per File component, and the filename must match the ``legacy_path`` (it cannot be renamed, which is already the case for course files).
- All File components with legacy paths can be accessed using either of the reference formats mentioned above (``/static/example.png`` or ``[https://courses.example.com]/asset-v1:org+course+run+type@asset+block@example.png``)
- All file components served using a legacy path are served from shared course-wide URL namespace (``.../legacy/example.png``) so that relative references among legacy asset files continue to work.

Naturally, the ``FileComponentMetadata`` table will enforce that ``legacy_path`` is unique per learning package.

2. File components can be referenced using new ``oex-asset:`` URL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When editing an XBlock Component (stored in ``openedx_content``, not modulestore), whether using a visual editor or editing OLX directly, users who want to reference a static asset file (e.g. embed an image) can browse through all files attached to the current component (with the ``/static/`` prefix to indicate they're public), as well as all shared File components in the course (or copied/linked into the course from a library). They can then use the new ``oex-asset:`` URL scheme to uniquely identify that asset::

    oex-asset:{alias}/{path}

- ``{alias}`` is generally the ``component_code`` of the File component that holds the asset file in question (see next sections for details)
- ``{path}`` is the file's path within that File component. It may contain slashes, and must be percent-encoded as any URL path is.

A reference with an empty alias refers to a file attached to the referencing component itself::

    oex-asset:/{path}

Examples, for an HTML component that has linked the File component ``moon-orbit-illustration`` under its default alias:

.. code-block:: html

    <img src="oex-asset:/diagram-1.png">                                 <!-- this component's own file, static/diagram-1.png -->
    <img src="oex-asset:moon-orbit-illustration/moon-orbit.svg">         <!-- an SVG file in a linked File component -->
    <a href="oex-asset:lecture-notes/week1/notes.pdf">Week 1 notes</a>   <!-- nested path in a linked File component -->

This scheme:

- **Is easy to detect.** ``oex-asset:…`` does not occur in content for any other reason, so a single pattern finds every reference in any field type (HTML, problem XML, JSON, CSS ``url()``, etc.) with no false positives from platform or theme ``/static/`` URLs. (The only exception is that course content *about* OLX authoring itself would need to escape ``oex-asset:`` if it occurs in text.)
- **Is unambiguous.** A reference names exactly one link and one path. Linking a new File component or adding files to one can never change what an existing reference resolves to.
- **Contains no context.** It holds no hostname, course key, library key or version. Copying content to a rerun, another course, a library or another instance doesn't require rewriting any references, as long as the links come along (see decision 4).
- **Can be rewritten trivially and unambiguously.** Browsers cannot load an ``oex-asset:`` URL directly; like the legacy formats, it must be rewritten before it reaches the browser. At render time, each ``oex-asset:{alias}/{path}`` is replaced with the :ref:`openedx-content-adr-0005` URL for the draft (Studio) or published (LMS) version of the linked File component, or of the referencing component for an empty alias. Visual editors do the same when loading content, and reverse it when saving (see decision 5). Unlike ``/static/`` rewriting, this never needs to guess whether a URL is an asset reference.
- **Is not an opaque key.** Unlike the existing ``asset-v1:...`` scheme, the ``oex-asset:`` URL format is meant as a portable, URL-shaped scoped identifier, local to a learning package. It is not an opaque key, nor is it unique across courses.

3. References using ``oex-asset:`` URLs are tracked with ``FileComponentLink``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When editing an XBlock/Component such as a Text (HTML) component, course authors can freely copy and paste ``oex-asset:`` references into the HTML/OLX to reference any available shared File components in the course (including ones copied from content libraries). When the author saves their changes, the OLX will be scanned for ``oex-asset:`` references, which can be done unambiguously, unlike the legacy asset reference schemes. As part of saving the new ``ComponentVersion``, a new model, ``FileComponentLink``, records each File component reference:

- ``component_version``: the :class:`ComponentVersion` that uses the File component.
- ``file_component``: the File :class:`Component` being used. It must be in the same :class:`LearningPackage` as the component version.
- ``alias``: a short identifier for the File component. It is identical to the File component's ``component_code``, except when the ``ComponentVersion`` needs to reference a File component from a content library and a code collision occurs with the existing course files.

Links are attached to a *version*, not to the Component, for the same reason :class:`ComponentVersionMedia` is. The set of File components a component uses is part of its content, so linking or unlinking a File component creates a new version of the component. That version can be edited in draft, published, reverted and exported like any other change. When a component version is created, its links are copied forward from the previous version unless they are changed. This is a snapshot, as with :class:`ComponentVersionMedia`, but a component typically has zero to a handful of links, so the cost is small.

Each ``FileComponentLink`` also creates a :class:`PublishableEntityVersionDependency` from the component version to the File component. This means the existing side-effect machinery works unchanged: editing a draft File component marks every component that uses it as having unpublished changes, and the publish log records the change against those components as well. It also answers "where is this asset used?" with a query instead of by parsing content (though this excludes "legacy path" references).

4. The ``alias`` is used to avoid ID conflicts when pasting/linking content from another Learning Package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When an XBlock Component with references to shared File component(s) is pasted/copied from one Learning Package to another, e.g. from a library to a course, or from a library to another library, all of the shared File component(s) that it references need to be copied into the destination Learning Package as well.

A new principle that we want to uphold is that **OLX should not change** when a Component is copied from one Learning Package to another. So if the Component in question has some HTML like ``<img src="oex-asset:shared-file5/example.png" alt="Example" />``, then we want to ensure that ``shared-file5`` can refer to the correct shared File component copied from the original Learning Package, and not conflict with a potentially unrelated File component in the destination Learning Package that uses the same ``shared-file5`` identifier as its ``component_code``. By avoiding any need to rewrite the OLX, we can reduce storage space, consolidate ``Media`` rows, and facilitate simpler comparison of content.

When copying a Component with references to shared File components to a new Learning Package, each referenced File component is handled as follows:

- First, if the destination Learning Package already has a File component that is a downstream copy of the same upstream File component (see decision 6), that existing copy is reused, even if its ``component_code`` or content differs. The copy is not updated as a side effect of pasting, since that would change every other component that uses it; if the source uses a newer upstream version, the author can sync the File component as usual. Matching on upstream prevents repeated pastes or imports of the same library content from creating a new copy of the File component each time.
- Otherwise, if the destination Learning Package already has a File component with identical ``component_code`` and media asset file hash(es), it is reused and no File components need to be copied. Identical bytes don't prove that it's the same asset, but reusing a File component with the same code and content is harmless.
- In both of the above cases, only the main Component needs to be copied, but we still have to create ``FileComponentLink`` and :class:`PublishableEntityVersionDependency` objects to track the relationship, using the original ``component_code`` as the ``alias`` if the reused File component's code differs.
- Otherwise, the File component is copied into the destination Learning Package, and becomes a downstream of the original if the original is in a library. If the destination Learning Package already has a conflicting File component, with identical ``component_code`` but different media asset file hash(es), the copy is given a different ``component_code``, and the ``FileComponentLink`` created for it will use the ``alias`` column to alias this new code to the old code, so that no changes to the OLX are required.

5. Editors should de-reference full URLs on save
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In the course of editing courseware, authors may use their browser's "Copy Image URL" to copy the URL of an image and paste it elsewhere, resulting in a full, rewritten URL like ``https://demo.openedx.org/assets/content_libraries/lib:Axim:200/xblock.v1:problem@multi_choice_8/v4/static/images/fig1.png`` ending up in the OLX. If any such URLs are detected, they should be automatically rewritten to the ``oex-asset:`` format.

Note: Only rewrite full URLs that point into the same learning package. Rewriting a URL that points at another course's asset would create a link across packages, which we want to avoid.

6. Versioning follows the library content model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

There are two independent layers of versioning, and they work the same way as for library components used in courses today.

**Within a learning package, links are unpinned.** A ``FileComponentLink`` names a File component, not a version of it, just as a unit's unpinned children are components rather than component versions. The version used is determined by where the content is rendered:

- In Studio (authoring), the component's draft is rendered, and ``oex-asset:`` references resolve to the **draft** version of each linked File component.
- In the LMS, the component's published version is rendered, and ``oex-asset:`` references resolve to the **published** version of each linked File component.

Editing a File component's draft therefore immediately changes what authors see in every component that uses it, and marks those components as having unpublished changes (via the dependency from decision 3). Learners see nothing until the File component is published. Publishing a component also publishes the draft versions of the File components it links to, the same way publishing a unit publishes its unpinned children, so a published component never references an unpublished File component.

**Between a library and a course, updates are pulled explicitly.** A course copy of a library File component is a downstream of the library File component, and it tracks the upstream the same way downstream course blocks do:

- ``version_synced``: the library File component's published version number that the course copy was created from or last synced with.
- ``version_declined``: the latest upstream version the course author chose not to sync, if any.

Only *published* library versions are ever copied into a course. Library drafts are never visible to courses.

Upstream tracking for File components uses the same platform-level mechanism as components (currently the ``ComponentLink`` model and related sync APIs in ``openedx-platform``). The ``upstream`` model may need adjustment, since a File component is not an XBlock and has no XBlock fields to store ``upstream_version`` in. The details are left to the platform implementation.

7. Backwards compatibility in OLX export
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When exporting to a course tarball, we want the result to be importable on older versions of Open edX that do not support the ``oex-asset:`` scheme. To achieve this:

- Legacy File components and legacy references like ``/static/x.png`` continue to import/export as before (no changes)
- On export, shared File components that don't have a ``legacy_path`` or that use ``oex-asset:`` references will be included in the tarball at e.g. ``static/-oex-asset-[code]/x.png`` and references in the OLX will be rewritten to ``/static/-oex-asset-[code]/x.png``
- On import, that will be reversed, and File components will be created and OLX will be rewritten to use the ``oex-asset:`` format.

Consequences
------------

- Asset usage ("which components use this file?") becomes a simple query over ``FileComponentLink``, rather than a best-effort parse of all course content.
- For content using ``oex-asset:`` references: reruns, clipboard copy/paste, library import and library sync never need to rewrite references in component content. They only need to copy or remap links, keeping their aliases.
- A course's learning package always contains every asset it uses, so it can be exported, backed up and restored in isolation.
- Every component version snapshots its links, adding a small number of rows per component version for components that use shared assets.
- Three formats (``oex-asset:`` and legacy ``/static/`` or ``[https://host]/asset-v1:``) have to be supported by the rendering pipeline indefinitely.
- The ``oex-asset:`` scheme must be allowed (or at least ignored) by every sanitizer and editor that touches authored content.
- Library File components used in many courses are copied into each course. This costs rows, not bytes, because blobs are deduplicated per org.
- The ``backup_restore`` format must be extended to serialize ``FileComponentLink`` rows with each component version.
- An aliased reference can resolve differently when the ID string is pasted into another component. If component A links ``shared-file5`` as an alias for the File component ``shared-file5-2`` (decision 4), and an author copies the string ``oex-asset:shared-file5/x.png`` from A into component B in the same course, it will resolve to the course's own ``shared-file5`` instead. Editors can detect this on save and warn the author, prompting them to choose which File component they meant.
- Dynamically built paths in content won't work. JS that does ``"oex-asset:" + name`` will fail to load, because the reference won't be detected and the ``FileComponentLink`` won't be created.
- Publishing one component publishes the shared File component, which changes what learners see in every other component that uses it. That's consistent with how unit children behave, but may be surprising.
- **Copying library content assets into a modulestore course is unchanged for now**. Because modulestore content doesn't support ``Component`` / ``FileComponentLink``, the existing logic for handling static files (copy them, rewrite the OLX ``/static/filename`` references as needed) will still apply until courseware is migrated to ``openedx_content``.

Rejected Alternatives
---------------------

Authors must explicitly create ``FileComponentLink`` references themselves
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We could say that authors must create ``FileComponentLink`` references in the UI (e.g. using a file picker) before ``oex-asset:`` style references in the OLX will work. However, this is unduly burdensome for course authors, compared to the relative simplicity of copying and pasting ``oex-asset:...`` strings, and leaving the system to auto-create (or delete) ``FileComponentLink`` references as needed.

Linking directly to File components in libraries
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Letting a course component link to a File component that lives in a library's learning package would avoid copying. It was rejected because it breaks learning package isolation (deleting or un-publishing a library asset would break courses), can't use :class:`PublishableEntityVersionDependency` for side effects, would give courses two different versioning and permission models for assets depending on where they came from, and is inconsistent with how library components are used in courses.

Keeping ``/static/`` with collision checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The smallest change would be to keep ``/static/{path}`` as the only reference format, resolve it through the component's links, and reject links that would create path collisions. This would fix ambiguity, but not detection: ``/static/`` would still be hard to tell apart from platform URLs or incidental content (e.g. a course on HTML with examples that include ``/static/`` but are unrelated to course assets). Rejecting collisions would also prevent legitimate cases, such as two HTML-package File components that each contain an ``index.html``, and adding a file to a linked File component could still change what another reference resolves to.

Referencing File components by UUID or ``component_code`` instead of an alias
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

UUIDs are unique per learning package, so references would need rewriting on every rerun and every copy. Referencing the File component's ``component_code`` directly would also be stable within a package, but a library's codes and a course's codes can collide when library File components are copied into a course, and the copy might need a different code. An alias on the link keeps the reference stable no matter what the target is called in the package it ends up in. Since the alias defaults to ``component_code``, the two are usually identical in practice.

Template placeholders instead of a URL scheme
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Placeholders such as ``{{ asset "moon-orbit-illustration/moon-orbit.svg" }}`` are equally easy to detect, but they are not URLs. HTML parsers, WYSIWYG editors and sanitizers mangle them in ``src`` and ``href`` attributes, while a URL-shaped reference survives those tools as long as the scheme is allowed.

``oex-asset://{alias}/{path}`` with an authority part (``//``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Adding ``//`` after the scheme would be a little more conventional, and would work equally well since references are rewritten before rendering either way. It is rejected because it makes every reference longer without adding anything, and because the alias is not a host: it is only meaningful relative to the referencing component's links.

Also, URL parsers like `The JavaScript URL interface <https://developer.mozilla.org/en-US/docs/Web/API/URL>`_ still parse a string like ``oex-asset:foo/bar.txt`` as a valid URL (with an empty ``host`` value).

Relative ``./oex-asset:{alias}/{path}`` references
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Prefixing references with ``./`` would make them relative URLs rather than a custom scheme, so browsers would request e.g. ``/xblock/{usage_key}/oex-asset:foo/bar.png`` and the server could resolve it without any rewriting. This was rejected because:

- A relative URL resolves against whatever page the content is embedded in: the LMS unit iframe, the authoring MFE (often on a different host from Studio), mobile apps, the Blocks API, LTI consumers, emails and so on. Every one of those would need a route (or a ``<base>`` override) that knows how to resolve ``oex-asset:`` paths, and some API consumers can support neither.
- Whether a draft or published version is served would depend implicitly on which route answered, instead of being explicit in the URL as in :ref:`openedx-content-adr-0005`, and every asset would need an extra redirect.
- It is fragile: if an author, WYSIWYG editor or URL normalizer drops the leading ``./``, the reference silently becomes an ``oex-asset:`` URL that the browser cannot load.

Rewriting is needed anyway for the legacy ``/static/`` and ``asset-v1:`` formats, so supporting one more (unambiguous) pattern in the same pipeline costs little.

``asset:`` as the name of the asset URL reference
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``asset://...`` URL scheme is used by other frameworks like Tauri, so we choose a more unique name that specifically references the Open edX platform, i.e. ``oex-asset:...``.

Pinning links to specific File component versions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``FileComponentLink`` could name a specific version of the File component, like a pinned container child. This would stop a File component edit from affecting components that use it, but authors expect that replacing a shared image updates it everywhere in the course. Pinning also creates a different version of the same asset for every component, and requires a new version of every using component each time the File component changes. Controlled updates are already provided one layer up, between a library and a course (decision 6).

TODOs and open questions
------------------------

- Contentstore builds asset-v1 block names by replacing ``/`` with ``_``, which loses information. ``images/a.png`` and ``images_a.png`` produce the same key, so going from key to ``legacy_path`` isn't always 1:1. The ADR should specify how that is resolved.
- Reconcile the ``.../legacy/example.png`` asset URLs with :ref:`openedx-content-adr-0005`
- 0013 decision 7 derives component_code from the filename with subdirectories dropped, so migrated nested legacy files will collide on code.
- What happens when a File component that others link to is deleted? (Can we ensure the published Component's old reference still resolves to the file component version it was last linked to?)
- Referencing a ``private`` File component from learner-facing content should warn.
- Specify whether syncing a library component also syncs the File components it links to, and what happens if the course edited its copy locally.
- Export: Files attached to a component (``oex-asset:/path``) also need a rule for export to older platforms. The ``locked`` and ``title`` need to go into ``policies/assets.json``. Private static files will have to be in a separate folder outside of ``static/`` and won't be backwards compatible.
