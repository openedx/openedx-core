.. _openedx-learning-adr-0003:

3. How should versioning be handled for CBE competency achievement criteria?
=============================================================================

Context
-------
Course Authors and/or Platform Administrators will be entering the competency achievement criteria rules in Studio that learners are required to meet in order to demonstrate competencies. Depending on the institution, these Course Authors or Platform Administrators may have a variety of job titles, including Instructional Designer, Curriculum Designer, Instructor, LMS Administrator, Faculty, or other Staff.

Typically, only one person would be responsible for entering competency achievement criteria rules in Studio for each course, though this person may change over time. However, entire programs could have many different Course Authors or Platform Administrators with this responsibility.

Typically, institutions and instructional designers do not change the mastery requirements (competency achievement criteria) for their competencies frequently over time. However, the ability to do historical audit logging of changes within Studio can be a valuable feature to those who have mistakenly made changes and want to revert or those who want to experiment with new approaches.

Currently, Open edX always displays the latest edited version of content in the Studio UI and always shows the latest published version of content in the LMS UI, despite having more robust version tracking on the backend (Publishable Entities).

Authoring data (criteria definitions) and runtime learner data (status) have different governance needs. The former is long-lived and typically non-PII, while the latter is user-specific, can be large (learners x criteria/competencies), and may require stricter retention and access controls. These differing lifecycles can make deep coupling of authoring and runtime data harder to manage at scale. Performance is also a consideration as computing or resolving versioned criteria for large courses could add overhead in Studio authoring screens or LMS views.

Decision
--------
For the initial implementation, versioning and traceability of competency achievement criteria will be handled with a combination of model history and lifecycle guardrails:

1. Apply ``django-simple-history`` to competency criteria definition moodels/tables:

   - ``CompetencyCriteriaGroup``
   - ``CompetencyCriteria``
   - ``CompetencyRuleProfile``

   This provides historical row snapshots and audit metadata for authored criteria definitions, without adopting the full publishable framework for this phase.

2. Do not apply ``django-simple-history`` to ``oel_tagging_tag``, ``oel_tagging_taxonomy``, or ``CompetencyTaxonomy`` in this phase.

   These models are treated as non-evaluative display/metadata for competency criteria purposes; edits to names or metadata in these tables are not intended to change evaluation outcomes.

3. ``oel_tagging_objecttag`` associations used by competency criteria follow post-use archive rules:

   - Before any related learner status exists, edits and deletes are allowed.
   - After any related learner status exists, disassociation/deletion is archive-only (soft delete), not hard delete.
   - Ordinary reads (tag lists, object-tag-for-an-object lookups used by authoring UIs, and similar) exclude archived rows, so an archived association behaves as gone from those paths. For MVP, one exception applies: the ``ObjectTag`` of an archived ``CompetencyCriterion`` stays live, so the competency tag remains visible on the tagged object while the criterion itself is closed to authoring.
   - The read path that resolves a learner status row back to the ``ObjectTag`` it references is exempt from that exclusion: archived rows remain queryable on that path, so learner status records can continue to be traced back to their source association.

4. Authoring guardrails must warn on potentially impactful edits:

   - If a user edits competency criteria definitions, Studio must display an explicit warning that the change will be applied going forward and will not retroactively affect any existing learner status.
   - Applying these changes requires explicit user confirmation.
   - If/when ``rule_type``/``rule_payload`` become editable through a REST API or application-layer/service code, a ``CompetencyRuleProfile`` would be "in use" if any ``CompetencyCriterion`` assigned to it (``competency_rule_profile_id``) has an associated ``StudentCompetencyCriteriaStatus`` row, and editing an in-use profile's ``rule_type``/``rule_payload`` would require the same warning and confirmation.
   - If/when a more specific profile can exist, the same warning applies when creating one causes existing criteria to be reassigned to it.
   - The same warning also applies when an authoring action switches a criterion between a profile assignment and per-criterion overrides (ADR 0002 Decision 4).

5. Do not store history for learner competency status tables, and update rows in place. These tables do not use ``django-simple-history``:

   - For ``StudentCompetencyCriteriaStatus``, ``StudentCompetencyCriteriaGroupStatus``, and ``StudentCompetencyStatus``, each row is updated in place when a learner's status changes.
   - There is no history of prior status values beyond the ``modified`` timestamp, and no separate history table.
   - Current status is the single row for a given learner + target entity.

   Open edX has no concept of gradeable subsection attempts. This means that an attempt is actually defined at the level of an individual problem, so storing one row per attempt can result in tens of billions of rows for a large Open edX instance. That scale creates real operational burden: schema migrations, backups, and truncation and retention policy. Therefore, we did additional market research and found that storing history for learner competency status will not be required by the initial pilot partners for the MVP of the CBE implementation, and it would be safe to add later if needed.


