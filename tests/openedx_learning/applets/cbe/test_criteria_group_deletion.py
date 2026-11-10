"""Delete-behavior tests for CompetencyCriteriaGroup's own foreign keys."""
import pytest
from django.apps import apps

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
    Deleting a CourseRun cascades to any CompetencyCriteriaGroup scoped to it via `course`, and
    through that group's `parent` CASCADE to the leaf groups under it: the delete succeeds, the
    course-level group and leaf rows are gone, and the root above them stays. A course-scoped
    subtree has no meaning once the course run it evaluates against no longer exists.

    `course` is a nullable cascading foreign key. MySQL cannot defer foreign-key constraint
    checks, so Django's collector nulls such a key before the DELETE; SQLite never does. A
    regression in that MySQL path therefore fails this test in CI (which runs MySQL) while still
    passing locally on SQLite.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    leaves = [CompetencyCriteriaGroup.objects.create(tag=tag, parent=group) for _ in range(2)]
    group_pks = [group.pk, *(leaf.pk for leaf in leaves)]
    assert CompetencyCriteriaGroup.objects.filter(pk__in=group_pks).count() == 3

    course_run.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk__in=group_pks).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()


def test_deleting_a_group_at_depth_also_deletes_every_descendant_group(tag: Tag) -> None:
    """
    Deleting a CompetencyCriteriaGroup removes not just its direct children but every group
    beneath it at any depth: `parent` is a self-referential CASCADE, so a single delete has
    Django's collector walk the whole subtree, not just one level. Deleting the root and checking
    the grandchild is what actually exercises that recursion.
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


def test_deleting_a_root_with_descendants_frees_its_place(tag: Tag, course_run: CourseRun) -> None:
    """Deleting a root deletes its whole subtree and frees the tag for a new root."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    course_level_group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    leaves = [CompetencyCriteriaGroup.objects.create(tag=tag, parent=course_level_group) for _ in range(2)]
    group_pks = [root.pk, course_level_group.pk, *(leaf.pk for leaf in leaves)]
    assert CompetencyCriteriaGroup.objects.filter(pk__in=group_pks).count() == 4

    root.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk__in=group_pks).exists()
    new_root = CompetencyCriteriaGroup.objects.create(tag=tag)
    assert new_root.scope_code == f"tag:{tag.pk}"
