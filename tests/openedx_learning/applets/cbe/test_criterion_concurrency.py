"""
Two-connection tests for the row locks that criterion authoring writers take.

Plain SQLite has no row locks, so these run only where ``select_for_update()`` is real (MySQL in CI).
Each test runs the delete in one thread and pauses it inside its transaction, then lets a second
connection act while the first still holds its locks.
"""
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any

import pytest
import rules
from django.apps import apps
from django.db import connection
from django.db.utils import OperationalError
from rules.permissions import permissions as rule_permissions

from openedx_catalog.models import CourseRun
from openedx_learning.api import (
    CompetencyCriteriaGroupArchivedError,
    CompetencyCriterionArchivedError,
    bulk_update_competency_criteria,
    delete_or_archive_competency_criterion,
    update_competency_criteria_group,
)
from openedx_learning.applets.cbe import api as cbe_api
from openedx_learning.applets.cbe.api import _cascade_from_group, _create_leaf_group
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    LogicOperator,
    MasteryStatus,
    RuleType,
    StudentCompetencyCriterionStatus,
)
from openedx_tagging.models import ObjectTag, Tag

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(not connection.features.has_select_for_update, reason="needs real row locks"),
    pytest.mark.usefixtures("migration_seed_rows"),
]

CHANGE_OBJECTTAG_OBJECTID = "oel_tagging.change_objecttag_objectid"
NEW_RULE: dict[str, Any] = {
    "rule_type_override": RuleType.GRADE,
    "rule_payload_override": {"op": "gte", "value": 0.9, "scale": "percent"},
}
# How long a test waits for a thread to reach a pause point or finish; far above any healthy run.
WAIT_SECONDS = 30
# A worker blocked on a lock fails after this many seconds instead of MySQL's default fifty.
LOCK_WAIT_SECONDS = 4


@pytest.fixture(autouse=True)
def _allow_object_level_tagging() -> Iterator[None]:
    """Let any user tag any object; restore the real rule afterwards."""
    original = rule_permissions[CHANGE_OBJECTTAG_OBJECTID]
    rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, lambda _user, _object_id: True)
    yield
    rules.set_perm(CHANGE_OBJECTTAG_OBJECTID, original)


