.. _openedx-content-adr-0011:

11. Do not implement copy-on-write
==================================

Status
------

Proposed.

Context
-------

Course reruns, "duplicate this component", and library-to-course copies all produce content that is mostly identical to its source. We considered adding copy-on-write to the ``publishing`` applet, so that two different :class:`PublishableEntity` objects could share the same :class:`PublishableEntityVersion` rows until one of them is edited, at which point only that entity would get a new version. The main goal of this would be to let each copy carry its full edit history (its entire provenance chain from the moment it was created, surviving across re-runs, duplications, and linking from a library into a course), and to do so in a normalized way without needing to duplicate all of the historic version rows. Other goals would be to save storage space, and to improve rerun performance by dramatically reducing the number of database rows that need to be copied to create a course rerun.

Today the schema rules this out by design. A :class:`PublishableEntityVersion` has a single ``entity`` foreign key with a ``(entity, version_num)`` unique constraint; ``Draft.version`` and ``Published.version`` are one-to-one fields; and the content models mirror this with ``ComponentVersion.component`` and ``ContainerVersion.container``. A version belongs to exactly one entity, and an entity's history is the set of versions that point at it.

Decision
--------

We will keep the status quo. A :class:`PublishableEntityVersion` continues to belong to exactly one :class:`PublishableEntity`, and copying content (including reruns) will require creating new entity, version, draft and published rows in the destination. Where history is wanted for a copy, it must be fully copied too.

The reasons:

- **There are other ways to track a component's history.** Between :class:`DraftChangeLog`, :class:`PublishLog`, openedx-platform's :class:`ComponentLink`, and potential future ``RerunLog`` models, we can find ways to show a component's complete provenance history without necessarily implementing copy-on-write nor completely duplicating it.
- **Copy-on-write only pays off inside a shared learning package.** Sharing versions across learning packages would violate the fundamental isolation principles that :class:`LearningPackage` is designed to achieve. It would break media ownership, cascade deletion and backup self-containment. So reruns would have to share one :class:`LearningPackage`, which leads to questions of how to scope entity codes per run, how to make publishing, draft listing, backup and deletion run-aware, and how to keep very large packages workable.
- **Storage savings from copy-on-write specifically are insignificant.** :class:`Media` is already deduplicated by hash within a learning package. Once copies live in the same package, a plain copy of a component version links to the existing :class:`Media` row, so OLX text is stored only once, with or without copy-on-write; the same applies to asset files like images. The major storage space savings come from the hash-based deduplication within each learning package, not from copy-on-write specifically, and future ADRs will explore other ways of achieving similar benefits across learning packages.
- **Containers cannot be shared anyway.** A :class:`ContainerVersion` points at an :class:`EntityList` whose rows reference specific child entities. A copy needs its own container versions regardless, so only leaf component versions would ever be shared.
- **The blast radius is large.** Both rejected designs below change public models exposed through ``models_api``, require a DEPR of parts of the ``versioning`` helper, touch every applet, and need coordinated changes in ``openedx-platform`` (the XBlock runtime's version lookups, library history, restore, upstream sync and the clipboard).

A future ADR will address reducing storage space by consolidating media across the learning packages that hold different versions of the same course, which captures most of the storage benefit without changing the publishing model.

Rejected Alternatives
---------------------

Both alternatives share some common changes: remove ``PublishableEntityVersion.entity``, ``ComponentVersion.component``, and ``ContainerVersion.container``; change ``Draft.version`` and ``Published.version`` to plain foreign keys instead of 1:1 keys; and presumably give reruns a shared learning package.

Version chain: drop ``entity``, add ``previous_version``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Remove ``PublishableEntityVersion.entity`` entirely, associate entities with versions only through :class:`Draft` and :class:`Published`, and add a ``previous_version`` foreign key to :class:`PublishableEntityVersion` with the rule ``version_num = previous_version.version_num + 1``. A newly created copy of an entity would share the source's draft pointer and therefore its whole version history chain.

Under this approach, ``version_num`` stops being unique per entity: resetting a draft and editing again produces a second version with the same number. That would break every place that treats ``(entity, version_num)`` as an identity, including the XBlock runtime's ``@version`` lookups, backup and restore, and upstream sync. It also removes the database constraint that today turns concurrent edits into an ``IntegrityError``, which would have to be replaced with optimistic locking in ``set_draft_version``.

Link table: ``PublishableEntityVersionLink``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Move the entity relationship into a many-to-many table ``(entity, version, version_num)`` with unique constraints on ``(entity, version)`` and ``(entity, version_num)``. A copy is made by copying the source's link rows, so each entity keeps its own unique, monotonic numbering, the concurrency constraint survives, and the ``versioning`` helpers keep their contract. An optional ``previous_version`` field can still record lineage.

This was the preferable of the two designs and would be the starting point if copy-on-write is revisited. It was rejected for now for all the reasons listed above, rather than for shortcomings of this specific approach to implementing copy-on-write.
