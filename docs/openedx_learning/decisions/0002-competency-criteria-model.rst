.. _openedx-learning-adr-0002:

2. How should CBE competency achievement criteria be modeled in the database?
==============================================================================

Context
-------
Competency Based Education (CBE) requires that the LMS have the ability to track learners' mastery of competencies through the means of competency achievement criteria. For example, in order to demonstrate that I have mastered the Multiplication competency, I need to have earned 75% or higher on Assignment 1 or Assignment 2. The association of the competency, the threshold, the assignments, and the logical OR operator together make up the competency achievement criteria. Course Authors and Platform Administrators need a way to set up these associations in Studio so that their outcomes can be calculated as learners complete their materials. This is an important prerequisite for being able to display competency progress dashboards to learners and staff to make Open edX the platform of choice for those using the CBE model.

In the vast majority of cases, the rule used to evaluate mastery (the threshold, for example "75% or higher") is the same across the entire Open edX instance, so the system-wide default is the common case. The scoping and override mechanisms described below exist to support less common exceptions to that default: a taxonomy-level default is needed when a competency's threshold should differ from the system-wide one; an organization-level default is needed because Open edX already allows a single taxonomy to be associated with multiple organizations, so organizations sharing a taxonomy may still expect different thresholds (for example, a general skill taxonomy used differently by two departments), or a taxonomy should not be locally weakened by any organization at all (for example, one adopted from a third-party). Course- and criterion-level overrides support finer-grained exceptions beyond that.

In order to support these use cases, we need to be able to model these rules (competency achievement criteria) and their association to the tag/competency to be demonstrated and the object (course, subsection, unit, etc) or objects that are used as the means to assess competency mastery. We also need to leave flexibility for a variety of different types as well as groupings to be able to develop a variety of pathways of different combinations of objects that can be used by learners to demonstrate mastery of a competency.

Additionally, we need to be able to track each learner's progress towards competency demonstration as they begin receiving results for their work on objects associated with the competency via competency achievement criteria.

Terminology
-----------
To reduce ambiguity, this ADR uses the following CamelCase domain terms:

- ``CompetencyTaxonomy``: A taxonomy that is explicitly enabled for CBE competency features.
- ``CompetencyAchievementCriteria``: The full criteria expression used to evaluate each learner's status for one competency. Evaluation yields a competency outcome (at minimum demonstrated/not demonstrated, and potentially richer outcomes such as mastery level in future ADRs).
- ``CompetencyCriteriaGroup``: An internal node in the ``CompetencyAchievementCriteria`` expression tree that combines child nodes with ``AND`` or ``OR``.
- ``CompetencyCriterion``: A leaf node in the ``CompetencyAchievementCriteria`` expression tree. It points to one tag/object association plus the rule used to evaluate that object.
- ``CompetencyRuleProfile``: A reusable default evaluation rule that can be scoped by taxonomy, course, or organization.

In short: ``CompetencyAchievementCriteria`` is the full tree, while ``CompetencyCriterion`` is one leaf in that tree.

Decision
--------