class Worker:
    """
    A function running in its own thread, and so on its own database connection.

    The worker's exception is kept and re-raised by ``result()``, so a failure in the thread fails
    the test instead of vanishing.
    """

    def __init__(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self._func, self._args, self._kwargs = func, args, kwargs
        self._value: Any = None
        self._error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        """Thread body: cap lock waits, run the function, keep its outcome, and close the connection."""
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SET SESSION innodb_lock_wait_timeout = {LOCK_WAIT_SECONDS}")
            self._value = self._func(*self._args, **self._kwargs)
        except BaseException as error:
            self._error = error
        finally:
            # The table flush after the test waits on any transaction this connection left open.
            connection.close()

    def is_blocked(self) -> bool:
        """Return whether the worker is still running after a short wait, i.e. it is waiting on a lock."""
        self.thread.join(timeout=1.0)
        return self.thread.is_alive()

    def result(self) -> Any:
        """Wait for the worker, then return its value or re-raise its exception."""
        self.thread.join(timeout=WAIT_SECONDS)
        assert not self.thread.is_alive(), "worker is still running"
        if self._error is not None:
            raise self._error
        return self._value


@pytest.fixture(name="start")
def _start() -> Iterator[Callable[..., Worker]]:
    """Return a function that starts a Worker, and wait for every Worker it started once the test ends."""
    workers: list[Worker] = []

    def _start_worker(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Worker:
        workers.append(Worker(func, *args, **kwargs))
        return workers[-1]

    yield _start_worker
    for worker in workers:
        worker.thread.join(timeout=WAIT_SECONDS)


class PausePoint:
    """Two events that stop the first call of a wrapped function until the test lets it go."""

    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()
        self._tripped = False

    def wrap(self, original: Callable[..., Any]) -> Callable[..., Any]:
        """Return ``original`` wrapped to signal ``reached`` and wait for ``release`` on its first call only."""

        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            if not self._tripped:
                self._tripped = True
                self.reached.set()
                if not self.release.wait(WAIT_SECONDS):
                    raise RuntimeError("pause point was never released")
            return original(*args, **kwargs)

        return _wrapper

    def wait_until_reached(self) -> None:
        """Wait for the paused thread to arrive."""
        assert self.reached.wait(WAIT_SECONDS), "the delete never reached its pause point"


def pause_before_the_walk(monkeypatch: pytest.MonkeyPatch) -> PausePoint:
    """
    Pause the delete after it writes the criterion and before it walks up the groups.

    The walk calls itself at every level, so only its first call pauses.
    """
    pause = PausePoint()
    monkeypatch.setattr(cbe_api, "_cascade_from_group", pause.wrap(_cascade_from_group))
    return pause


def pause_after_the_walk(monkeypatch: pytest.MonkeyPatch) -> PausePoint:
    """Pause the delete after the walk, when it builds its result, which is the last step inside its transaction."""
    pause = PausePoint()
    monkeypatch.setattr(cbe_api, "CriterionDeletionResult", pause.wrap(cbe_api.CriterionDeletionResult))
    return pause


@pytest.fixture(name="learner_progress", params=[False, True], ids=["hard delete", "archive"])
def _learner_progress(request: pytest.FixtureRequest) -> bool:
    """Whether the criterion has a learner status row, which makes a delete archive it instead of removing it."""
    return request.param


@pytest.fixture(name="leaf_and_criterion")
def _leaf_and_criterion(
    learner_progress: bool,
    tag: Tag,
    course_run: CourseRun,
    default_rule_profile: CompetencyRuleProfile,
    user,
) -> tuple[CompetencyCriteriaGroup, CompetencyCriterion]:
    """A leaf group holding one criterion, with a learner status row on it if ``learner_progress``."""
    leaf = _create_leaf_group(tag, course_run)
    key = course_run.course_key
    assert key is not None
    object_tag = ObjectTag.objects.create(
        object_id=f"block-v1:{key.org}+{key.course}+{key.run}+type@sequential+block@s1",
        taxonomy=tag.taxonomy,
        tag=tag,
    )
    criterion = CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, rule_profile=default_rule_profile)
    if learner_progress:
        now = datetime.now(timezone.utc)
        StudentCompetencyCriterionStatus.objects.create(
            user=user, criterion=criterion, status_id=MasteryStatus.DEMONSTRATED, created=now, modified=now,
        )
    return leaf, criterion


def test_delete_and_bulk_update_do_not_deadlock(
    learner_progress: bool,
    leaf_and_criterion: tuple[CompetencyCriteriaGroup, CompetencyCriterion],
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Worker],
    user,
) -> None:
    """
    A bulk update of a criterion that is being deleted waits for the delete, then fails cleanly.

    Neither side may be killed by the database as a deadlock victim. After a hard delete the update finds
    nothing to edit; after an archive it finds the criterion archived.
    """
    leaf, criterion = leaf_and_criterion
    pause = pause_before_the_walk(monkeypatch)
    try:
        delete = start(delete_or_archive_competency_criterion, criterion.id, user)
        pause.wait_until_reached()
        bulk_update = start(bulk_update_competency_criteria, [criterion.id], leaf.id, user=user, **NEW_RULE)
        assert bulk_update.is_blocked()
    finally:
        pause.release.set()

    # An OperationalError (MySQL error 1213, deadlock) raised by either worker is re-raised here.
    result = delete.result()
    expected: tuple[type[Exception], ...]
    if learner_progress:
        expected = (CompetencyCriterionArchivedError,)
    else:
        expected = (CompetencyCriterion.DoesNotExist, CompetencyCriteriaGroup.DoesNotExist)
    with pytest.raises(expected) as raised:
        bulk_update.result()
    assert not isinstance(raised.value, OperationalError)
    assert result.archived is learner_progress


def test_patch_does_not_write_onto_an_archived_or_deleted_group(
    learner_progress: bool,
    leaf_and_criterion: tuple[CompetencyCriteriaGroup, CompetencyCriterion],
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Worker],
    user,
) -> None:
    """
    A PATCH of a leaf's logic operator that races the delete of its only criterion changes nothing.

    The delete archives or deletes the leaf. The PATCH waits for it, then reports the group as archived
    or missing instead of writing onto it.
    """
    leaf, criterion = leaf_and_criterion
    assert leaf.logic_operator == LogicOperator.OR
    pause = pause_after_the_walk(monkeypatch)
    try:
        delete = start(delete_or_archive_competency_criterion, criterion.id, user)
        pause.wait_until_reached()
        patch = start(update_competency_criteria_group, leaf.id, logic_operator=LogicOperator.AND, user=user)
        assert patch.is_blocked()
    finally:
        pause.release.set()

    assert delete.result().archived is learner_progress
    expected = CompetencyCriteriaGroupArchivedError if learner_progress else CompetencyCriteriaGroup.DoesNotExist
    with pytest.raises(expected):
        patch.result()
    historical_group = apps.get_model("openedx_learning", "HistoricalCompetencyCriteriaGroup")
    assert not historical_group.objects.filter(id=leaf.id, logic_operator=LogicOperator.AND).exists()
