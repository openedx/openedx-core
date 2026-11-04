.. _openedx-learning-adr-0004:

4. How should learner competency mastery be recorded concurrently and at scale?
================================================================================

Status
------
Accepted.

Context
-------
A learner's mastery of one competency is stored at three levels of the criteria tree: the graded
leaf criterion, each criteria group above it, and the competency itself. There is one row per
learner and node, updated in place (:ref:`openedx-learning-adr-0002`,
:ref:`openedx-learning-adr-0003`). Each row holds one of three values, lowest to highest:
``AttemptedNotDemonstrated``, ``PartiallyAttempted``, ``Demonstrated``.

One grade change updates the leaf and then every row above it, for many learners at once. This ADR
decides how those updates stay correct when two of them for the same learner overlap.

The problem: a group requires both Assignment A and Assignment B, and celery tasks recomputing
grades and competency statuses for this worker run at the same time. That is likely to happen
when instructor actions trigger multiple subsection grading events.
Each of the two writers sees its own assignment done and the other still outstanding,
so both write "not demonstrated" for the group. Both are wrong, both have finished, and nothing is
left to correct it.

Two constraints shape the answer. First, **the grading task cannot be one transaction**: it reads
MongoDB and memcached, queues further celery tasks, publishes events, and triggers database writes
owned by four other Django apps. Wrapping all of that would roll back other apps' data and publish
events for a grade that never commits. Second, **everything above the leaf is derived**: a group's
value can always be recalculated from the leaves beneath it, so the leaf is the only row that is a
direct consequence of the grade.

That constraint is why the whole grading task can't be one transaction. A separate constraint is why
even a transaction scoped only to the leaf and its rollup would still be wrong: two tasks finishing different
assignments for the same group race on that group's row regardless of transaction size, since each transaction
hides its writes from the other until it commits (Rejected Alternative 2).

Decision
--------

1. **Write the leaf status in the same transaction as the grade. Nothing above it.**
   The grading task calls one openedx-core function, which writes the leaf, so the grade and its
   leaf commit or fail together.

2. **Schedule rollup as a secondary celery task, asynchronously after the grade is recorded.**
   This separation avoids direct contention between concurrent grade changes on shared parent nodes.
   The secondary task is automatically retried by celery if it fails.

3. **Rollup commits each level before reading the next, with no locks.**
   A writer sees only committed data, so whichever writer reads a parent last sees all its children
   at their final values and computes the correct result. This depends on Decision 4, which provides
   a reconciliation rule.

4. **Automatic updates may only raise a status, never lower it.**
   Each write stores whichever is higher, the stored or the newly computed value, so concurrent
   writers cannot overwrite each other. A writer reading stale data can only compute a value that
   is too low, and too low is discarded. That is also what makes celery's repeated and out-of-order
   delivery harmless.

5. **Add a manually-invoked recovery mechanism.**
   For example a management command or Django admin action forces roll-ups to recalculate for a given range, to recover from
   operational failures, content tagging errors, or bugs in the roll-up code that celery's retry
   won't catch.

6. **Only a direct staff edit may lower a status.** A staff correction may set any value, and the
   rows above it are recalculated and overwritten rather than merged. A later grade change can raise
   what an edit lowered, but never lower what an edit raised.    The staff correction takes a row lock
   on the ``StudentCompetencyCriteriaGroupStatus`` row for that learner and the competency's root
   criteria group, the group with no parent; no other learner-status writer takes a lock. Criterion
   authoring writers (create, delete, bulk update, and group update) serialize per competency on
   the competency's ``Tag`` row, each taking that lock first and no other group or criterion row lock.



Rejected Alternatives
---------------------

1. Lock each criteria group row before recalculating it.

    - Pros:
        - Correctness comes from making contending writers take turns, which is easier to prove than
          an argument about the order of commits and reads.
    - Cons:
        - One grade change can affect several leaves of the same tree, so a writer can need several
          locks at once, which introduces deadlocks that need their own detection and retry code.
        - It puts a lock wait on every grade change. MySQL waits 50 seconds by default, inside a task
          allowed 300 seconds in total.
        - Correctness would depend on the isolation level, silently, and SQLite has no row locks, so
          the test suite could not exercise it.

2. Share one transaction between the grade and the whole roll-up, not just the leaf.

    - Pros:
        - The grade and every mastery row it touches would commit or fail together, so no roll-up
          could ever be left unfinished and Decisions 2 and 5 would be unnecessary.
    - Cons:
        - The grading task cannot be wrapped in a transaction at all, for the reasons in the Context.
        - Wrapping only the roll-up is worse than doing nothing: it hides each writer's changes from
          the other until both have finished, which is the problem in the Context again, one level up
          the tree and harder to diagnose.

3. Take one lock on the learner's competency root-group status row, then recalculate the whole
   subtree beneath it.

    - Pros:
        - Easy to reason about: one lock, always the same row, so no deadlock and no ordering
          argument.
    - Cons:
        - It puts a lock, and its timeout handling, on every grade change rather than only on the
          rare path that lowers a value.
        - It needs row locks, which SQLite does not support.

    This is the right shape for the paths that lower a value, and Decision 6 uses it there.

4. Use a coarser lock, either one per deployment or one per learner.

    - Pros:
        - A single lock replaces the ordering argument in Decision 3.
    - Cons:
        - A deployment-wide lock serializes every learner behind every other, giving up the
          throughput bursty grading needs.
        - The "one per learner" option makes a learner's unrelated competencies wait for each
          other, since one lock would then cover every tree they have.
        - Either kind adds machinery for acquiring and releasing locks, and for recovering from a
          dead lock holder, across a very large key space.

5. Recalculate the derived levels on every read instead of storing them.

    - Pros:
        - No roll-up writes at all, so there is nothing to keep consistent.
    - Cons:
        - It moves a full bottom-up tree evaluation onto every read, the opposite of what dashboards
          need.
        - Already settled against in :ref:`openedx-learning-adr-0002`.

6. Send an event to openedx-core and do all the work in a separate celery task.

    - Pros:
        - Recording a grade would not depend on the competency code being installed or fast.
    - Cons:
        - openedx-core is a library and cannot own a celery queue, so every caller would supply one.
        - The leaf would no longer commit with the grade, giving up the one guarantee Decision 1 is
          cheap enough to keep.

7. Detect conflicts optimistically, with a version column and a retry loop for the losing write.

    - Pros:
        - Contention costs a retry rather than a wait.
    - Cons:
        - Decision 4 is already optimistic, without the retry loop. A write that loses has computed
          a value that is too low, and discarding those is exactly what Decision 4 does.

8. Read-after-write: after writing the leaf, re-read each parent's children before rolling up, to catch a race
   with another writer already in flight.

    - Pros:
        - Recovers from the race within the same request, without a separate marker or job.
    - Cons:
        - Decision 1 shares a transaction only between the grade and its leaf, and Decision 3 commits each rollup level
          separately before reading the next. That leaves no single transaction boundary for a read-after-write check to
          run inside: by the time a re-read would happen, the level below has already committed and could change again
          before the write completes.
        - It also only checks for a race at the moment each parent is read. If the worker crashes mid-cascade before reaching the next read,
          nothing notices the rollup was left unfinished. Decision 5's manual recovery mechanism exists to catch that case.

Changelog
---------

2026-11-04:

* Amended Decision 6 for issue #674: the rule that no other path takes a lock covers learner-status
  writers only. Criterion authoring writers serialize per competency on the ``Tag`` row.
