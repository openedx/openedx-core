"""
Delete-behavior tests for CompetencyCriteriaGroup's own foreign keys.

| Foreign key | Value | Why |
| CompetencyCriteriaGroup.parent | CASCADE | a subtree is meaningless without its parent |
| CompetencyCriteriaGroup.tag | CASCADE | a criteria tree is meaningless without its competency |
| CompetencyCriteriaGroup.course | CASCADE | a course-scoped tree is meaningless without its run |

``on_delete`` expresses containment rather than protection (ADR-0002 Decision 7): it governs
deletion of the row a foreign key points *at*, never the row holding it. So all three edges above
are how Django's collector walks *down* the tree once something above it is deleted.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.apps import apps
from django.db import connection

from openedx_catalog.models import CourseRun
from openedx_learning.models import CompetencyCriteriaGroup, CompetencyTaxonomy
from openedx_tagging.models import Tag

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------------------------
# Each CASCADE test asserts the referencing row existed beforehand and is gone afterward, not
# merely that no exception was raised.


# ---------------------------------------------------------------------------------------------


def test_deleting_a_group_also_deletes_its_child_groups(tag: Tag) -> None:
    """
    Deleting a CompetencyCriteriaGroup cascades to any child group referencing it via `parent`:
    the delete succeeds and the child row is gone too.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    assert CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()

    root.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()


def test_deleting_a_tag_also_deletes_its_competency_criteria_groups(tag: Tag, group: CompetencyCriteriaGroup) -> None:
    """
    Deleting a Tag cascades to any CompetencyCriteriaGroup referencing it via `tag`: the delete
    succeeds and the group row is gone. Also confirms django-simple-history records the cascaded
    removal as its own historical row (history_type='-'), not silently: an author or auditor
    reviewing history for a group that vanished this way still finds why it did.
    """
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    group_pk = group.pk

    tag.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group_pk).exists()

    historical_group = apps.get_model("openedx_learning", "HistoricalCompetencyCriteriaGroup")
    assert historical_group.objects.filter(id=group_pk, history_type="-").exists()


def test_deleting_a_course_run_also_deletes_its_course_scoped_criteria_groups(
    tag: Tag, course_run: CourseRun
) -> None:
    """
    Deleting a CourseRun cascades to any CompetencyCriteriaGroup scoped to it via `course`: the
    delete succeeds and the group row is gone too. A course-scoped criteria tree has no meaning
    once the course run it evaluates against no longer exists.
    """
    group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run)
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()

    course_run.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


def test_a_cascaded_group_removal_is_recorded_in_history(tag: Tag) -> None:
    """
    A group removed by a cascade, rather than by a direct delete, still gets its own historical
    row with history_type '-'. An author or auditor reviewing history for a group that vanished
    this way still finds why it did.
    """
    historical_group = apps.get_model("openedx_learning", "HistoricalCompetencyCriteriaGroup")
    group = CompetencyCriteriaGroup.objects.create(tag=tag)
    group_pk = group.pk

    tag.delete()

    assert historical_group.objects.filter(id=group_pk, history_type="-").exists()


def test_deleting_a_group_at_depth_also_deletes_every_descendant_group(tag: Tag) -> None:
    """
    Deleting a CompetencyCriteriaGroup removes not just its direct children but every group
    beneath it at any depth: `parent` is a self-referential CASCADE, so a single delete has
    Django's collector walk the whole subtree, not just one level. Deleting the root and checking
    the grandchild is what actually exercises that recursion; deleting the middle node instead
    would only re-prove the one-hop cascade the depth-1 test above already covers.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    grandchild = CompetencyCriteriaGroup.objects.create(tag=tag, parent=child)

    root.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=grandchild.pk).exists()


def test_deleting_a_taxonomy_also_deletes_its_tags_criteria_groups(competency_taxonomy: CompetencyTaxonomy) -> None:
    """
    Deleting a CompetencyTaxonomy cascades through every Tag it owns (already CASCADE in
    openedx_tagging) and, transitively, through this model's own `tag` CASCADE: every
    CompetencyCriteriaGroup for a tag under that taxonomy is gone too.
    """
    tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Writing Poetry")
    group = CompetencyCriteriaGroup.objects.create(tag=tag)

    competency_taxonomy.delete()

    assert not Tag.objects.filter(pk=tag.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


# ---------------------------------------------------------------------------------------------
# MySQL collector semantics, reproduced on SQLite
# MySQL cannot defer foreign-key constraint checks, and Django's CASCADE handler reads that
# flag directly: it nulls a nullable cascading foreign key before the DELETE. On SQLite that
# nulling never happens, so the test below monkeypatches the flag to reproduce it. Without the
# monkeypatch it passes against broken and correct code alike, so do not drop it.


# ---------------------------------------------------------------------------------------------


def test_course_run_delete_cascades_its_course_scoped_criteria_group_under_mysql_collector_semantics(
    monkeypatch: pytest.MonkeyPatch, tag: Tag, course_run: CourseRun
) -> None:
    """
    Deleting a CourseRun with a course-scoped CompetencyCriteriaGroup succeeds and cascades the
    group away even under MySQL's non-deferred constraint semantics. `course` is a nullable
    cascading foreign key, so Django's collector nulls it before the DELETE rather than only
    after. CompetencyCriteriaGroup carries no uniqueness constraint a null `course_id` could
    collide with, so this path is expected to just succeed; pinned here so a regression that
    breaks it does not go unnoticed.
    """
    monkeypatch.setattr(type(connection.features), "can_defer_constraint_checks", False, raising=False)
    group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run)

    course_run.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