1. ``CompetencyTaxonomy`` concept (database table)

   Represents the set of taxonomies that are competency-enabled rather than generic tag-only taxonomies.

   Maps to ``Taxonomy`` using Django multi-table inheritance (``CompetencyTaxonomy(Taxonomy)``), not a ``taxonomy_type`` column on ``oel_tagging_taxonomy``.

   Relationship to other concepts:

   - ``CompetencyRuleProfile`` can be scoped to a ``CompetencyTaxonomy``.
   - The override tiebreak (``taxonomy_overrides_org``, below) is recorded on the taxonomy rather than on the organization-scoped profile: a single taxonomy can compete against many organization-scoped profiles (Decision 4), so the taxonomy is the one place a single answer can be set that automatically applies to all of them, current or future, rather than needing to be repeated on each org-scoped row.
   - ``CompetencyCriteriaGroup`` and ``CompetencyCriterion`` are only valid for competencies from enabled taxonomies.

   A taxonomy listed in this table:

   - is able to be displayed in the UI with the competency criteria association view.
   - is able to be displayed in the UI with the competency progress tracking views.
   - is also able to be displayed in existing taxonomy views.
   - has constraints on associated content objects to only be those supported for progress tracking.
   - has constraints on associated content objects to only include ones that could logically be used to demonstrate mastery of the competency (for example, associating both a course and one assignment within that same course would be ambiguous).

   In contrast, a taxonomy that is not listed in this table:

   - is only displayed in existing taxonomy views.
   - is not displayed in competency criteria association views.
   - is not displayed in competency progress tracking views.
   - has no competency-specific constraints on associated content objects.

   This new database table will have the following columns:
   
   1. ``taxonomy_ptr_id``: Primary key and one-to-one foreign key to ``oel_tagging_taxonomy.id``.
   2. ``taxonomy_overrides_org``: Boolean, defaults to ``false``. Used only while computing which single ``CompetencyRuleProfile`` to assign to a ``CompetencyCriterion`` (Decision 4). If, for a criterion's context, both an organization-scoped profile row and a taxonomy-scoped profile row exist as candidates, this field decides which one gets assigned: ``false`` (default) assigns the organization-scoped row; ``true`` assigns this taxonomy's own row instead, so it cannot be overridden by an organization. Once assigned, the criterion stores that one profile's id and this field plays no further part. This field is created now but read by no code path in this phase, since organization-scoped profiles don't exist yet and the conflict it resolves can't occur; see the MVP note in Decision 4.

   Lifecycle rules for this parent/child pair:

   - Creating a competency taxonomy creates both the parent ``oel_tagging_taxonomy`` row and the ``CompetencyTaxonomy`` row in one transaction.
   - Deleting either representation is treated as deleting the competency taxonomy and removes both rows, subject to Decision 7 delete protections.

