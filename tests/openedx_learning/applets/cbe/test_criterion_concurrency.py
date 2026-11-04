"""
Two-connection tests for the row locks that criterion authoring writers take.

Plain SQLite has no row locks, so these run only where ``select_for_update()`` is real (MySQL in CI).
Each test runs the delete in one thread and pauses it inside its transaction, then lets a second
connection act while the first still holds its locks.
"""
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
import rules
from django.apps import apps
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.utils import OperationalError
from rules.permissions import permissions as rule_permissions

from openedx_catalog.models import CourseRun
from openedx_learning.api import (
    CompetencyCriteriaGroupArchivedError,
    CompetencyCriterionArchivedError,
    GradedObjectScore,
    associate_competency_criterion,
    bulk_update_competency_criteria,
    delete_or_archive_competency_criterion,
    record_graded_object_statuses,
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


def tag_row_is_locked(tag: Tag) -> bool:
    """
    Return whether another connection holds the lock on ``tag``'s row.

    ``nowait`` makes MySQL fail at once instead of waiting, so the answer involves no timing.
    """
    try:
        with transaction.atomic():
            Tag.objects.select_for_update(nowait=True).get(pk=tag.pk)
    except DatabaseError:
        return True
    return False


@pytest.fixture(name="sibling_object_id")
def _sibling_object_id(course_run: CourseRun) -> str:
    """The usage key of a second sequential in the same course as the criterion in ``leaf_and_criterion``."""
    key = course_run.course_key
    assert key is not None
    return f"block-v1:{key.org}+{key.course}+{key.run}+type@sequential+block@s2"


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


def test_tag_lock_is_held_for_the_whole_delete_request(
    leaf_and_criterion: tuple[CompetencyCriteriaGroup, CompetencyCriterion],
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Worker],
    tag: Tag,
    user,
) -> None:
    """The competency's Tag row stays locked from the delete's first write until its transaction commits."""
    _, criterion = leaf_and_criterion
    pause = pause_before_the_walk(monkeypatch)
    try:
        delete = start(delete_or_archive_competency_criterion, criterion.id, user)
        pause.wait_until_reached()
        assert tag_row_is_locked(tag)
    finally:
        pause.release.set()

    delete.result()
    assert not tag_row_is_locked(tag)


@pytest.mark.parametrize("learner_progress", [False], ids=["hard delete"])
def test_create_for_the_same_competency_waits_and_its_criterion_survives(
    leaf_and_criterion: tuple[CompetencyCriteriaGroup, CompetencyCriterion],
    monkeypatch: pytest.MonkeyPatch,
    sibling_object_id: str,
    start: Callable[..., Worker],
    user,
) -> None:
    """
    A create for the competency whose only criterion is being deleted waits, then builds a fresh tree.

    The delete removes the leaf, course-level group and root. The create, which would otherwise reuse
    those containers while the delete is removing them, runs afterwards and makes its own.
    """
    leaf, criterion = leaf_and_criterion
    course_level_group_id = leaf.parent_id
    assert course_level_group_id is not None
    root_id = CompetencyCriteriaGroup.objects.get(pk=course_level_group_id).parent_id
    pause = pause_before_the_walk(monkeypatch)
    try:
        delete = start(delete_or_archive_competency_criterion, criterion.id, user)
        pause.wait_until_reached()
        create = start(associate_competency_criterion, criterion.object_tag.tag_id, sibling_object_id)
        assert create.is_blocked()
    finally:
        pause.release.set()

    delete.result()
    new_criterion = create.result()
    new_leaf = new_criterion.group
    assert new_leaf.id != leaf.id
    new_course_level_group = new_leaf.parent
    assert new_course_level_group is not None
    assert new_course_level_group.id != course_level_group_id
    new_root = new_course_level_group.parent
    assert new_root is not None
    assert new_root.id != root_id
    assert new_root.parent_id is None
    assert CompetencyCriterion.objects.filter(pk=new_criterion.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk__in=[leaf.id, course_level_group_id, root_id]).exists()


