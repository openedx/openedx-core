"""
Tests for the 0017_criteria_group_scope_code migration's duplicate-root check and backfill.

Uses the `migrator` pytest fixture from django_test_migrations to run the actual
migration against historical (frozen) model states, the same way it will run against
real downstream data.
"""
from __future__ import annotations

import importlib

import pytest
from django.db import IntegrityError, connection, transaction

from openedx_learning.models import CompetencyCriteriaGroup

MIGRATE_FROM = ("openedx_learning", "0016_add_competency_course_run")
MIGRATE_TO = ("openedx_learning", "0017_criteria_group_scope_code")

DuplicateRootCriteriaGroupsError = importlib.import_module(
    "openedx_learning.migrations.0017_criteria_group_scope_code"
).DuplicateRootCriteriaGroupsError


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
    # The live model's save() must produce the code the backfill wrote, or a second root would slip past it. This
    # INSERT goes through the live model at state 0017, so a later migration that adds a column to this table breaks
    # it; when that happens, compare the backfilled value with the live model's computed code instead.
    with pytest.raises(IntegrityError), transaction.atomic():
        CompetencyCriteriaGroup.objects.create(tag_id=tag_a.pk)


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
