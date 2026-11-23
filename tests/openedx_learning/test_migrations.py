"""
Tests for the 0017_criteria_group_scope_code migration's duplicate-root check and backfill,
and the 0019_backfill_subtree_course backfill.

Uses the `migrator` pytest fixture from django_test_migrations to run the actual
migration against historical (frozen) model states, the same way it will run against
real downstream data.
"""
from __future__ import annotations

import importlib

import pytest
from django.db import IntegrityError, connection, transaction

MIGRATE_FROM = ("openedx_learning", "0016_add_competency_course_run")
MIGRATE_TO = ("openedx_learning", "0017_criteria_group_scope_code")

DuplicateRootCriteriaGroupsError = importlib.import_module(
    "openedx_learning.migrations.0017_criteria_group_scope_code"
).DuplicateRootCriteriaGroupsError

SUBTREE_MIGRATE_FROM = ("openedx_learning", "0018_criteria_group_subtree_course")
SUBTREE_MIGRATE_TO = ("openedx_learning", "0019_backfill_subtree_course")

subtree_migration = importlib.import_module("openedx_learning.migrations.0019_backfill_subtree_course")


def _create_tag(apps, taxonomy, value):
    """Create a root-level tag with a historical model."""
    Tag = apps.get_model("oel_tagging", "Tag")
    return Tag.objects.create(taxonomy=taxonomy, value=value, external_id=value, depth=0, lineage=value + "\t")


def _create_taxonomy(apps):
    """Create a taxonomy with a historical model."""
    Taxonomy = apps.get_model("oel_tagging", "Taxonomy")
    return Taxonomy.objects.create(name="Migration Test", export_id="migration_test")


def _column_names(Group) -> set[str]:
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, Group._meta.db_table)
    return {column.name for column in description}


@pytest.mark.django_db
def test_backfill_sets_scope_code_on_roots_only(migrator) -> None:
    """
    Every root group, archived or not, gets its tag's root scope_code; course-level groups
    and leaves stay null.
    """
    old_state = migrator.apply_initial_migration(MIGRATE_FROM)
    apps = old_state.apps
    Organization = apps.get_model("organizations", "Organization")
    CatalogCourse = apps.get_model("openedx_catalog", "CatalogCourse")
    CourseRun = apps.get_model("openedx_catalog", "CourseRun")
    Group = apps.get_model("openedx_learning", "CompetencyCriteriaGroup")

    taxonomy = _create_taxonomy(apps)
    tag_a = _create_tag(apps, taxonomy, "Competency A")
    tag_b = _create_tag(apps, taxonomy, "Competency B")
    org = Organization.objects.create(name="Org1", short_name="Org1")
    catalog_course = CatalogCourse.objects.create(org=org, course_code="Math100", title="Math", language="en")
    course_run = CourseRun.objects.create(
        catalog_course=catalog_course, run_code="A", course_key="course-v1:Org1+Math100+A", title="Math",
    )

    root_a = Group.objects.create(tag=tag_a)
    course_group = Group.objects.create(tag=tag_a, parent=root_a, course=course_run)
    leaf = Group.objects.create(tag=tag_a, parent=course_group)
    archived_leaf = Group.objects.create(tag=tag_a, parent=course_group, archived=True)
    archived_root_b = Group.objects.create(tag=tag_b, archived=True)

    new_state = migrator.apply_tested_migration(MIGRATE_TO)
    NewGroup = new_state.apps.get_model("openedx_learning", "CompetencyCriteriaGroup")

    assert NewGroup.objects.get(pk=root_a.pk).scope_code == f"tag:{tag_a.pk}"
    assert NewGroup.objects.get(pk=archived_root_b.pk).scope_code == f"tag:{tag_b.pk}"
    assert NewGroup.objects.get(pk=course_group.pk).scope_code is None
    assert NewGroup.objects.get(pk=leaf.pk).scope_code is None
    assert NewGroup.objects.get(pk=archived_leaf.pk).scope_code is None
    # The backfilled value must be the one save() writes for a root (see test_criteria_group.py), or a second
    # root would slip past the unique constraint. The live model cannot INSERT at state 0017, because a later
    # migration adds a column to this table, so the historical model writes the code save() would.
    with pytest.raises(IntegrityError), transaction.atomic():
        NewGroup.objects.create(tag_id=tag_a.pk, scope_code=f"tag:{tag_a.pk}")


