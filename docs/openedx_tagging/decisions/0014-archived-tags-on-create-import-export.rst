.. _openedx-tagging-adr-0014:

14. Archived tags on create, import, and export
================================================

Status
------

Proposed

Several decisions below are marked **(awaiting confirmation)**. They need an answer from the
owner named in each one before this ADR can be accepted.

Context
-------

Competency-based education (CBE) records learner mastery against tags. Deleting a tag would
destroy the history that points at it, so the approved design on `#655`_ archives a tag that
learner mastery depends on instead of deleting it (see also `openedx_learning ADR 0003
<../../openedx_learning/decisions/0003-competency-criteria-versioning.rst>`_, decision 3).
`#776`_ adds the ``Tag.archived`` flag, `#778`_ hides archived tags from the read paths and the
export through ``TagQuerySet.unarchived()``, and `#780`_ turns a tag delete into an archive when
something depends on the tag.

An archived tag stays in the database and keeps its ``value`` and ``external_id``. The
per-taxonomy ``unique_together`` constraints on ``(taxonomy, value)`` and
``(taxonomy, external_id)`` have no exception for archived rows, so both values stay taken.
Nothing in the application can restore an archived tag; only a direct database edit can.

This ADR records how the paths that write tags into a taxonomy treat an archived tag: creating a
tag (``Taxonomy.add_tag``, ``add_tag_to_taxonomy`` and the REST create endpoint), importing a file
(``TagImportPlan`` through ``import_tags``), and exporting a taxonomy. It covers ``Tag`` only.
Archived taxonomies and archived tag associations (``ObjectTag``) follow `#778`_ and `#779`_.

Decision
--------

An archived tag is no longer part of its taxonomy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

No path in this ADR creates, updates, renames, re-parents or revives an archived tag. The tag
still occupies its value and its ``external_id``, and those values stay reserved for as long as
the tag exists.

The archived subtree
~~~~~~~~~~~~~~~~~~~~

The **archived subtree** of a taxonomy is every archived tag together with every tag under one.
``TagQuerySet.in_archived_subtree()`` returns it. Because tree depth is capped by
``TAXONOMY_MAX_DEPTH``, the query checks the tag and a fixed number of ancestors, so its size does
not grow with the number of archived tags.

Create, import, and the replace sweep treat every tag in the archived subtree as outside the
taxonomy. The export from `#778`_ already leaves the whole subtree out.

**(Awaiting confirmation from the #778 owner.)** Whether an unarchived tag under an archived tag
can exist at all is open. If archiving always covers the whole subtree, as `#780`_'s rules produce,
the subtree rule above only guards against direct database edits. If such a tag can exist, the
listings and ``update_tag`` from `#778`_ still treat it as live, while the paths in this ADR do
not.

Create
~~~~~~

Adding a tag whose value or ``external_id`` belongs to an archived tag raises ``ValueError`` with a
message that names the archived tag as the cause and asks for a different value. A value held by an
unarchived tag keeps the existing "already exists" message. Adding a tag under a tag in the
archived subtree is refused. Values are stripped before they are compared, as ``Tag.clean`` strips
them before saving.

Import
~~~~~~

A row whose ``value``, ``id``, ``previous_id`` or ``parent_id`` matches a tag in the archived
subtree is an import plan error. When any row has this error, the plan builds no actions, so the
import changes nothing. The error is reported once per row.

The match runs in the database, under the column collation, because the import actions find the
tags they change with database lookups. A comparison in Python would disagree with the database:
on MySQL the collation also ignores accents and trailing spaces.

A replace import never deletes a tag in the archived subtree, because the export leaves that
subtree out and its absence from the file says nothing about it. These checks hold for the tags
that are archived when the plan is built.

Export
~~~~~~

An export leaves out the archived subtree (`#778`_). Importing that export into an empty taxonomy,
or re-importing it into its own taxonomy with ``replace=True``, succeeds and leaves the archived
subtree as it was.

Known limitations
~~~~~~~~~~~~~~~~~

- **(Awaiting confirmation from the #780 owner.)** A replace import that deletes the parent of an
  archived tag moves the archived tag to the root. This rewrites the parent of an archived record.
  The recommended rule is to archive such a parent instead, the way `#780`_ archives the ancestors
  of a depended-upon tag. Moving the tag to the root is the interim behavior until the import's
  deletes follow `#780`_.
- **(Awaiting confirmation from the #780 owner.)** A replace import deletes through its own
  ``DeleteTag`` action, which `#780`_ does not cover. Until the import's deletes go through the
  archive-or-delete decision of `#780`_, a replace import can hard-delete a tag that learner mastery
  depends on. The recommended shape is one decision for the whole delete set, made inside the
  import's ``execute`` transaction.
- Course import reuses ``TagImportPlan`` (`ADR 0011 <0011-cross-instance-taxonomy-identity.rst>`_).
  A course whose tags match an archived tag on the target instance therefore fails its criteria
  import with this ADR's plan error, although the course author cannot change the taxonomy. ADR
  0011 should be amended for this case separately.
- The error quotes the administrator's input, not the archived tag that holds the value. An
  administrator cannot see that tag, for example the archived "Cafe" that refuses "Café" on MySQL.
- ``Taxonomy.update_tag`` renaming a tag to an archived value raises ``IntegrityError`` rather than
  a message that names the archived tag. The REST rename path reports it through the serializer.
- The Django admin edit page for ``Tag`` does not restrict its fields, so an operator can change
  ``archived`` and an archived tag's value there. Such changes are outside the rules in this ADR.

Future considerations
~~~~~~~~~~~~~~~~~~~~~

**(Awaiting confirmation from product.)** The approved thread on `#655`_ returns an error "for now"
and expects that adding a tag with an archived tag's value may later un-archive that tag instead.
The reserved values above describe the current state, not a permanent rule. A restore path would
also let the messages suggest restoring the tag instead of choosing a different value.

Rejected Alternatives
---------------------

Un-archive the tag when its value is created or imported again
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Rejected for now because no restore path has been designed: the approved design on `#655`_ chose an
error, and silently reviving a tag would reattach learner history to a tag an administrator chose to
remove.

Release the archived tag's value and ``external_id``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Making the unique constraints conditional on ``archived`` would let a new tag take the value.
Rejected because two tags in one taxonomy would then share a value and an ``external_id``, which
breaks import and export, both keyed on them, and any later restore.

Find the archived subtree by lineage prefix
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

One ``lineage__startswith`` condition per archived tag selects the same rows, but the query grows
with every archived tag. SQLite refuses an expression tree deeper than 1,000, so a taxonomy with
about 1,000 archived tags could no longer be imported at all.

Delete the archived subtree on a replace import
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Rejected because the export omits the subtree, so every round trip of a taxonomy through export and
replace import would destroy the history that archiving exists to keep.

Changelog
---------

2026-11-06:

* Proposed.

.. _#655: https://github.com/openedx/openedx-core/issues/655
.. _#776: https://github.com/openedx/openedx-core/issues/776
.. _#778: https://github.com/openedx/openedx-core/issues/778
.. _#779: https://github.com/openedx/openedx-core/issues/779
.. _#780: https://github.com/openedx/openedx-core/issues/780
