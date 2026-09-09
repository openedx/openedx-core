.. _openedx-tagging-adr-0012:

12. Non-nullable tag ``external_id``
=====================================

Status
------

Proposed

Context
-------

`ADR 0010 <0010-mutable-tag-external-id.rst>`_ made ``Tag.external_id`` mutable through an
import-only rename pathway, to serve as the institution-editable "Competency ID" for
Competency-Based Education (CBE) tags. That ADR left ``external_id`` nullable, scoped to
competency taxonomies.

Building the CBE competency workflow surfaced a stronger requirement: every tag needs a
value for ``external_id`` in order to be able to associate it with objects as part of
competency criteria. It's also anticipated that non-CBE taxonomies will want a mandatory,
institution-assigned identifier in the future, particularly when there becomes a desire to
convert regular taxonomies into Competency Taxonomies.

Today, ``external_id`` is nullable specifically to work around its own
``unique_together(taxonomy, external_id)`` constraint: NULL is the only value Postgres,
MySQL, and SQLite all treat as exempt from a uniqueness constraint, so it's the
mechanism that lets multiple tags in the same taxonomy have "no external_id" at once.
Making the field mandatory means every existing NULL row in every current Open edX
instance needs a real value before the constraint can be added, since this is a
published library whose migrations run unmodified against arbitrary downstream data.
``Tag.value`` already carries the same per-taxonomy, case-insensitive uniqueness
constraint (``unique_together(taxonomy, value)``), so backfilling ``external_id`` from
``value`` is collision-free in virtually every case. The one edge case is a tag whose
``value`` matches a different tag's pre-existing, hand-assigned ``external_id`` in the
same taxonomy, which the backfill algorithm below handles.

Decision
--------

Make ``Tag.external_id`` ``NOT NULL`` at the database level, for every taxonomy, in one
migration.

- **Backfill for existing rows.** A ``RunPython`` step generates a value for every tag
  whose ``external_id`` is currently NULL, following the same
  backfill-then-constrain shape as the ``depth``/``lineage`` migration
  (``0020_tag_depth_and_lineage.py``): populate real values first, then add the
  constraint in the same migration once every row satisfies it.
- **Backfill algorithm.** Set ``external_id`` to the tag's existing ``value``. In the
  rare case where that collides with a different tag's pre-existing, hand-assigned
  ``external_id`` in the same taxonomy, the collision must be resolved deterministically
  without an unbounded retry loop, for example by appending an incrementing numeric
  counter to the copied value until it's unique. No particular format or
  collision-resolution scheme is required by this decision.
- **New tags going forward.** The REST API keeps treating ``external_id`` as optional
  for the caller, unchanged from today. When a tag is created via the API without one,
  the same backfill algorithm generates one automatically, rather than rejecting the
  request. No existing API integration that omits ``external_id`` today has to change.
  The import format already requires an identifier for every tag, unchanged by this
  decision, so auto-generation only applies to the API creation path.
- **Institutions can replace an auto-generated value.** If an institution doesn't want
  the auto-generated ``external_id``, they can change it to their own value through
  ADR 0010's rename pathway.

This is not treated as a breaking change to the public REST API, and doesn't go through
the DEPR process. The request side (``external_id`` on tag creation) stays optional, so
no existing caller has to change what it sends. The response side narrows from
"nullable string" to "always a string," which is the safe direction for consumers:
code written to expect a possible ``null`` simply never takes that branch anymore.

Future considerations
~~~~~~~~~~~~~~~~~~~~~~

If an institution converts an existing regular taxonomy to a CBE taxonomy, its tags
will already have a non-null ``external_id`` (auto-generated or backfilled) from this
decision, with no separate migration needed for that conversion.

Rejected Alternatives
----------------------

Derive backfilled values from the tag's primary key
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Needs the same kind of collision fallback as the ``value``-derived approach: a tag could
already have a hand-assigned ``external_id`` that happens to match another tag's ``id``.
Also rejected because ``id`` is meant to stay internal (per the README's model
conventions, ``uuid`` is the documented identifier for external reference), and because
it produces a value with no relationship to the tag (e.g. ``482913``), which reads as
meaningless to institutions.

Require callers to supply ``external_id`` on tag creation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Simpler mechanically than auto-generation, but rejected because it's a breaking change
for the REST API and any import/UI flow that omits ``external_id`` today.

Scope this decision to CBE/competency taxonomies only, for now
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Rejected because taxonomies later converted to Competency Taxonomies will want this too.

Changelog
---------

2026-07-10:

* Proposed.

2026-08-17:

* Scoped the import-format optionality claim to the REST API only; the import format
  already requires an identifier per tag row.
* Struck the "uppercase and replace spaces with underscores" legibility example from
  the backfill algorithm: it isn't injective, so it can map two distinct tag values
  onto the same ``external_id``, undermining the collision-free argument this decision
  relies on.
