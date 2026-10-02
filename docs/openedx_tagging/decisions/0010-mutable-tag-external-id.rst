.. _openedx-tagging-adr-0010:

10. Mutable tag ``external_id`` for institution-defined identifiers
=====================================================================

Status
------

Proposed

Context
-------

`Issue #625 <https://github.com/openedx/openedx-core/issues/625>`_ asks the project to
determine an ID field strategy for Competency-Based Education (CBE) competency data.
Competency Management UX designs show a "Competency ID" on each competency tag: a
short, human-readable identifier an institution assigns and can reference in its own
systems (spreadsheets, internal catalogs, etc). Clicking it drives the workflow that
creates a ``CompetencyCriteria`` association between the competency and content
criteria rules.

This identifier is explicitly **not** a CASE or CTDL identifier. Those are external
standards bodies' IDs that may need their own storage in the future; conflating them
with an institution's internal identifier would make each harder to reason about
independently. This ADR is scoped to the internal, institution-defined identifier
only, and assumes one such identifier per tag: no LMS surveyed for this ADR supports
multiple institution identifiers for the same competency.

Institutions do rename these identifiers: when a competency taxonomy is revised (for
example, after a curriculum or accreditation-standard update), the existing tags get
renumbered in place rather than the institution creating a brand new taxonomy for the
same competencies. Any solution needs to survive that rename without losing the tag's
existing associations (e.g. its ``CompetencyCriteria`` links) or its import/export
identity.

``Tag`` already has an ``external_id`` field, used to link a Tag to a record in an
externally-defined taxonomy. The name reflects that the identifier is external to
Open edX, not external to the institution: the institution that assigns it is free to
use it for exactly this kind of internal, institution-defined identifier. Today,
though, ``external_id`` is de facto immutable: no API path mutates it after creation,
tag re-sync code documents it as "must be an immutable ID", and import/export uses it
purely as the lookup key to find and update existing tags on re-import.

Decision
--------

Use ``Tag.external_id`` as the institution's editable competency identifier, and
enable a pathway for its value to change, rather than adding a new field.

- The mutability pathway is import-only for now: the tag import file format gains a
  new optional column, ``previous_id``. When a row's ``previous_id`` matches an
  existing tag's ``external_id`` within the taxonomy, and the row's ``id`` differs
  from it, the import treats the row as a rename: it updates that tag's
  ``external_id`` to the new ``id`` (and any other changed fields) in place, instead
  of deleting the old tag and creating a new one. This preserves the tag's existing
  associations and its import/export identity across the rename.
- Editing ``external_id`` through the Taxonomy Editing UI or the generic
  tag-update REST API is anticipated as future-phase work. Both are unchanged by
  this decision for now.
- ``previous_id`` is import-only: it is not a new persisted field and does not
  appear in exports. Institutions should maintain their own record of prior
  identifiers for now; keeping that history within Open edX itself could be a future
  phase of work.
- The existing per-taxonomy uniqueness constraint on ``external_id``
  (``unique_together`` on ``(taxonomy, external_id)``) is unchanged: if the new ``id``
  collides with a tag that isn't itself part of a rename in the same import, the
  import rejects the row, the same way a duplicate ``external_id`` on tag creation
  already does today.
- Renames within a single import are order-independent: two or more tags can rename
  onto each other's current ``external_id`` values in the same file (a swap, or a
  longer cycle), regardless of row order. Any tag whose current ``external_id`` is
  another row's target is moved through a temporary, internal identifier first, then
  landed on its final value, so no two tags ever collide mid-import. This applies to
  ``external_id`` only: a row that also tries to take on the other tag's current
  ``value`` in the same swap is still rejected, since ``value`` carries the same
  per-taxonomy uniqueness constraint without the same staging treatment.
- No schema change and no migration: ``external_id`` already permits writes at the
  model layer, and ``previous_id`` is read per-row from the import file and consumed
  only while generating the import plan.

Future considerations
~~~~~~~~~~~~~~~~~~~~~~

Merging multiple existing tags into one is a future phase of work that is not precluded by this
decision. Its semantics (what happens to each old tag's associations, which tag's
other fields win) would be defined in a future document. ``previous_id`` is a transient import-file
column, not a database field name, so supporting multiple prior identifiers later
likely wouldn't require renaming it or introducing a new column: it could just accept
multiple values under the same ``previous_id`` key when that need arises.
Alternatively, renaming it to ``previous_ids`` at that point wouldn't be too costly
either, since it's an import-format detail rather than a stored field.

Rejected Alternatives
----------------------

Add a new ``Tag.code`` field
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An earlier draft of this ADR proposed a generic, per-taxonomy-unique identifier
field, separate from ``external_id``, to get a mutable identifier without disturbing
``external_id``'s immutability contract. Community review converged on relaxing
``external_id`` itself instead: a second field would equal ``external_id`` in value
the vast majority of the time, reading as duplication to end users.

Separate ``CompetencyMetadata`` table
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Proposed during review as a home for competency-specific tag fields, analogous to
``CompetencyTaxonomy``. Rejected: no competency-specific tag metadata exists today
beyond the identifier this ADR handles via ``external_id``. If a descriptive field
(e.g. a tag description or competency statement) is needed later, it belongs directly
on ``Tag`` as a generic field, since other taxonomies could reasonably want tag
descriptions too; a competency-specific table would need its own justification if
and when competency-specific metadata actually arises.

Keep ``external_id`` strictly immutable
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Treat every ``external_id`` change as a delete-and-recreate instead of relaxing the
immutability contract. Rejected: this loses the tag's existing associations (e.g. its
``CompetencyCriteria`` links) and its import/export identity, exactly the problem
institutions hit when they rename an identifier.

Changelog
---------

2026-09-05:

* Revised: renames within one import are now order-independent, so two or more tags
  can swap or cycle through each other's ``external_id`` values in a single file.

2026-07-07:

* Revised: dropped the new ``Tag.code`` field. ``Tag.external_id`` becomes mutable
  instead, through a new ``previous_id`` import column, per PR review and follow-up
  team discussion.

2026-07-03:

* Proposed.
