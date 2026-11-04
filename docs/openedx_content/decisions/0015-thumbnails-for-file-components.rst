.. _openedx-content-adr-0015:

15. Thumbnails for File Components
===================================

Status
------

Draft. Depends on :ref:`openedx-content-adr-0013` and :ref:`openedx-content-adr-0014`.

Context
-------

When a course author uploads an image to a course's "Files", represented as a File component (a :class:`Component` of type ``file``) per :ref:`openedx-content-adr-0013`, Studio automatically generates a smaller thumbnail image. This lets the Files page show a preview of each image without loading the full file.

Three properties of the existing design shape this decision:

- :class:`PublishableEntityVersion` objects, which back every :class:`Component` version (including a File component's), are created once and never updated.
- A thumbnail is not authored content: it is a derived, disposable artifact that can always be regenerated from the original file. The legacy contentstore already treats it this way — it is generated whenever an asset is uploaded or a course is imported, but never written into a course's export package.
- :class:`Media` is a self-contained model for storing and deduplicating binary data by content hash within a :class:`LearningPackage`. Its own documentation explicitly invites third-party apps to extend it with a ``OneToOneField``, independent of any :class:`Component`.

Decisions
---------

1. Thumbnails are not versioned
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Creating or regenerating a thumbnail never creates a new version of the File component. The source image continues to be versioned normally, like any other File component content, but the thumbnail itself is not authored content and does not participate in that versioning.

2. Thumbnail metadata lives platform-side, not in ``openedx_content``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A new Django model, ``ImageMediaThumbnail``, owned by openedx-platform, is keyed to the **source image**, not to the File component — via a ``ForeignKey`` referencing the source image's ``ImageMedia`` entry (:ref:`openedx-content-adr-0013`), and records which :class:`Media` object is the current thumbnail for that source image, together with any other presentation-adjacent fields the platform needs for that asset. Consistent with Decision 1, this model is a plain, mutable Django row: creating a new thumbnail or changing these fields is a simple update, not a new version.

Keying on ``ImageMedia`` rather than the File component means a thumbnail is a property of a specific, immutable set of image bytes. This matters because a File component can hold more than one image over its history, and, per :ref:`openedx-content-adr-0013`'s Decision 2, a single File component can also group more than one image file at once. It also means, by construction, that only ``Media`` already confirmed to be a valid image (i.e. one that has an ``ImageMedia`` entry) can ever have a thumbnail.

3. Thumbnail bytes are stored as ``Media``, referenced by a direct foreign key
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The thumbnail image itself is written to ``openedx_content`` as an ordinary :class:`Media` object. The platform-side model from Decision 2 holds a second, separate foreign key to that :class:`Media` row — alongside its key relationship to the source image's ``ImageMedia`` — and regenerating a thumbnail simply repoints that second foreign key at a new, or existing and content-identical, :class:`Media` row.

Critically, neither the source image's :class:`Media` nor the thumbnail's :class:`Media` gain any new association with a :class:`ComponentVersion` through ``ComponentVersionMedia`` as a result of this model. It is a sibling piece of data that happens to live in the same :class:`LearningPackage`, addressed and deduplicated the same way as any other :class:`Media`, but outside of the versioned graph that the File component participates in.

This gives the thumbnail the storage and deduplication properties of :class:`Media`, including sharing bytes across reruns of a course via ``media_file_namespace``.

4. The platform-side model supports multiple thumbnail variants per source image
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Rather than a single thumbnail per source image, the model from Decision 2 is keyed on ``(image_media, variant)``, where ``image_media`` is the ``ForeignKey`` to the source image's ``ImageMedia`` from Decision 2, and ``variant`` is a short string identifying the size or context a thumbnail was generated for (e.g. ``"default"``), with a ``UniqueConstraint`` on that pair. Each ``(image_media, variant)`` row has its own foreign key to a :class:`Media` row for the thumbnail itself, per Decision 3.

Only one variant (``"default"``) is ever generated for now. Nothing here commits to building multiple sizes now; it only keeps the schema from needing a breaking migration if and when a second size is needed.

That single ``"default"`` size is meant to serve more than one consumer at once: Studio's Files page preview, and the "card" preview shown when browsing or searching for content to reuse in a content library. Choosing its dimensions to match that card size means both use cases share the same variant, rather than needing a second one just to support the library card view.

5. Thumbnail generation remains synchronous, at upload and at import time
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Thumbnails continue to be generated eagerly, at the same points where the legacy contentstore already generates them: when an author uploads an image, and when a course is imported. No thumbnail is generated speculatively ahead of need, and none is generated lazily at serving time.

The same idea applies to a course rerun, to copy/paste, and to syncing a downstream File component with a newer published version of its upstream. Because the model from Decision 2 is keyed on the source image's ``ImageMedia`` rather than on the File component, this often requires no action at all: if the destination ends up with the exact same source :class:`Media` (and therefore the same ``ImageMedia``, since it is a pure function of those bytes), the existing thumbnail row is already correct, since the key it's looked up by hasn't changed. A new thumbnail only needs to be generated when a genuinely new source :class:`Media` appears with no existing thumbnail row — for example, a File component copied into a Learning Package that doesn't already have that exact image, or a sync that pulls in a new, edited version of the source image. In that case, the new ``Media`` also needs its own ``ImageMedia`` before a thumbnail can be keyed to it, but that is already required independently of thumbnails, per :ref:`openedx-content-adr-0013`.

6. Thumbnails are served by a dedicated platform-side endpoint, not the general asset-serving API
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The URL scheme :ref:`openedx-content-adr-0005` defines is resolved by looking up ``(component_version, path)`` in ``ComponentVersionMedia``, and per Decision 3, a thumbnail's :class:`Media` is never registered there. So a thumbnail cannot be served through that same endpoint.

Instead, the platform-side app that owns the model from Decision 2 exposes its own endpoint, e.g. ``GET /api/contentstore/v2/file_components/{component_key}/thumbnail``. Given a File component, it resolves the source image's current draft :class:`Media` through ``ComponentVersionMedia`` (the same way any other file attached to that version is resolved), then that ``Media``'s ``ImageMedia``, looks up the thumbnail's :class:`Media` for that ``(image_media, variant)`` from the model in Decision 2, and serves it, reusing the same underlying serving mechanism :ref:`openedx-content-adr-0005` already defines for any ``Media``.

Consequences
------------

- No schema changes are required in ``openedx_content``. The File component remains, as :ref:`openedx-content-adr-0013` describes it, a dumb mapping of paths to :class:`Media`.
- Thumbnails do not participate in draft/publish, backup and restore, or any other machinery that assumes a :class:`PublishableEntityVersion` is immutable once created, because a thumbnail never becomes one.
- Thumbnails are not included in a course's export package, and are regenerated from the source asset on import. This preserves the behavior the legacy contentstore already has today. No new mechanism is introduced to make thumbnails portable, because they were never treated as portable content: the source image is the asset of record, and the thumbnail is a derived, disposable artifact of it.
- Regenerating a thumbnail repoints the platform-side model's foreign key at a new :class:`Media` row; it does not delete or overwrite the previous one, which is left unreferenced. ``openedx_content`` has no deletion or garbage-collection mechanism for individual :class:`Media` rows today.
- Although the platform-side model is not versioned, the thumbnail for a past version of a File component can still be retrieved: since the model is keyed on the source image's ``ImageMedia`` rather than on the File component, looking up which :class:`Media` a past :class:`ComponentVersion` used (via ``ComponentVersionMedia``, which already records this), then that ``Media``'s ``ImageMedia``, and then looking up that ``ImageMedia``'s thumbnail works for any version, past or present.
- Thumbnails are naturally deduplicated across File components: if two different File components happen to hold byte-identical source images (the same :class:`Media`, whether by coincidence or because one was copied from the other), they share a single thumbnail row and a single thumbnail :class:`Media`, rather than each generating and storing its own copy.

Rejected Alternatives
---------------------

Storing the thumbnail in an ``openedx_content`` metadata model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:ref:`openedx-content-adr-0013` introduces ``FileComponentMetadata``, a separate, unversioned model keyed by a ``OneToOneField(primary_key=True)`` to :class:`Component`, for per-asset metadata that does not fit within the existing :class:`Component`/:class:`Media` models (``locked``, ``private`` and ``legacy_path``). Those fields are generic: they apply the same way to a File component regardless of what kind of file it holds. Decision 2 of this ADR follows that same trivial-unversioned-model *shape*, but for a purpose specific to image content, not a generic one.

:ref:`openedx-content-adr-0013` also introduces ``ImageMedia``, a model that extends :class:`Media` with a ``OneToOneField`` to capture metadata "purely derived from the image byte data itself" (e.g. dimensions). Storing the current thumbnail as a field on ``ImageMedia`` was considered instead, since it would use the same extension pattern and the same :class:`Media`-backed deduplication as Decision 3.

We reject this because a thumbnail does not meet the "purely derived from the image byte data itself" bar that ``ImageMedia`` is scoped to. Unlike dimensions, which are a fixed function of the bytes alone, the correct thumbnail for a given image also depends on an external, configurable parameter — the target size and resizing algorithm. If that parameter changes, the correct thumbnail for the same, unchanged image bytes changes with it — something that can never happen to an image's dimensions.

More fundamentally, a thumbnail only makes sense for image content, is driven by that platform-configurable parameter rather than being a fixed function of the bytes, and, per Decision 4 of this ADR, needs to support more than one value per image — since ``ImageMedia`` is 1:1 with a single :class:`Media`, a single ``thumbnail`` field on it could not represent more than one size, short of adding one field per size or a separate table keyed by size. None of that fits a model that is meant to stay a simple, purely-derived reflection of a single image's byte data, nor the generic, file-type-agnostic pattern ``FileComponentMetadata`` follows above.

This is a different thing from Decision 2's choice to key the platform-side model on ``ImageMedia`` itself, rather than on the File component. Both approaches use ``ImageMedia`` to identify the source image, but Decision 2's model is a new, purpose-built table that merely holds a ``ForeignKey`` to ``ImageMedia`` (and supports multiple ``variant`` rows per ``ImageMedia``), rather than a field bolted directly onto ``ImageMedia`` itself, whose own documented contract is to stay a pure function of an image's bytes alone. Referencing ``ImageMedia`` from an external table doesn't have to honor that contract; adding a field to ``ImageMedia`` would.

Creating a new ``openedx-core`` app for this metadata
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Rather than putting the model from Decision 2 in openedx-platform, we could add a new sibling app to ``openedx-core`` itself, following the same pattern ``openedx_learning`` and ``openedx_catalog`` use, keyed with ``ForeignKey``\ s to the source image's ``ImageMedia`` and the thumbnail's :class:`Media` the same way. This is technically straightforward: a new app can sit above ``openedx_content`` without violating the layering enforced by ``.importlinter``.

We reject this for now because the use cases are very limited at first, and it may not be worth the packaging and versioning overhead of a new, small ``openedx-core`` app for something only used in one place. This is not because the size and resizing algorithm used to generate a thumbnail would force ``openedx-core`` to depend on platform-specific configuration: a generic API, where the caller supplies the desired dimensions and the app itself has no notion of "variants" of its own, would avoid that coupling. If thumbnails gain more consumers or more variants in the future, revisiting this as an ``openedx-core`` app with such a generic, parameter-driven API is a reasonable next step.

Grouping the thumbnail as a second file within the source File component
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:ref:`openedx-content-adr-0013` allows a File component to group multiple related files together (e.g. different resolutions of the same image), which could in principle hold the thumbnail alongside the original. This was rejected because ``ComponentVersionMedia`` is a full snapshot of a version's media, not a delta: regenerating the thumbnail would force a new :class:`ComponentVersion` that also rewrites the unrelated, unchanged association to the original file. Grouped files also carry no notion of which one is a derived thumbnail versus the source, so this approach would still require a naming convention to tell them apart, without avoiding the versioning cost.

Making ``ComponentVersionMedia`` mutable for derived files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To avoid the cost above, we considered allowing certain paths within a ``ComponentVersionMedia`` mapping to be updated in place, without creating a new :class:`ComponentVersion`. This was rejected because :class:`PublishableEntityVersion` is immutable by design across all of ``openedx_content`` — other systems (backup and restore, course export/import, version history and diff tooling) already depend on a version being a fixed, reproducible snapshot in time. Carving out a mutability exception for one kind of derived file would undermine that guarantee platform-wide, for a benefit that only this feature needs.

Keeping thumbnails in the legacy MongoDB-backed contentstore
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Leaving thumbnails behind in the legacy contentstore would keep a MongoDB dependency alive for one narrow piece of functionality, working against the eventual retirement of ``contentstore``.

An independent storage model with no relationship to ``Media``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A platform-side model with its own image field and storage backend, entirely disconnected from ``openedx_content`` — the pattern ``edxval.VideoImage`` already uses for video thumbnails — was considered. It is a simpler design, but it forfeits the content-addressed deduplication that :class:`Media` already provides, and it does not follow the extension pattern that :class:`Media` explicitly documents for third-party apps.

Generating thumbnails on the fly at serving time
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Rather than generating a thumbnail eagerly at upload and import time, we considered generating it lazily, the first time it is requested, and caching the result. This would avoid spending storage and CPU on thumbnails that are never viewed, and would let the same design serve multiple sizes on demand instead of a single fixed one. We reject it for now on the grounds of added complexity: it requires a caching strategy and changes to the asset-serving view, beyond what this ADR needs to resolve. Nothing about this design precludes adding on-the-fly generation as a future enhancement layered on top of the same storage model.

Open Questions
--------------

- **Exact location of the platform-side model.** This ADR specifies that the thumbnail metadata model lives in openedx-platform, but does not pin down which Django app owns it. This is left to implementation.
- **Thumbnails of XBlocks, not just images.** This ADR is scoped to thumbnails of image files. A related, but architecturally distinct, need is a visual preview of an XBlock component itself (e.g. a Problem or an HTML interactive), generated by rendering the component rather than resizing an existing image — useful for content library "card" views that cover any component type, not just ones with an attached image. That is out of scope here, but worth exploring as a future extension.
