.. _openedx-tagging-adr-0010:

10. Mutability and Identity of Taxonomy Tags
==============================================

Status
------

Accepted

Context
-------

Taxonomy tags rely on three different identifiers, each serving a distinct purpose:

#. ``id``: the internal, environment-specific database primary key.
#. ``value``: a string used for display, search indexing, and short-lived API interactions. It is unique within a taxonomy, and mutable.
#. ``external_id``: a string used to track a tag's identity across system boundaries, particularly during long-lived use cases like taxonomy import and export.

The system treats ``external_id`` as immutable. The architectural tension arises when an ``external_id`` legitimately needs to change - for example, to correct a typo, or to align with an upstream terminology change (e.g. updating an external standard from "equity considerations" to "use considerations").

If ``external_id`` is strictly immutable, a user must delete the existing tag and create a new one, destroying all existing object associations (foreign keys) tied to that tag.

Conversely, if ``external_id`` were freely mutable, we would break the mechanism used to maintain continuity during taxonomy import/export. Since internal database ``id`` values cannot be used across different environments (they are auto-incremented and environment-specific), the system would have no reliable way to map an incoming, updated tag to the existing tag already in the database.

The core problem: how do we uniquely identify tags across decoupled environments while allowing administrators to mutate both display values and external identifiers, without destroying existing data relationships?

Decision
--------

Role and scope of identifiers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To maintain clean boundaries between internal systems and external data mobility, the roles of our identifiers are strictly defined:

* **Internal database ``id``:** reserved for internal, environment-specific relational mapping. It is not exposed in import/export payloads, since it has no cross-environment portability.
* **``value``:** the mutable, human-readable label. It is unique within a taxonomy, but is not relied upon as a stable identifier for import/export.
* **``external_id``:** the primary identifier used to match a ``Tag`` across a taxonomy's own import/export. Its scope is limited to that: it is not the identifier used when content tags (``ObjectTag``) reference or export their taxonomy and tag. Content tags instead cache the tag's ``value`` and the taxonomy's ``export_id`` (see :ref:`openedx-tagging-adr-0006`), which is a separate, taxonomy-level identifier.

Handling identifier mutability
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* The taxonomy editor UI displays external IDs, but doesn't allow changing them. It may allow specifying one when creating a tag, and/or generate one based on the value if none is specified.
* In the rare case where an external ID needs to be changed, an administrator can do so via the Django admin UI.

This matches the current implementation: the tag update REST API only accepts an updated ``value`` (``Taxonomy.update_tag()`` only supports updating a tag's value), while ``external_id`` can only be set at tag creation, or changed directly through the Django admin.

Changelog
---------

2026-07-07:

* Finalized the decision based on the PR discussion, replacing the previously open options for handling identifier mutability.
* Moved from ``docs/decisions/`` to ``docs/openedx_tagging/decisions/``, and renumbered, to match current ADR location and numbering conventions.
* Clarified that ``external_id``'s scope is limited to a taxonomy's own import/export, distinct from ``Taxonomy.export_id``, which content tags use to reference their taxonomy (see :ref:`openedx-tagging-adr-0006`).
