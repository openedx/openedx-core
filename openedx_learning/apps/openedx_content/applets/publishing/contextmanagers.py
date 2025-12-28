"""
Context Managers for internal use in the publishing app.

Do not use this directly outside the publishing app. Use the public API's
bulk_draft_changes_for instead (which will invoke this internally).
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable

from django.db.transaction import Atomic

from .models import DraftChangeLog


class DraftChangeLogContext(Atomic):
    """
    Context manager for batching draft changes into DraftChangeChangeLogs.

    🛑 This is a PRIVATE implementation. Outside of the publishing app, clients
    should use the bulk_draft_changes_for() API call instead of using this
    manager directly, since this is a bit experimental and the implementation
    may shift around a bit.

    The main idea is that we want to create a context manager that will keep
    track of what the "active" DraftChangeChangeLogs is for a given
    LearningPackage. The problem is that declaring the context can happen many
    layers higher in the stack than doing the draft changes. For instance,
    imagine::

        with bulk_draft_changes_for(learning_package.id):
            for section in course:
                update_section_drafts(learning_package_id, section)

    In this hypothetical code block, update_section_drafts might call a function
    to update sequences, which calls something to update units, etc. Only at the
    very bottom layer of the stack will those code paths actually alter the
    drafts themselves. It would make the API too cumbersome to explicitly pass
    the active DraftChangeChangeLog through every layer. So instead, we use this
    class to essentially keep the active DraftChangeChangeLog in a global
    (ContextVar) so that the publishing API draft-related code can access it
    later.

    Since it is possible to nest context managers, we keep a list of the
    DraftChangeChangeLogs that have been created and treat it like a stack that
    gets pushed to whenever we __enter__ and popped off whenever we __exit__.

    DraftChangeLogContext also subclasses Django's Atomic context manager, since
    any operation on multiple Drafts as part of a DraftChangeLog will want to be
    an atomic operation.
    """
    _draft_change_logs: ContextVar[list | None] = ContextVar('_draft_change_logs', default=None)

    def __init__(
        self,
        learning_package_id: int,
        changed_at: datetime | None = None,
        changed_by: int | None = None,
        exit_callbacks: list[Callable[[DraftChangeLog], None]] | None = None
    ) -> None:
        super().__init__(using=None, savepoint=False, durable=False)

        self.learning_package_id = learning_package_id
        self.changed_by = changed_by
        self.changed_at = changed_at or datetime.now(tz=timezone.utc)

        # This will get properly initialized on __enter__()
        self.draft_change_log = None

        # We currently use self.exit_callbacks as a way to run parent/child
        # side-effect creation. DraftChangeLogContext itself is a lower-level
        # part of the code that doesn't understand what containers are.
        self.exit_callbacks = exit_callbacks or []

    @classmethod
    def get_active_draft_change_log(cls, learning_package_id: int) -> DraftChangeLog | None:
        """
        Get the DraftChangeLogContext for new DraftChangeLogRecords.

        This is expected to be called internally by the publishing API when it
        modifies Drafts. If there is no active DraftChangeLog, this method will
        return None, and the caller should create their own DraftChangeLog.
        """
        draft_change_logs = cls._draft_change_logs.get()

        # If we've never used this manager...
        if draft_change_logs is None:
            return None

        # Otherwise, find the most recently created DraftChangeLog *that matches
        # the learning_package_id*. This is for two reasons:
        #
        # 1. We might nest operations so that we're working across multiple
        #    LearningPackages at once, e.g. copying content from different
        #    libraries and importing them into another library.
        # 2. It's a guard in case we somehow got the global state management
        #    wrong. We want the failure mode to be "we didn't find the
        #    DraftChangeLog you were looking for, so make another one and suffer
        #    a performance penalty", as opposed to, "we accidentally gave you a
        #    DraftChangeLog for an entirely different LearningPackage, and now
        #    your Draft data is corrupted."
        for draft_change_log in reversed(draft_change_logs):
            if draft_change_log.learning_package_id == learning_package_id:
                return draft_change_log

        # If we got here, then either the list was empty (the manager was used
        # at some point but exited), or none of the DraftChangeLogs are for the
        # correct LearningPackage.
        return None

    def __enter__(self):
        """
        Enter our context.

        This starts the transaction and sets up our active DraftChangeLog.
        """
        super().__enter__()

        self.draft_change_log = DraftChangeLog.objects.create(
            learning_package_id=self.learning_package_id,
            changed_by_id=self.changed_by,
            changed_at=self.changed_at,
        )
        draft_change_sets = self._draft_change_logs.get()
        if not draft_change_sets:
            draft_change_sets = []
        draft_change_sets.append(self.draft_change_log)
        self._draft_change_logs.set(draft_change_sets)

        return self.draft_change_log

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Exit our context.

        Pops the active DraftChangeLog off of our stack and run any
        post-processing callbacks needed. This callback mechanism is how child-
        parent side-effects are calculated.
        """
        draft_change_logs = self._draft_change_logs.get()
        if draft_change_logs:
            draft_change_log = draft_change_logs.pop()
            for exit_callback in self.exit_callbacks:
                exit_callback(draft_change_log)

            # Edge case: the draft changes that accumulated during our context
            # cancel each other out, and there are no actual
            # DraftChangeLogRecords at the end. In this case, we might as well
            # delete the entire DraftChangeLog.
            if not draft_change_log.records.exists():
                draft_change_log.delete()

        self._draft_change_logs.set(draft_change_logs)

        return super().__exit__(exc_type, exc_value, traceback)
