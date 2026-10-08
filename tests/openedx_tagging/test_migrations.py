"""
Tests for openedx_tagging migrations, run against existing data.

Uses the `migrator` pytest fixture from django_test_migrations to run the actual
migration against historical (frozen) model states, the same way it will run against
real downstream data.
"""
from __future__ import annotations

import pytest

MIGRATE_FROM = ("oel_tagging", "0021_remove_system_defined_add_read_only")
MIGRATE_TO = ("oel_tagging", "0022_tag_external_id_not_null")

MIGRATE_0023_FROM = ("oel_tagging", "0022_tag_external_id_not_null")
MIGRATE_0023_TO = ("oel_tagging", "0023_archived_and_deletion_locked")


@pytest.mark.django_db
def test_backfill_generates_missing_external_ids(migrator) -> None:
    """
    Tags with a NULL external_id get one generated from their value; tags that already
    have one are left untouched; a collision between a generated value and a pre-existing,
    hand-assigned external_id is resolved to two distinct identifiers.
    """
    old_state = migrator.apply_initial_migration(MIGRATE_FROM)
    Taxonomy = old_state.apps.get_model("oel_tagging", "Taxonomy")
    Tag = old_state.apps.get_model("oel_tagging", "Tag")

    taxonomy = Taxonomy.objects.create(name="Migration Test", export_id="migration_test")

    plain_tag = Tag.objects.create(
        taxonomy=taxonomy, value="Plain Tag", external_id=None, depth=0, lineage="Plain Tag\t",
    )
    long_value = "L" * 300
    long_tag = Tag.objects.create(
        taxonomy=taxonomy, value=long_value, external_id=None, depth=0, lineage=long_value + "\t",
    )
    # An institution-assigned external_id that happens to match another tag's value exactly:
    institution_tag = Tag.objects.create(
        taxonomy=taxonomy, value="Institution Value", external_id="Colliding Value",
        depth=0, lineage="Institution Value\t",
    )
    colliding_tag = Tag.objects.create(
        taxonomy=taxonomy, value="Colliding Value", external_id=None, depth=0, lineage="Colliding Value\t",
    )
    # An empty string is also "no external_id": the field has always been blank=True,
    # and downstream data outside this repo's own code paths could hold "" instead of NULL.
    empty_string_tag = Tag.objects.create(
        taxonomy=taxonomy, value="Empty String Tag", external_id="", depth=0, lineage="Empty String Tag\t",
    )

    new_state = migrator.apply_tested_migration(MIGRATE_TO)
    NewTag = new_state.apps.get_model("oel_tagging", "Tag")

    new_plain_tag = NewTag.objects.get(pk=plain_tag.pk)
    new_long_tag = NewTag.objects.get(pk=long_tag.pk)
    new_institution_tag = NewTag.objects.get(pk=institution_tag.pk)
    new_colliding_tag = NewTag.objects.get(pk=colliding_tag.pk)
    new_empty_string_tag = NewTag.objects.get(pk=empty_string_tag.pk)

    assert new_plain_tag.external_id == "Plain Tag"
    assert new_empty_string_tag.external_id == "Empty String Tag"

    # Truncated, non-empty, and unique in its taxonomy:
    assert new_long_tag.external_id
    assert len(new_long_tag.external_id) <= 255

    # The institution-assigned value must not be overwritten by the collision...
    assert new_institution_tag.external_id == "Colliding Value"
    # ...and the colliding tag must get a different, generated identifier instead:
    assert new_colliding_tag.external_id is not None
    assert new_colliding_tag.external_id != new_institution_tag.external_id

    all_external_ids = list(
        NewTag.objects.filter(taxonomy_id=taxonomy.pk).values_list("external_id", flat=True)
    )
    assert None not in all_external_ids
    assert "" not in all_external_ids
    assert len(all_external_ids) == len({eid.casefold() for eid in all_external_ids})


@pytest.mark.django_db
def test_reverse_migration_is_a_noop_that_keeps_data(migrator) -> None:
    """
    Migrating back down only loosens the NOT NULL constraint; it doesn't delete the
    external_id values that were generated going forward.
    """
    old_state = migrator.apply_initial_migration(MIGRATE_FROM)
    Taxonomy = old_state.apps.get_model("oel_tagging", "Taxonomy")
    Tag = old_state.apps.get_model("oel_tagging", "Tag")

    taxonomy = Taxonomy.objects.create(name="Rollback Test", export_id="rollback_test")
    tag = Tag.objects.create(
        taxonomy=taxonomy, value="Rollback Tag", external_id=None, depth=0, lineage="Rollback Tag\t",
    )

    migrator.apply_tested_migration(MIGRATE_TO)
    reverted_state = migrator.apply_tested_migration(MIGRATE_FROM)
    RevertedTag = reverted_state.apps.get_model("oel_tagging", "Tag")

    reverted_tag = RevertedTag.objects.get(pk=tag.pk)
    assert reverted_tag.external_id == "Rollback Tag"


@pytest.mark.django_db
def test_archived_and_deletion_locked_default_to_false_for_existing_rows(migrator) -> None:
    """
    Rows that exist before 0023 come out with archived and deletion_locked False, and their
    other fields are untouched.
    """
    old_state = migrator.apply_initial_migration(MIGRATE_0023_FROM)
    Taxonomy = old_state.apps.get_model("oel_tagging", "Taxonomy")
    Tag = old_state.apps.get_model("oel_tagging", "Tag")
    ObjectTag = old_state.apps.get_model("oel_tagging", "ObjectTag")

    taxonomy = Taxonomy.objects.create(name="Flags Test", export_id="flags_test")
    tag = Tag.objects.create(
        taxonomy=taxonomy, value="Flags Tag", external_id="flags-tag", depth=0, lineage="Flags Tag\t",
    )
    # Historical models don't run ObjectTag.__init__, so the cached fields are set explicitly.
    object_tag = ObjectTag.objects.create(
        object_id="object:id:1", taxonomy=taxonomy, tag=tag, _export_id="flags_test", _value="Flags Tag",
    )

    new_state = migrator.apply_tested_migration(MIGRATE_0023_TO)
    NewTaxonomy = new_state.apps.get_model("oel_tagging", "Taxonomy")
    NewTag = new_state.apps.get_model("oel_tagging", "Tag")
    NewObjectTag = new_state.apps.get_model("oel_tagging", "ObjectTag")

    new_taxonomy = NewTaxonomy.objects.get(pk=taxonomy.pk)
    new_tag = NewTag.objects.get(pk=tag.pk)
    new_object_tag = NewObjectTag.objects.get(pk=object_tag.pk)

    assert new_taxonomy.archived is False
    assert new_tag.archived is False
    assert new_object_tag.archived is False
    assert new_object_tag.deletion_locked is False

    assert new_taxonomy.export_id == "flags_test"
    assert new_tag.value == "Flags Tag"
    assert new_tag.external_id == "flags-tag"
    assert new_object_tag.object_id == "object:id:1"
    assert new_object_tag.taxonomy_id == taxonomy.pk
    assert new_object_tag.tag_id == tag.pk
    assert new_object_tag._export_id == "flags_test"  # pylint: disable=protected-access
    assert new_object_tag._value == "Flags Tag"  # pylint: disable=protected-access
