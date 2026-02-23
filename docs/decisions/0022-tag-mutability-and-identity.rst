20. Mutability and Identity of Taxonomy Tags
============================================

Context
-------

Taxonomy tags currently rely on three different identifiers, each serving a distinct purpose:
1. 'id': The internal, environment-specific database primary key.
2. 'value': A string used for display, search indexing, and short-lived API interactions (unique within a taxonomy).
3. 'external_id': A string used to track tag identity across system boundaries, particularly during long-lived use cases like course imports and exports.

Currently, the system implicitly treats 'external_id' as immutable. The architectural tension arises when an 'external_id' legitimately needs to change—for example, to correct a typo or to align with an upstream terminology change (e.g., updating an external standard from "equity considerations" to "use considerations").

If 'external_id' is strictly immutable, a user must delete the existing tag and create a new one, thereby destroying all existing object associations (foreign keys) tied to that tag.

Conversely, if we allow 'external_id' to be mutable, we break the mechanism used to maintain continuity during import/export workflows. Since internal database ''id''s cannot be used across different environments (as they are auto-incremented and environment-specific), the system would have no reliable way to map an incoming updated tag to the existing tag in the database.

The core architectural problem is: **How do we uniquely identify tags across decoupled environments while allowing administrators to mutate both display values and external identifiers without destroying existing data relationships?**

Decisions (Draft)
-----------------

1. Role and Scope of Identifiers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To maintain clean boundaries between internal systems and external data mobility, the roles of our identifiers must be strictly defined:

* **Internal Database 'id':** Strictly reserved for internal, environment-specific relational mapping. It will not be exposed in import/export payloads, as it lacks cross-environment portability.
* **'value':** Serves as the mutable, human-readable label. It remains unique within a taxonomy to facilitate hierarchical search indexing, but is not relied upon as a stable identifier for import/export.
* **'external_id':** Serves as the primary key for cross-environment synchronization (import/export).

2. Handling Identifier Mutability
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

(Note: This section requires team consensus based on the context above. Proposed options to discuss:)

* **Option A: Strict Immutability.** 'external_id' remains permanently immutable. If a change is required, the user must create a new tag and manually migrate associations.
* **Option B: API/UI Mutability with Import Limitations.** Allow 'external_id' to be mutated via the UI/API (using the internal DB 'id' as the lookup under the hood). However, during file-based import/export, changing an 'external_id' will result in the creation of a new tag, as the file has no knowledge of the system's internal DB 'id'.
* **Option C: Implementation of an 'old_external_id' payload.** Allow full mutability, but require import payloads to explicitly declare an 'old_external_id' to safely resolve updates across system boundaries.