2. ``CompetencyCriteriaGroup`` concept (database table)

   Represents an internal boolean-expression node for ``CompetencyAchievementCriteria``.

   A single ``CompetencyAchievementCriteria`` is represented by one root ``CompetencyCriteriaGroup`` plus all descendant groups and leaf ``CompetencyCriterion`` rows.

   Relationship to other concepts:

   - A group belongs to one competency (``oel_tagging_tag_id``) and optional course scope (``course_id``).
   - A group can have child groups.
   - ``logic_operator`` (``AND``, ``OR``, or null) defines how children are combined; null occurs only for a group with a single child, where combining logic is moot, and the application layer treats null the same as ``OR`` (the default when there's nothing to disambiguate).
   - ``ordering`` defines deterministic sibling evaluation sequence during group recomputation and enables short-circuit evaluation.

   This new database table will have the following columns:

   1. ``id``: unique primary key
   2. ``parent_id``: The ``CompetencyCriteriaGroup.id`` of the parent group. Null means this is a root group.
   3. ``oel_tagging_tag_id``: The ``oel_tagging_tag.id`` for the competency represented by this criteria tree.
   4. ``course_id``: Nullable foreign key for the course that scopes this criteria tree. Within a course-scoped subtree, every group carries the same ``course_id``, not only its topmost group; immutable after creation. Root groups always have ``course_id`` null.
   5. ``name``: string
   6. ``ordering``: Indicates evaluation sequence number for this criteria group. This defines deterministic evaluation order for siblings during read-time evaluation and event-driven recomputation, and enables short-circuit evaluation.
   7. ``logic_operator``: Either “AND” or “OR” or null. This determines how children are combined at a group node ("AND" or "OR").
   8. ``archived``: Boolean, defaults to false. Set instead of deleting a group once a student status exists for it. Archived groups are hidden from authoring and new associations but remain queryable, so existing ``CompetencyCriteria`` rows and learner status rows stay resolvable.

   Example: A root group uses "OR" with two child groups.

   - Child group A (``ordering=1``) requires "AND" across Assignment 1 and Assignment 2.
   - Child group B (``ordering=2``) requires "AND" across Final Exam and Lab Assignment 3.
   - If group A evaluates to true, group B is not evaluated.
   - ``ordering`` complements learner-status materialization: progress is still persisted at leaf/group/root levels, and ``ordering`` only defines child scan order when a parent recomputes after a leaf status change.

   Concrete event example (materialization + ordered recompute):

   - Root group uses ``AND`` with Group A (``ordering=1``) and Group B (``ordering=2``).
   - Group A status is currently ``AttemptedNotDemonstrated``; Group B status is currently ``PartiallyAttempted``; root is ``AttemptedNotDemonstrated``.
   - New event: learner completes one remaining leaf criterion under Group B, so that leaf row changes to ``Demonstrated``.
   - Bottom-up materialization updates Group B first. Group B now becomes ``Demonstrated``.
   - Recompute the root ``AND`` group in ``ordering`` sequence: Group A is evaluated first and is still ``AttemptedNotDemonstrated``, so root is determined immediately and Group B does not need to be checked for root recomputation.
   - Persist changed rows only: the updated leaf row and Group B row. Root remains ``AttemptedNotDemonstrated`` and is not rewritten.

   Boundaries and intended behavior:

   - Empty groups: Persisted criteria definitions should not contain empty groups. Authoring flows may temporarily create empty groups while editing, but backend validation must reject them.
   - Mixed tree depths: Backend supports deeply nested groups. Current frontend authoring constraint is a maximum depth of 3 layers total, using zero-indexed depth (``0=root``, ``1=course-scope group``, ``2=leaf criteria/group``).
   - Retrieval scope: A read always includes the root scope (all nodes where ``course_id is null``), plus any selected subset of course-scoped groups, each returned as a complete subtree, never truncated.
   - Practical size and growth: Total rows in ``CompetencyCriteriaGroup`` are expected to grow over time as course runs are added; this ADR sets no global DB row cap. No max total node-count cap is required per root group. For ``course_id is null`` branches, expected size is small (realistically <=500 nodes).


3. ``CompetencyRuleProfile`` concept (database table)

   Represents a reusable default rule configuration that can be applied to many ``CompetencyCriterion`` rows.

   Relationship to other concepts:

   - Each row is scoped by at most one of taxonomy, course, or organization (or by none, for the system default). A check constraint enforces that at most one of ``organization_id``, ``course_id``, and ``competency_taxonomy_id`` is non-null per row. See Decision 4 for how a criterion is assigned a profile when rows in more than one of these scopes could apply to it.
   - At most one profile row may exist per distinct scope value. This is enforced by a unique constraint on the generated ``scope_code`` column (Decision 5), not a plain unique constraint on the three raw scope columns; see the ``scope_code`` column definition below for why.
   - The system default is the single profile row where all three scope fields are null. ``scope_code`` is never null, including for this row (see below), so its singularity is enforced by the same unique constraint as every other profile rather than a separate procedural guarantee; it is seeded once via migration and never created or deleted through the profile API. If/when a REST API or application-layer/service code exists for editing a profile's ``rule_type``/``rule_payload``, the system default row would be editable through it like any other profile. Until then, only an operator can edit it directly (for example via Django admin or SQL).
   - Is referenced by ``CompetencyCriterion``, which may override its type/payload.
   - Never hard-deleted; retirement is archive-only (Decision 7).

   This new database table will have the following columns:

   1. ``id``: unique primary key
   2. ``organization_id``: The ``organization_id`` of the organization that this competency rule profile is scoped to. Null if it is not scoped to a specific organization.
   3. ``course_id``: The ``course_id`` of the course that this competency rule profile is scoped to. Null if it is not scoped to a specific course.
   4. ``competency_taxonomy_id``: The ``CompetencyTaxonomy.taxonomy_ptr_id`` of the competency taxonomy that this competency rule profile is scoped to. Null if it is not scoped to a specific taxonomy.
   5. ``scope_code``: A database-generated column that is always in the fixed, trivially-parseable format ``"org:X,course:Y,taxonomy:Z"``, with each segment left blank when the corresponding scope column is null: for example ``"org:5,course:,taxonomy:"``, ``"org:,course:12,taxonomy:"``, ``"org:,course:,taxonomy:7"``, or ``"org:,course:,taxonomy:"`` for the system default. ``scope_code`` is therefore never null, including for the system default row. This exists because SQL never treats two ``NULL`` values as equal for uniqueness purposes, so a plain unique constraint across the three nullable scope columns would not stop two rows from sharing the same scope (for example two rows both with ``organization_id=5`` and the other two columns null). Collapsing the scope into one generated, always-non-null column sidesteps that, and does so identically on every database backend this project supports, including MySQL, which does not support the conditional/partial unique indexes that would otherwise be the usual fix. ``scope_code`` embeds internal ID references and exists solely to enforce uniqueness; it is not intended to be exported or exposed outside this system.
   6. ``rule_type``: “View”, “Grade”, “MasteryLevel” (Only “Grade” will be supported for now)
   7. ``rule_payload``: JSON payload keyed by ``rule_type`` to avoid freeform strings. It is structured JSON (not arbitrary freeform data): each ``rule_type`` defines the allowed payload shape and required keys, and validation enforces this contract. JSON is used instead of fixed columns like ``op``, ``value``, and ``scale`` so that future rule types (for example, ``MasteryLevel`` thresholds or plugin-defined evaluators such as CEL-based rules) can add their own fields without repeated schema migrations or many nullable columns. Examples:

      1. ``Grade``: ``{"op": "gte", "value": 0.75, "scale": "percent"}``. Allowed ``op`` values: ``gte``, ``lte``, ``eq``. ``value`` must be a fraction between 0.0 and 1.0 inclusive, matching the platform's existing fractional grade representation, not a 0-100 scale.
   8. ``archived``: Boolean, defaults to false. Set instead of deleting a profile that is no longer wanted. Archived profiles are hidden from authoring and new associations but remain queryable, so existing ``CompetencyCriterion`` rows and learner status rows stay resolvable.

   A check constraint requires that at most one of ``organization_id``, ``course_id``, and ``competency_taxonomy_id`` is non-null on any row, matching the scoping rule above.

   Editing a profile may change ``rule_type``/``rule_payload`` only; scope fields are immutable after creation, to avoid silently re-scoping criteria that already resolved to this profile.

   MVP note: only the system-default profile exists in this phase; taxonomy-, course-, and organization-scoped profiles are all out of scope for this MVP, so every profile row in this phase has ``competency_taxonomy_id``, ``course_id``, and ``organization_id`` null. If/when taxonomy-, course-, or organization-scoped competency rule profiles are built, no schema change is needed here to support them -- the columns, the uniqueness constraint, and the re-assignment behavior (Decision 4) already do.

4. ``CompetencyCriterion`` concept (``CompetencyCriteria`` database table)

   Represents one leaf condition in a ``CompetencyAchievementCriteria`` tree.

   Relationship to other concepts:

   - Belongs to one ``CompetencyCriteriaGroup``.
   - Points to one ``oel_tagging_objecttag`` association.
   - Uses one ``CompetencyRuleProfile`` by default, with optional per-criterion overrides.

   This new database table will have the following columns:

   1. ``id``: unique primary key
   2. ``competency_criteria_group_id``: Foreign key to ``CompetencyCriteriaGroup.id``.
   3. ``oel_tagging_objecttag_id``: Tag/Object Association id
   4. ``competency_rule_profile_id``: Nullable FK to the ``CompetencyRuleProfile`` applied to this criterion.
   5. ``rule_type_override``: Nullable enumerated rule type: “View”, “Grade”, “MasteryLevel” (Only “Grade” will be supported for now). When set, this overrides the ``rule_type`` in the associated ``CompetencyRuleProfile`` for this criterion.
   6. ``rule_payload_override``: Nullable JSON payload keyed by ``rule_type`` to avoid freeform strings. When set, this overrides the ``rule_payload`` in the associated ``CompetencyRuleProfile`` for this criterion. The same typed/validated payload contract as ``rule_payload`` applies. Examples:

      1. ``Grade``: ``{"op": "gte", "value": 0.75, "scale": "percent"}``. Allowed ``op`` values: ``gte``, ``lte``, ``eq``. ``value`` must be a fraction between 0.0 and 1.0 inclusive, matching the platform's existing fractional grade representation, not a 0-100 scale.

   7. ``archived``: Boolean, defaults to false. Set instead of deleting a criterion once a student status exists for it. Archived criteria are hidden from authoring and new associations but remain queryable, so existing learner status rows stay resolvable.

   Exactly one of the following holds for a given criterion, never both, never neither:

   (a) ``competency_rule_profile_id`` is set and both override fields are null, or
   (b) ``competency_rule_profile_id`` is null and both override fields are set.

   ``competency_rule_profile_id`` is not assigned once and left alone. The same assignment computation -- using whatever scope the relevant authoring screen operates in, resolved per the table below -- is re-run at each of these points, and each one writes a new value:

   1. Creation: the authoring screen that creates the criterion assigns it using the scope that screen itself operates in (for example, adding Competency Criteria to the Course Outline page would have it supply its own course id; the Competency Management page will supply its own taxonomy id). This is independent of ``CompetencyCriteriaGroup.course_id``, which scopes evaluation windowing (Decision 2), not rule assignment; the two may coincidentally match but neither determines the other.
   2. If/when a more specific profile can exist, one created later that would now apply to an existing criterion causes that criterion to be reassigned to it, treated as an edit for the in-use warning (ADR 0003 Decision 4).
   3. A user sets a per-criterion override (for example, editing a ``CompetencyCriteriaGroup``'s default rule, which cascades to every ``CompetencyCriterion`` under that group): ``competency_rule_profile_id`` is set to null and the override fields are set instead.
   4. A user's override is changed to match what the computation in (1)/(2) would already produce for this criterion: the criterion is reassigned back to that profile and the override fields are cleared, rather than keeping a redundant override in place.

   (A future authoring action to revert a criterion to the next-closest scope, once course/organization scoping exists, would be a fifth trigger using the same computation; out of scope for this phase.)

   In no case is the FK re-resolved dynamically at evaluation time -- only these explicit write events change it.

   Which profile a criterion is assigned, by which profile rows exist for its context:

   .. list-table::
      :header-rows: 1

      * - Course profile exists?
        - Org profile exists?
        - Taxonomy profile exists?
        - Assigned
        - Notes
      * - Yes
        - (any)
        - (any)
        - Course
        - Always wins outright, no exceptions.
      * - No
        - Yes
        - Yes
        - See below
        - The only contested case.
      * - No
        - Yes
        - No
        - Organization
        - Nothing else to compete with.
      * - No
        - No
        - Yes
        - Taxonomy
        - Nothing else to compete with.
      * - No
        - No
        - No
        - System default
        - Nothing else exists.

   The contested case (no course profile; both an organization-scoped row and a taxonomy-scoped row exist) is resolved by ``CompetencyTaxonomy.taxonomy_overrides_org`` (Decision 1):

   .. list-table::
      :header-rows: 1

      * - ``taxonomy_overrides_org``
        - Example
        - Assigned
      * - ``false`` (default)
        - A general skill taxonomy ("Communication") used by multiple departments, each wanting its own threshold
        - Organization
      * - ``true``
        - A taxonomy adopted from a third-party standard body ("Nursing"), which should not be locally weakened
        - Taxonomy

   MVP note: only the system-default profile exists in this phase, so every ``CompetencyCriterion`` created in this phase is assigned it -- the only authoring screens that exist today (Competency Management, and eventually Libraries) supply a competency tag but no taxonomy, course, or organization context to resolve a more specific profile from. Organization scoping in particular has no defined source yet: no authoring screen has been decided to expose an organization context, so there is nothing to assign an organization-scoped profile from until that is decided. If/when taxonomy-, course-, and organization-scoped competency rule profiles exist, resolution follows the tables above; no schema change is needed to enable them then.

5. Indexes for common lookups

   1. ``CompetencyCriteriaGroup(oel_tagging_tag_id, course_id)``
   2. ``CompetencyCriteriaGroup(parent_id)``
   3. ``oel_tagging_objecttag(object_id)``
   4. ``CompetencyCriteria(oel_tagging_objecttag_id)``
   5. ``CompetencyCriteria(competency_criteria_group_id)``
   6. ``StudentCompetencyCriteriaStatus(user_id, competency_criteria_id)`` (unique -- each learner status table holds exactly one row per learner and target entity, updated in place; see ADR 0003 Decision 5)
   7. ``StudentCompetencyCriteriaGroupStatus(user_id, competency_criteria_group_id)`` (unique)
   8. ``StudentCompetencyStatus(user_id, oel_tagging_tag_id)`` (unique)
   9. ``CompetencyRuleProfile(scope_code)`` (unique -- at most one profile per distinct scope value; a plain unique constraint on the three raw nullable scope columns would not enforce this, since SQL never treats two ``NULL`` values as equal and this project's MySQL backend does not support the conditional/partial unique indexes that would otherwise route around that; see the ``scope_code`` column in Decision 3)
   10. ``CompetencyMasteryStatuses(status)`` (unique)

6. Learner progress status concepts (``StudentCompetency*Status`` database tables)

   When a completion event (graded, completed, mastered, etc.) occurs for an object, determine and track the learner's status in earning the competency. To reduce recalculation frequency, store results at each level.

   Relationship to other concepts:

   - ``StudentCompetencyCriteriaStatus`` tracks status at ``CompetencyCriterion`` leaf level.
   - ``StudentCompetencyCriteriaGroupStatus`` tracks status at ``CompetencyCriteriaGroup`` node level.
   - ``StudentCompetencyStatus`` tracks top-level competency demonstration state.
   - All learner status rows use a shared lookup table (``CompetencyMasteryStatuses``) so status semantics live in one place and student status tables stay structurally consistent.

   Intended update flow (bottom-up materialization):

   - A learner event updates one ``StudentCompetencyCriteriaStatus`` row.
   - Recompute ancestor ``CompetencyCriteriaGroup`` statuses upward to the root.
   - At each group, evaluate children in ``ordering`` sequence and short-circuit when the group's result is already determined by its ``logic_operator``.
   - Persist only rows whose status changed.

   1. Add new database table for ``CompetencyMasteryStatuses`` with these columns:

      1. ``id``: unique primary key
      2. ``status``: unique status value (seeded values: “Demonstrated”, “AttemptedNotDemonstrated”, and “PartiallyAttempted”)

      Notes:

      - This table is system-owned lookup data and should be treated as immutable configuration, not user-authored rows.

   2. Add new database table for ``StudentCompetencyCriteriaStatus`` with these columns:

      1. ``id``: unique primary key
      2. ``competency_criteria_id``: Foreign key to ``CompetencyCriterion.id``
      3. ``user_id``: Foreign key pointing to user_id (presumably the learner's id, although it appears that it is possible for staff to get grades as well) in ``auth_user`` table
      4. ``status_id``: Foreign key to ``CompetencyMasteryStatuses.id``
      5. ``created``: The timestamp at which the student's criterion status row was first written.
      6. ``modified``: The timestamp at which the student's criterion status was last updated.

   3. Add a new database table for ``StudentCompetencyCriteriaGroupStatus`` with these columns:

      1. ``id``: unique primary key
      2. ``competency_criteria_group_id``: Foreign key to ``CompetencyCriteriaGroup.id``
      3. ``user_id``: Foreign key pointing to user_id (presumably the learner's id, although it appears that it is possible for staff to get grades as well) in ``auth_user`` table
      4. ``status_id``: Foreign key to ``CompetencyMasteryStatuses.id``
      5. ``created``: The timestamp at which the student's criteria-group status row was first written.
      6. ``modified``: The timestamp at which the student's criteria-group status was last updated.

   4. Add a new database table for ``StudentCompetencyStatus`` with these columns:

      1. ``id``: unique primary key
      2. ``oel_tagging_tag_id``: Foreign key pointing to Tag id
      3. ``user_id``: Foreign key pointing to user_id (presumably the learner's id, although it appears that it is possible for staff to get grades as well) in ``auth_user`` table
      4. ``status_id``: Foreign key to ``CompetencyMasteryStatuses.id``. This table should have a constraint to only allow status values of “Demonstrated” and “PartiallyAttempted” since it represents overall competency demonstration state, not in-progress states.
      5. ``created``: The timestamp at which the student's competency status row was first written.
      6. ``modified``: The timestamp at which the student's competency status was last updated.

7. Delete protection boundaries

   - If no related row exists in ``StudentCompetencyCriteriaStatus`` (the leaf-level learner status table, Decision 6) for a competency definition, delete behaves as it does today: a hard delete that cascades through competency metadata tables.
   - Once a related row exists in ``StudentCompetencyCriteriaStatus``, deletion of the associated competency definition row still succeeds, but as an archive (soft delete) instead of a hard delete: the row is hidden from authoring and new associations but remains queryable, so existing learner status rows stay resolvable. This archive-vs-hard-delete rule applies to ``oel_tagging_tag``, ``oel_tagging_taxonomy``, ``CompetencyTaxonomy``, ``oel_tagging_objecttag``, ``CompetencyCriteriaGroup``, and ``CompetencyCriteria``; see :ref:`openedx-learning-adr-0003` Decision 3 for ``oel_tagging_objecttag``'s own archive rule and traceability exception.
   - ``StudentCompetencyCriteriaStatus`` is what determines whether a record is protected. ``StudentCompetencyCriteriaGroupStatus`` and ``StudentCompetencyStatus`` are roll-up tables derived from it (Decision 6) and are not independently checked for this purpose: :ref:`openedx-learning-adr-0004` writes the leaf table synchronously with the grade but rolls the two roll-up tables up later via an asynchronous task, which can lag behind the leaf or, per that ADR's Decision 5, need manual recovery. Checking only the roll-up tables could therefore miss real learner progress that has not rolled up yet.
   - Direct deletion of a ``CompetencyRuleProfile`` is never a hard delete; retirement is always archive-only, via a normal update to its ``archived`` column (Decision 3). However, if a taxonomy or course that is associated with a taxonomy- or course-scoped profile is deleted, then this profile will be deleted along with it.

.. image:: images/CompetencyCriteriaModel.png
   :alt: Competency Criteria Model
   :width: 80%
   :align: center


Example
-------
The following example illustrates how the decision model supports both defaults and overrides without requiring authors to specify every rule by hand.

Competency: "Writing Poetry" (a competency taxonomy tag)

Course: "Course X"

Content objects:

- Assignment 7: "Submit a Poem"
- Assignment 9: "Remix a Poem"

1. ``oel_tagging_objecttag``:

   - Assignment 7 tagged with "Writing Poetry"
   - Assignment 9 tagged with "Writing Poetry"

2. ``CompetencyRuleProfile``:

   - Taxonomy-scoped default: ``Grade >= 75%`` for this competency taxonomy

3. ``CompetencyCriteriaGroup``:

   - Root group uses ``OR``
   - Group A (``ordering=1``) uses ``AND``
   - Group B (``ordering=2``) uses ``AND``

4. ``CompetencyCriteria``:

   - Group A + Assignment 7 (uses default rule profile)
   - Group A + Assignment 9 (override to ``Grade >= 85%``)
   - Group B + Assignment 7 (uses default rule profile)
   - Group B + Assignment 9 (uses default rule profile)

This allows authors to set a single default rule for most tagged content, and only override where needed. It also lets the same tag/object association participate in multiple criteria groups without duplicating tagging rows.


Rejected Alternatives
---------------------
1. Update ``oel_tagging_taxonomy`` to have a new column for ``taxonomy_type`` where the value could be “Competency” or “Tag”.

   1. Pros

      1. Simpler model with fewer tables
      2. Reuses existing taxonomy table and keeps reads straightforward when checking taxonomy usage
      3. Avoids introducing an additional join for queries that only need to know whether a taxonomy is competency-enabled
   2. Cons

      1. Couples CBE concerns directly into the generic tagging domain model, reducing separation of concerns
      2. Makes ``oel_tagging_taxonomy`` less generic and encourages enum/flag growth as new specialized usages are added
      3. Prevents strong foreign key guarantees for CBE tables, since they can only point to ``oel_tagging_taxonomy`` and not specifically to competency-enabled taxonomies
      4. Makes it harder to keep competency features optional for deployments that only want generic tagging
      5. Increases risk of future refactor/migration work if the competency domain later needs to be split from tagging

2. Same as above except combine the ``CompetencyCriteria`` and ``oel_tagging_objecttag`` tables by adding the rule information as columns on the ``oel_tagging_objecttag`` table. This would be a more denormalized approach that would reduce the number of joins needed to retrieve competency achievement criteria information but would add complexity to the ``oel_tagging_objecttag`` table and make it less flexible for other uses.

   1. Pros

      1. Reduces number of joins needed to retrieve competency achievement criteria information
      2. Single-row lookup per object tag when the competency criteria is a 1:1 mapping to a tag/object association
      3. Potentially simpler UI/API if all consumers already pivot around ``objecttag`` and do not need criteria grouping
   2. Cons

      1. Dilutes semantics as ``objecttag`` stops being a pure generic tagging junction.
      2. Many nullable columns. Most tags won't be criteria; you'll add mostly-null fields unless they're scoped with a type discriminator and partial indexes.
      3. It becomes easy to create criteria rows missing required fields (rule profile, overrides) unless enforced with a discriminator and additional constraints.
      4. It breaks or complicates criteria grouping because a single ``objecttag`` may need to participate in multiple criteria groups. You would need to duplicate ``objecttag`` rows or add another join table, which defeats the intended simplification.
      5. Down the road, permissioning differences in who can create/edit criteria vs who can create/edit generic tags would be harder to implement and audit.
      6. Performance risk if the objecttag table becomes very large and is queried for both generic tagging and competency criteria use cases with mostly-null criteria fields.
      7. Future rule types may require different fields, further bloating ``objecttag`` and reducing performance for non-competency use cases.

3. Add a generic oel\_tagging\_objecttag\_metadata table to attempt to assist with pluggable metadata concept. This table would have foreign keys to each metadata table, currently only competency\_criteria\_group and competency\_criteria as well as a type field to indicate what metadata table is being pointed to.

   1. Pros

      1. Centrally organizes metadata associations in one place
   2. Cons

      1. Adds additional overhead to retrieve specific metadata

4. Split rule storage into per-type tables (for example, ``competency_criteria_grade_rule`` and ``competency_criteria_mastery_rule``) instead of a single JSON payload.

   1. Pros

      1. Provides stricter schemas and validation per rule type
   2. Cons

      1. Increases table count and join complexity as new rule types are added

5. Require a strict, always-cascading scope on ``CompetencyRuleProfile`` (organization always set; taxonomy only settable alongside organization; course only settable alongside both), instead of letting each row be scoped by at most one of the three independently.

   1. Pros

      1. At most one profile can ever apply to a given criterion by construction, without a separate uniqueness constraint or tiebreak field.
   2. Cons

      1. Does not fit taxonomies that span multiple organizations: a single taxonomy-wide default would need one duplicate profile per associated organization.
      2. Requires reconciling profiles whenever an organization is added to or removed from a taxonomy.
      3. Organization and taxonomy are not naturally nested (a taxonomy can belong to many organizations and vice versa), so forcing one to always contain the other does not reflect the actual relationship between them.

6. Enforce ``CompetencyRuleProfile`` scope uniqueness with per-scope conditional/partial unique constraints (Django ``UniqueConstraint(condition=Q(...))``) directly on the three nullable scope columns, instead of a generated ``scope_code`` column (Decision 3).

   1. Pros

      1. No new column; the constraint reads directly off the existing scope columns.
      2. The commonly-recommended Django pattern for "unique except when null" scoping.
   2. Cons

      1. Silently does not work on this project's tested and production database backend. Django compiles a conditional ``UniqueConstraint`` to a partial index, which MySQL does not support; Django raises only a non-fatal system-check warning (``models.W036``) and skips creating the constraint, leaving the uniqueness rule completely unenforced at the database level.
      2. The gap would surface only as a data-integrity incident under concurrent writes, not as a test or migration failure, since SQLite (used for quick local test runs) does support partial indexes and would mask the problem in that environment.

Changelog
---------

2026-07-27:

* Made the learner status indexes unique, so there is one row per learner and node. This is what
  the in-place, monotone status updates in :ref:`openedx-learning-adr-0004` read, lock, and update.

2026-09-01:

* Amended Decisions 2, 3, 4, and 7 for issue #655: added archive-vs-hard-delete guardrails on
  records referenced by learner status (``CompetencyCriteriaGroup``, ``CompetencyCriteria``,
  ``Tag``, and ``Taxonomy``/``CompetencyTaxonomy``), and narrowed ``CompetencyRuleProfile`` scope
  to the single system-default row for this MVP. This entry supersedes the 2026-07-27 entry's
  characterization of :ref:`openedx-learning-adr-0004`'s locking behavior: the accepted ADR 0004
  has only its staff-correction path take a lock, not every update. That entry is left as
  originally written rather than edited in place.

2026-09-04:

* For issue #676: clarified that ``course_id`` applies to every group in a
  course-scoped subtree, not just the top one, and can't change after creation.
  Simplified retrieval scope and dropped the pagination note, since both assumed
  course-date windowing, which #676's new read path doesn't use.