@pytest.mark.django_db
def test_migration_reverses_cleanly(migrator) -> None:
    """Migrating back to 0016 drops scope_code and keeps existing groups."""
    new_state = migrator.apply_initial_migration(MIGRATE_TO)
    apps = new_state.apps
    Group = apps.get_model("openedx_learning", "CompetencyCriteriaGroup")
    tag = _create_tag(apps, _create_taxonomy(apps), "Competency A")
    root = Group.objects.create(tag=tag, scope_code=f"tag:{tag.pk}")

    old_state = migrator.apply_tested_migration(MIGRATE_FROM)
    OldGroup = old_state.apps.get_model("openedx_learning", "CompetencyCriteriaGroup")

    assert OldGroup.objects.filter(pk=root.pk).exists()
    assert "scope_code" not in _column_names(OldGroup)


@pytest.mark.django_db
def test_duplicate_roots_stop_the_migration_before_any_schema_change(migrator) -> None:
    """
    A tag with two roots, one of them archived, fails the migration with an error naming the
    tag and both groups, and leaves the table without a scope_code column.
    """
    old_state = migrator.apply_initial_migration(MIGRATE_FROM)
    apps = old_state.apps
    Group = apps.get_model("openedx_learning", "CompetencyCriteriaGroup")
    tag = _create_tag(apps, _create_taxonomy(apps), "Competency A")
    live_root = Group.objects.create(tag=tag)
    archived_root = Group.objects.create(tag=tag, archived=True)

    try:
        with pytest.raises(DuplicateRootCriteriaGroupsError) as excinfo:
            migrator.apply_tested_migration(MIGRATE_TO)

        assert f"tag {tag.pk} (root group ids: {live_root.pk}, {archived_root.pk})" in str(excinfo.value)
        assert "scope_code" not in _column_names(Group)
    finally:
        # The migrator fixture migrates to the latest state on teardown, which the duplicate would block.
        archived_root.delete()


@pytest.mark.django_db
def test_subtree_course_backfill_follows_the_derivation_rule(migrator, monkeypatch) -> None:
    """
    Migrating 0018 to 0019 backfills subtree_course on a nested tree built through historical models.

    The tree has groups above any course, a course-level group, and groups nested below it. Each
    group ends up with its own course if it has one, else its parent's subtree course, else null.
    The batch size is patched down to 1 so the batching loop runs more than once per course.
    """
    monkeypatch.setattr(subtree_migration, "BACKFILL_BATCH_SIZE", 1)
    old_state = migrator.apply_initial_migration(SUBTREE_MIGRATE_FROM)
    apps = old_state.apps
    Organization = apps.get_model("organizations", "Organization")
    CatalogCourse = apps.get_model("openedx_catalog", "CatalogCourse")
    CourseRun = apps.get_model("openedx_catalog", "CourseRun")
    Group = apps.get_model("openedx_learning", "CompetencyCriteriaGroup")

    org = Organization.objects.create(name="Org1", short_name="Org1")
    catalog_course = CatalogCourse.objects.create(org=org, course_code="Math100", title="Math", language="en")
    run_a = CourseRun.objects.create(
        catalog_course=catalog_course, run_code="A", course_key="course-v1:Org1+Math100+A", title="Math",
    )
    tag = _create_tag(apps, _create_taxonomy(apps), "Competency A")

    root = Group.objects.create(tag=tag, scope_code=f"tag:{tag.pk}")
    above_course = Group.objects.create(tag=tag, parent=root)
    course_a = Group.objects.create(tag=tag, parent=root, course=run_a)
    leaf = Group.objects.create(tag=tag, parent=course_a)
    nested = Group.objects.create(tag=tag, parent=leaf)
    archived_leaf = Group.objects.create(tag=tag, parent=course_a, archived=True)

    new_state = migrator.apply_tested_migration(SUBTREE_MIGRATE_TO)
    NewGroup = new_state.apps.get_model("openedx_learning", "CompetencyCriteriaGroup")

    expected_course_id = {
        root: None,
        above_course: None,
        course_a: run_a.pk,
        leaf: run_a.pk,
        nested: run_a.pk,
        archived_leaf: run_a.pk,
    }
    for group, course_id in expected_course_id.items():
        assert NewGroup.objects.get(pk=group.pk).subtree_course_id == course_id, group.pk