Rejected Alternatives
---------------------

1. Defer competency achievement criteria versioning for the initial implementation. Store only the latest authored criteria and expose the latest published state in the LMS, consistent with current Studio/LMS behavior.
    - Pros:
        - Keeps the initial implementation lightweight
    - Cons:
        - There is no built-in rollback or audit history
        - Adding versioning later will require data migration and careful choices about draft vs published defaults
2. Each model indicates version, status, and audit fields
    - Pros:
        - Simple and familiar pattern (version + status + created/updated metadata)
        - Straightforward queries for the current published state
        - Can support rollback by marking an earlier version as published
        - Stable identifiers (original_ids) can anchor versions and ease potential future migrations
    - Cons:
        - Requires custom conventions for versioning across related tables and nested groups
        - Lacks shared draft/publish APIs and immutable version objects that other authoring apps can reuse
        - Not necessarily consistent with existing patterns in the codebase (though these are already not overly consistent).
3. Publishable framework in openedx-learning
    - Pros:
        - First-class draft/published semantics with immutable historical versions
        - Consistent APIs and patterns shared across other authoring apps
    - Cons:
        - Requires modeling criteria/groups as publishable entities and wiring Studio/LMS workflows to versioning APIs
        - Adds schema and migration complexity for a feature that does not yet require full versioning
4. Append-only audit log table (event history)
    - Pros:
        - Lightweight way to capture who changed what and when
        - Enables basic rollback by replaying or reversing events
    - Cons:
        - Requires custom tooling to reconstruct past versions
        - Does not align with existing publishable versioning patterns
5. Keep the learner status tables append-only, storing every status change as a new row.
    - Pros:
        - Every write is an insert rather than a read-modify-write, so there is no current row to keep consistent.
        - Preserves a full audit trail of every status change.
    - Cons:
        - No MVP requirement calls for this history.
        - Grows the leaf table by a further multiplier of problem attempts per learner per leaf, reaching tens of billions of rows for a large instance.
        - A read must resolve the latest row for a learner and node rather than reading one in-place row, which is more expensive and more complex.
6. Compute leaves transiently, never store them.
    - Pros:
        - Eliminates the largest table, since leaf demonstration would be computed on demand from the leaf's rule and the learner's grade.
    - Cons:
        - A recomputed leaf reflects the rule as it stands now, not the rule in force when the learner was graded, which contradicts Decision 4 above and can silently lower a status.
7. Store child evaluations on the parent group row instead of a leaf table.
    - Pros:
        - Avoids the largest table entirely.
    - Cons:
        - A leaf write becomes a read-modify-write of a column shared with every sibling.
        - No unique index or foreign key stands behind a status packed into a per-group array, so nothing but application code keeps it consistent with the criteria it describes.
        - Couples a leaf's frozen mastery to the current shape of the criteria tree, so restructuring the tree can corrupt already-recorded mastery.
        - Removes the ability to individually track competency status progress by learning object, for example by subsection.
8. Put the leaf table behind its own database alias and router, a separate physical database, or native partitioning or sharding, from the start.
    - Pros:
        - Physically isolates or splits the largest table from the start.
    - Cons:
        - A second database alias runs on its own connection, so the leaf write and the ancestor writes could no longer share a single transaction with the grade write, which gives up the atomicity these writes need.
        - Imposes real operational cost on every deployment with nothing measured to justify it, and remains available later if a specific need is proven.
9. Serve heavy leaf-table reads from a read replica.
    - Pros:
        - Keeps dashboard and reporting reads off the primary.
    - Cons:
        - Premature: no measurement shows the primary struggling, and these are point lookups on a composite index, not the wide, expensive reads that drive ``StudentModule`` load in edx-platform.
        - Adding it later is a per-query choice, not a schema decision.
10. Give the leaf table a custom unsigned 64-bit primary key.
     - Cons:
         - ``BigAutoField``'s range is already far out of reach for this table.
         - Unsigned integers do not exist in PostgreSQL, and the custom field type carries ongoing maintenance cost for no real benefit at this scale.
11. Drop the database-level foreign key constraint on the learner column.
     - Cons:
         - This repo's convention is a real foreign key to ``settings.AUTH_USER_MODEL``. Reports of contention on the user row exist elsewhere in the ecosystem, but are not understood well enough here to design around.

Changelog
---------

2026-11-04:

* Amended Decision 3 for issue #674: the ``ObjectTag`` of an archived criterion stays live for MVP,
  so the competency tag remains visible on the object.