@pytest.mark.parametrize("learner_progress", [False], ids=["hard delete"])
def test_two_deletes_of_sibling_criteria_leave_no_empty_leaf(
    leaf_and_criterion: tuple[CompetencyCriteriaGroup, CompetencyCriterion],
    monkeypatch: pytest.MonkeyPatch,
    sibling_object_id: str,
    start: Callable[..., Worker],
    user,
) -> None:
    """
    Two criteria in one leaf, deleted at the same time, leave neither the leaf nor anything above it.

    Without the Tag lock each delete would still see the other's criterion in the leaf and stop the walk
    there, leaving an empty leaf behind.
    """
    leaf, first = leaf_and_criterion
    course_level_group_id = leaf.parent_id
    assert course_level_group_id is not None
    root_id = CompetencyCriteriaGroup.objects.get(pk=course_level_group_id).parent_id
    object_tag = ObjectTag.objects.create(
        object_id=sibling_object_id, taxonomy_id=first.object_tag.taxonomy_id, tag_id=first.object_tag.tag_id,
    )
    second = CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, rule_profile=first.rule_profile)
    pause = pause_before_the_walk(monkeypatch)
    try:
        first_delete = start(delete_or_archive_competency_criterion, first.id, user)
        pause.wait_until_reached()
        second_delete = start(delete_or_archive_competency_criterion, second.id, user)
        assert second_delete.is_blocked()
    finally:
        pause.release.set()

    assert first_delete.result().archived is False
    assert second_delete.result().archived is False
    assert not CompetencyCriterion.objects.filter(pk__in=[first.id, second.id]).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk__in=[leaf.id, course_level_group_id, root_id]).exists()


def _grade_then_wait(
    user_id: int, object_id: str, reached: threading.Event, release: threading.Event,
) -> None:
    """Record a passing grade for ``object_id`` and hold the grade's transaction open until ``release`` is set."""
    with transaction.atomic():
        record_graded_object_statuses(
            user_id=user_id, scores=[GradedObjectScore(object_id=object_id, fraction=Decimal("0.9"))],
        )
        reached.set()
        if not release.wait(WAIT_SECONDS):
            raise RuntimeError("the grade transaction was never released")


@pytest.mark.parametrize("learner_progress", [False], ids=["hard delete"])
def test_grade_write_and_hard_delete_fail_one_side_never_silently_grade_first(
    leaf_and_criterion: tuple[CompetencyCriteriaGroup, CompetencyCriterion],
    start: Callable[..., Worker],
    user,
) -> None:
    """
    A hard delete that races a grade write already holding the criterion fails with ``IntegrityError``.

    The grade's status row holds a lock on the criterion, so the delete waits for the grade to commit and
    then fails the foreign-key check. Nothing is lost: both rows survive, and a second delete archives the
    criterion.
    """
    _, criterion = leaf_and_criterion
    grade_reached, grade_release = threading.Event(), threading.Event()
    try:
        grade = start(_grade_then_wait, user.id, criterion.object_tag.object_id, grade_reached, grade_release)
        assert grade_reached.wait(WAIT_SECONDS), "the grade write never recorded its status"
        delete = start(delete_or_archive_competency_criterion, criterion.id, user)
        assert delete.is_blocked()
    finally:
        grade_release.set()

    grade.result()
    with pytest.raises(IntegrityError):
        delete.result()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert StudentCompetencyCriterionStatus.objects.filter(criterion=criterion, user=user).exists()
    assert delete_or_archive_competency_criterion(criterion.id, user).archived is True


@pytest.mark.parametrize("learner_progress", [False], ids=["hard delete"])
def test_grade_write_and_hard_delete_fail_one_side_never_silently_delete_first(
    leaf_and_criterion: tuple[CompetencyCriteriaGroup, CompetencyCriterion],
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Worker],
    user,
) -> None:
    """
    A grade write that races a hard delete already holding the criterion fails with ``IntegrityError``.

    The grade's status insert waits for the delete to commit and then fails the foreign-key check. The
    criterion is gone and no status row was written.
    """
    _, criterion = leaf_and_criterion
    pause = pause_before_the_walk(monkeypatch)
    grade_reached, grade_release = threading.Event(), threading.Event()
    # Already released: the grade's transaction ends as soon as its insert returns, so only the delete holds locks.
    grade_release.set()
    try:
        delete = start(delete_or_archive_competency_criterion, criterion.id, user)
        pause.wait_until_reached()
        grade = start(_grade_then_wait, user.id, criterion.object_tag.object_id, grade_reached, grade_release)
        assert grade.is_blocked()
    finally:
        pause.release.set()

    assert delete.result().archived is False
    with pytest.raises(IntegrityError):
        grade.result()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert not StudentCompetencyCriterionStatus.objects.filter(user=user).exists()
