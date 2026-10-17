"""
Test for import/export API
"""
import csv
import itertools
import json
from collections.abc import Sequence
from contextlib import contextmanager
from io import BytesIO, StringIO

import ddt  # type: ignore[import]
from django.db import transaction
from django.test.testcases import TestCase

import openedx_tagging.import_export.api as import_export_api
from openedx_tagging.import_export import ParserFormat
from openedx_tagging.import_export.import_plan import TagItem
from openedx_tagging.models import ObjectTag, Tag, TagImportTask, TagImportTaskState, Taxonomy

from .mixins import TestImportExportMixin


@ddt.ddt
class TestImportExportApi(TestImportExportMixin, TestCase):
    """
    Test import/export API functions
    """

    def setUp(self) -> None:
        self.tags = [
            {"id": "tag_31", "value": "Tag 31"},
            {"id": "tag_32", "value": "Tag 32"},
            {"id": "tag_33", "value": "Tag 33", "parent_id": "tag_31"},
            {"id": "tag_1", "value": "Tag 1 V2"},
            {"id": "tag_4", "value": "Tag 4", "parent_id": "tag_32"},
        ]
        json_data = {"tags": self.tags}
        self.file = BytesIO(json.dumps(json_data).encode())

        json_data = {"invalid": [
            {"id": "tag_1", "name": "Tag 1"},
        ]}
        self.invalid_parser_file = BytesIO(json.dumps(json_data).encode())
        json_data = {"tags": [
            {'id': 'tag_31', 'value': 'Tag 31'},
            {'id': 'tag_31', 'value': 'Tag 32'},
        ]}
        self.invalid_plan_file = BytesIO(json.dumps(json_data).encode())

        self.parser_format = ParserFormat.JSON

        self.open_taxonomy = Taxonomy(
            name="Open taxonomy",
            allow_free_text=True
        )
        self.read_only_taxonomy = Taxonomy(
            name="Read-only taxonomy",
            read_only=True,
        )
        return super().setUp()

    def test_check_status(self) -> None:
        TagImportTask.create(self.taxonomy)
        status = import_export_api.get_last_import_status(self.taxonomy)
        assert status == TagImportTaskState.LOADING_DATA

    def test_check_log(self) -> None:
        TagImportTask.create(self.taxonomy)
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert "Import task created" in log

    def test_invalid_import_tags(self) -> None:
        TagImportTask.create(self.taxonomy)
        with self.assertRaises(ValueError):
            # Raise error if there is a current in progress task
            import_export_api.import_tags(
                self.taxonomy,
                self.file,
                self.parser_format,
            )

    def test_import_export_validations(self) -> None:
        # Check that import is invalid with open taxonomy
        with self.assertRaises(ValueError):
            import_export_api.import_tags(
                self.open_taxonomy,
                self.file,
                self.parser_format,
            )

        # Check that import is invalid with read-only taxonomy
        with self.assertRaises(ValueError):
            import_export_api.import_tags(
                self.read_only_taxonomy,
                self.file,
                self.parser_format,
            )

    def test_with_python_error(self) -> None:
        self.file.close()
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            self.file,
            self.parser_format,
        )
        assert not result
        status = import_export_api.get_last_import_status(self.taxonomy)
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert status == TagImportTaskState(task.status)
        assert status == TagImportTaskState.ERROR
        assert log == task.log
        assert "ValueError('I/O operation on closed file.')" in log

    def test_with_parser_error(self) -> None:
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            self.invalid_parser_file,
            self.parser_format,
        )
        assert not result
        status = import_export_api.get_last_import_status(self.taxonomy)
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert status == TagImportTaskState(task.status)
        assert status == TagImportTaskState.ERROR
        assert log == task.log
        assert "Starting to load data from file" in log
        assert "Invalid '.json' format" in log

    def test_with_plan_errors(self) -> None:
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            self.invalid_plan_file,
            self.parser_format,
        )
        assert not result
        status = import_export_api.get_last_import_status(self.taxonomy)
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert status == TagImportTaskState(task.status)
        assert status == TagImportTaskState.ERROR
        assert log == task.log
        assert "Starting to load data from file" in log
        assert "Load data finished" in log
        assert "Starting plan actions" in log
        assert "Plan finished" in log
        assert "Conflict with 'create'" in log

    def test_valid(self) -> None:
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            self.file,
            self.parser_format,
            replace=True,
        )
        assert result
        status = import_export_api.get_last_import_status(self.taxonomy)
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert status == TagImportTaskState(task.status)
        assert status == TagImportTaskState.SUCCESS
        assert log == task.log
        assert "Starting to load data from file" in log
        assert "Load data finished" in log
        assert "Starting plan actions" in log
        assert "Plan finished" in log
        assert "Starting execute actions" in log
        assert "Execution finished" in log

    def test_start_task_after_error(self) -> None:
        result, _task, _plan = import_export_api.import_tags(
            self.taxonomy,
            self.invalid_parser_file,
            self.parser_format,
        )
        assert not result
        result, _task, _plan = import_export_api.import_tags(
            self.taxonomy,
            self.file,
            self.parser_format,
        )
        assert result

    def test_start_task_after_success(self) -> None:
        result, _task, _plan = import_export_api.import_tags(
            self.taxonomy,
            self.file,
            self.parser_format,
        )
        assert result

        # Opening again the file
        json_data = {"tags": self.tags}
        self.file = BytesIO(json.dumps(json_data).encode())

        result, _task, _plan = import_export_api.import_tags(
            self.taxonomy,
            self.file,
            self.parser_format,
        )
        assert result

    def test_import_with_export_output(self) -> None:
        for parser_format in ParserFormat:
            output = import_export_api.export_tags(
                self.taxonomy,
                parser_format,
            )
            file = BytesIO(output.encode())
            new_taxonomy = Taxonomy(name="New taxonomy", export_id=f"new_taxonomy_{parser_format}")
            new_taxonomy.save()
            result, _task, _plan = import_export_api.import_tags(
                new_taxonomy,
                file,
                parser_format,
            )
            assert result
            old_tags = self.taxonomy.tag_set.all()
            assert len(old_tags) == new_taxonomy.tag_set.count()

            for tag in old_tags:
                new_tag = new_taxonomy.tag_set.get(external_id=tag.external_id)
                assert new_tag.value == tag.value
                if tag.parent:
                    assert new_tag.parent
                    assert tag.parent.external_id == new_tag.parent.external_id

    def test_reimport_after_generated_external_ids_is_a_no_op(self) -> None:
        """
        Export a taxonomy whose tags have auto-generated external_ids, then re-import
        the exact same file. The plan should report no changes at all.
        """
        self.taxonomy.add_tag("Generated Tag One")
        self.taxonomy.add_tag("Generated Tag Two", parent_tag_value="Generated Tag One")

        output = import_export_api.export_tags(self.taxonomy, self.parser_format)
        file = BytesIO(output.encode())

        result, _task, plan = import_export_api.import_tags(
            self.taxonomy,
            file,
            self.parser_format,
            replace=True,
            plan_only=True,
        )
        assert result
        assert plan is not None
        assert not plan.errors
        assert all(action.name == "without_changes" for action in plan.actions)

    def test_reimport_pre_upgrade_export_file_fails_safely(self) -> None:
        """
        A file exported before this ticket's upgrade has each tag's old integer database id
        in the `id` column instead of a real external_id. Re-importing it afterward (in ADD
        mode, not replace) must fail safely: validation errors identifying the rows it can't
        match, and no tag created, renamed, re-parented, or deleted.
        """
        tag = self.taxonomy.add_tag("Existing Tag")
        tag_count_before = self.taxonomy.tag_set.count()

        # Simulate a pre-upgrade export: `id` is an old integer PK, not `tag.external_id`.
        stale_pk = str(tag.pk)
        assert stale_pk != tag.external_id  # sanity check the simulation is realistic
        import_file = BytesIO(json.dumps({"tags": [{"id": stale_pk, "value": tag.value}]}).encode())

        result, _task, plan = import_export_api.import_tags(
            self.taxonomy,
            import_file,
            self.parser_format,
            replace=False,
            plan_only=True,
        )
        assert not result
        assert plan is not None
        assert plan.errors
        assert any("Duplicated tag value" in str(error) for error in plan.errors)
        assert self.taxonomy.tag_set.count() == tag_count_before
        # Confirm nothing was renamed either:
        tag.refresh_from_db()
        assert tag.value == "Existing Tag"

    def test_reimport_pre_upgrade_export_file_fails_safely_on_id_collision(self) -> None:
        """
        Same stale-database-id scenario as above, but the id also happens to equal a
        different tag's real, current external_id, so the row resolves to that other
        tag instead of failing to match anyone. The import still fails safely: that
        tag's value differs from the row's (Tag.value is unique per taxonomy, and the
        original tag still holds it), so a rename action also applies to it and its
        duplicate-value check rejects the whole plan.
        """
        tag = self.taxonomy.add_tag("Existing Tag")
        stale_pk = str(tag.pk)
        assert stale_pk != tag.external_id  # sanity check the simulation is realistic

        # A different, unrelated tag whose real external_id happens to equal that stale id.
        other_tag = self.taxonomy.add_tag("Unrelated Tag", external_id=stale_pk)
        tag_count_before = self.taxonomy.tag_set.count()

        import_file = BytesIO(json.dumps({"tags": [{"id": stale_pk, "value": tag.value}]}).encode())

        result, _task, plan = import_export_api.import_tags(
            self.taxonomy,
            import_file,
            self.parser_format,
            replace=False,
            plan_only=True,
        )
        assert not result
        assert plan is not None
        assert plan.errors
        assert any("Duplicated tag value" in str(error) for error in plan.errors)
        assert self.taxonomy.tag_set.count() == tag_count_before
        # Confirm the unrelated tag was not renamed or re-parented:
        other_tag.refresh_from_db()
        assert other_tag.value == "Unrelated Tag"
        assert other_tag.parent is None
        # And the original tag is also untouched:
        tag.refresh_from_db()
        assert tag.value == "Existing Tag"

    def test_import_removing_with_childs(self) -> None:
        """
        Test import need to remove childs with parents that will also be removed
        """
        new_taxonomy = Taxonomy(name="New taxonomy")
        new_taxonomy.save()
        level2 = Tag.objects.create(
            id=1000,
            external_id="tag_2",
            value="Tag 2",
            taxonomy=new_taxonomy,
        )
        level1 = Tag.objects.create(
            id=1001,
            external_id="tag_1",
            value="Tag 1",
            taxonomy=new_taxonomy,
        )
        level3 = Tag.objects.create(
            id=1002,
            external_id="tag_3",
            value="Tag 3",
            taxonomy=new_taxonomy,
        )
        level2.parent = level1
        level2.save()

        level3.parent = level3
        level3.save()

        # Import with empty tags, to remove all tags
        importFile = BytesIO(json.dumps({"tags": []}).encode())
        result, _tasks, _plan = import_export_api.import_tags(
            new_taxonomy,
            importFile,
            ParserFormat.JSON,
            replace=True,
        )
        assert result

    def test_import_removing_with_childs_no_external_id(self) -> None:
        """
        Test import need to remove childs with parents that will also be removed,
        using tags without external_id
        """
        new_taxonomy = Taxonomy(name="New taxonomy")
        new_taxonomy.save()
        level2 = Tag.objects.create(
            id=1000,
            value="Tag 2",
            taxonomy=new_taxonomy,
        )
        level1 = Tag.objects.create(
            id=1001,
            value="Tag 1",
            taxonomy=new_taxonomy,
        )
        level3 = Tag.objects.create(
            id=1002,
            value="Tag 3",
            taxonomy=new_taxonomy,
        )
        level2.parent = level1
        level2.save()

        level3.parent = level3
        level3.save()

        # Import with empty tags, to remove all tags
        importFile = BytesIO(json.dumps({"tags": []}).encode())

        result, _tasks, _plan = import_export_api.import_tags(
            new_taxonomy,
            importFile,
            ParserFormat.JSON,
            replace=True,
        )
        assert result

    def test_import_rename_external_id_preserves_pk(self) -> None:
        """
        Importing a row with a matching `previous_id` renames the tag's
        external_id in place, preserving its primary key (see ADR 0010).
        """
        old_pk = self.taxonomy.tag_set.get(external_id="tag_1").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_50", "value": "Tag 1 Renamed", "previous_id": "tag_1"},
        ]}).encode())
        result, _task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        assert result

        renamed_tag = Tag.objects.get(pk=old_pk)
        assert renamed_tag.external_id == "tag_50"
        assert renamed_tag.value == "Tag 1 Renamed"
        assert not self.taxonomy.tag_set.filter(external_id="tag_1").exists()

    def test_import_rename_external_id_then_export(self) -> None:
        """
        A follow-up export after a rename contains the new id, and neither
        the old id nor a `previous_id` field, since `previous_id` is
        import-only and never persisted (see ADR 0010).
        """
        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_50", "value": "Tag 1 Renamed", "previous_id": "tag_1"},
        ]}).encode())
        result, _task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        assert result

        output = import_export_api.export_tags(self.taxonomy, self.parser_format)
        exported_tags = json.loads(output).get("tags")
        exported_ids = [tag.get("id") for tag in exported_tags]
        assert "tag_50" in exported_ids
        assert "tag_1" not in exported_ids
        for tag in exported_tags:
            assert "previous_id" not in tag

    def test_import_rename_external_id_preserves_pk_csv(self) -> None:
        """
        Same as `test_import_rename_external_id_preserves_pk`, but through
        the .csv format (see ADR 0010).
        """
        old_pk = self.taxonomy.tag_set.get(external_id="tag_1").pk

        importFile = BytesIO("id,value,previous_id\ntag_50,Tag 1 Renamed,tag_1\n".encode())
        result, _task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            ParserFormat.CSV,
        )
        assert result

        renamed_tag = Tag.objects.get(pk=old_pk)
        assert renamed_tag.external_id == "tag_50"
        assert renamed_tag.value == "Tag 1 Renamed"
        assert not self.taxonomy.tag_set.filter(external_id="tag_1").exists()

    def test_import_rename_external_id_then_export_csv(self) -> None:
        """
        Same as `test_import_rename_external_id_then_export`, but via .csv:
        the export contains the new id, not the old one, and its header
        has no `previous_id` column, since it's import-only and never
        persisted (see ADR 0010).
        """
        importFile = BytesIO("id,value,previous_id\ntag_50,Tag 1 Renamed,tag_1\n".encode())
        result, _task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            ParserFormat.CSV,
        )
        assert result

        output = import_export_api.export_tags(self.taxonomy, ParserFormat.CSV)
        header = output.splitlines()[0].split(",")
        assert "previous_id" not in header

        exported_ids = [line.split(",")[0] for line in output.splitlines()[1:]]
        assert "tag_50" in exported_ids
        assert "tag_1" not in exported_ids

    def test_import_rename_external_id_survives_replace_mode(self) -> None:
        """
        Studio's taxonomy import wizard always runs with replace=True, so
        this must be verified end-to-end, not just at generate_actions()
        level (see
        test_import_plan.TestTagImportPlan.test_generate_actions_rename_external_id_replace_skips_delete
        for the plan-level check that the old id is excluded from the
        delete sweep).
        """
        old_pk = self.taxonomy.tag_set.get(external_id="tag_1").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_50", "value": "Tag 1 Renamed", "previous_id": "tag_1"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
            replace=True,
        )
        assert result
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log

        renamed_tag = Tag.objects.get(pk=old_pk)
        assert renamed_tag.external_id == "tag_50"
        assert renamed_tag.value == "Tag 1 Renamed"
        assert not self.taxonomy.tag_set.filter(external_id="tag_1").exists()

    def test_import_rename_external_id_survives_replace_mode_case_different_previous_id(self) -> None:
        """
        Regression: replace=True with a previous_id that differs from the
        stored external_id only in case ("TAG_1" vs "tag_1") must still
        protect the existing tag from the delete sweep end-to-end. Before
        the fix, the exact-case pop in generate_actions() missed this tag,
        the delete sweep queued and removed it, and the rename's own
        execute() then crashed with Tag.DoesNotExist since the pk it
        resolved at plan time no longer existed.
        """
        old_pk = self.taxonomy.tag_set.get(external_id="tag_1").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_50", "value": "Tag 1 Renamed", "previous_id": "TAG_1"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
            replace=True,
        )
        assert result
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log

        renamed_tag = Tag.objects.get(pk=old_pk)
        assert renamed_tag.external_id == "tag_50"
        assert renamed_tag.value == "Tag 1 Renamed"
        assert not self.taxonomy.tag_set.filter(external_id="tag_1").exists()

    def test_import_plain_row_replace_survives_case_different_id(self) -> None:
        """
        Regression: a plain row (no previous_id) with id="TAG_1" under
        replace=True must still protect the existing tag_1 tag from the
        delete sweep end-to-end, even though it differs from the stored
        external_id only in case. Before the fix, the exact-case
        `tag.id in tags_for_delete` check missed this tag, the delete
        sweep removed it, and tag_4's own update_parent action (an
        explicit re-parent onto "tag_1") then crashed looking up a tag
        that had already been deleted.
        """
        old_pk = self.taxonomy.tag_set.get(external_id="tag_1").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "TAG_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2", "parent_id": "tag_1"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4", "parent_id": "tag_1"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
            replace=True,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert result

        surviving_tag = Tag.objects.get(pk=old_pk)
        assert surviving_tag.external_id == "tag_1"
        assert surviving_tag.value == "Tag 1"

        reparented_tag = self.taxonomy.tag_set.get(external_id="tag_4")
        assert reparented_tag.parent is not None
        assert reparented_tag.parent.pk == old_pk

    def test_import_rename_external_id_previous_id_equals_id_is_noop(self) -> None:
        """
        previous_id equal to id is a no-op: RenameTagExternalId.applies_for
        declines to fire (see test_actions.py), and normal update handling
        applies instead. End-to-end: re-importing a tag with previous_id
        set to its own external_id succeeds, and export still shows the
        same id.
        """
        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_1", "value": "Tag 1", "previous_id": "tag_1"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        assert result
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log

        output = import_export_api.export_tags(self.taxonomy, self.parser_format)
        exported_tags = json.loads(output).get("tags")
        exported_ids = [tag.get("id") for tag in exported_tags]
        assert "tag_1" in exported_ids

    def test_import_rename_external_id_unmatched_previous_id_rejected(self) -> None:
        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_50", "value": "Tag 50", "previous_id": "tag_999"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        assert not result
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Unknown previous_id" in log
        assert not self.taxonomy.tag_set.filter(external_id="tag_50").exists()

    def test_import_rename_external_id_colliding_new_id_rejected(self) -> None:
        tag_before = self.taxonomy.tag_set.get(external_id="tag_1")
        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_2", "value": "Tag 1", "previous_id": "tag_1"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        assert not result
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "already exists" in log

        tag_after = self.taxonomy.tag_set.get(external_id="tag_1")
        assert tag_after.pk == tag_before.pk
        assert tag_after.value == tag_before.value

    def test_import_rename_external_id_duplicate_previous_id_rejected(self) -> None:
        """
        Two rows sharing the same previous_id both target the same old tag.
        This must be rejected cleanly at the plan step, not crash at execute
        time once the first rename has already renamed the old tag away.
        """
        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_50", "value": "Tag 50", "previous_id": "tag_1"},
            {"id": "tag_60", "value": "Tag 60", "previous_id": "tag_1"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        assert not result
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Duplicated previous_id" in log
        assert "Traceback" not in log

        tag_after = self.taxonomy.tag_set.get(external_id="tag_1")
        assert tag_after.external_id == "tag_1"
        assert not self.taxonomy.tag_set.filter(external_id="tag_50").exists()
        assert not self.taxonomy.tag_set.filter(external_id="tag_60").exists()

    def test_import_rename_external_id_reuses_id_freed_by_replace_delete(self) -> None:
        """
        Replace-mode import that omits tag_1 (queuing it for deletion) and
        renames tag_2 onto id="tag_1" in the same file, reusing the id
        tag_1's deletion is about to free. Must succeed end-to-end: tag_1
        still being physically present (though queued for deletion) at
        validate time must not count as a collision.

        The new value and parent_id deliberately differ from tag_1's
        current ones: with matching values, RenameTag/UpdateParentTag's
        DB-only lookups would fire instead and pass even without this fix,
        proving nothing. tag_3 gets its own no-op row so it survives as a
        valid parent target, rather than being swept up by the same delete.
        """
        old_tag_1_pk = self.taxonomy.tag_set.get(external_id="tag_1").pk
        old_tag_2_pk = self.taxonomy.tag_set.get(external_id="tag_2").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_1", "value": "Renamed From Tag 2", "previous_id": "tag_2", "parent_id": "tag_3"},
            {"id": "tag_3", "value": "Tag 3"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
            replace=True,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert "Duplicated tag value" not in log
        assert result

        # tag_1's old row was genuinely deleted, not merely renamed away.
        assert not Tag.objects.filter(pk=old_tag_1_pk).exists()

        # tag_2 is the same underlying row, now wearing tag_1's freed-up id,
        # with the row's OWN new value and parent, not tag_1's old ones:
        # proof that RenameTag/UpdateParentTag did not sneak in and mutate
        # the doomed tag_1 before it got deleted.
        renamed_tag = Tag.objects.get(pk=old_tag_2_pk)
        assert renamed_tag.external_id == "tag_1"
        assert renamed_tag.value == "Renamed From Tag 2"
        assert renamed_tag.parent is not None
        assert renamed_tag.parent.external_id == "tag_3"

    def test_import_rename_parent_and_child_both_renamed(self) -> None:
        """
        A parent tag and its child are both renamed in the same import,
        with the child's parent_id explicitly set to the parent's NEW id.
        Must apply cleanly end-to-end, with the child correctly re-parented
        under the renamed parent.
        """
        old_pk_1 = self.taxonomy.tag_set.get(external_id="tag_1").pk
        old_pk_2 = self.taxonomy.tag_set.get(external_id="tag_2").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_10", "value": "Tag 1 Renamed", "previous_id": "tag_1"},
            {"id": "tag_20", "value": "Tag 2 Renamed", "previous_id": "tag_2", "parent_id": "tag_10"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert result

        parent_tag = Tag.objects.get(pk=old_pk_1)
        child_tag = Tag.objects.get(pk=old_pk_2)
        assert parent_tag.external_id == "tag_10"
        assert child_tag.external_id == "tag_20"
        assert child_tag.parent is not None
        assert child_tag.parent.pk == parent_tag.pk

    def test_import_rename_parent_and_child_both_renamed_replace_mode(self) -> None:
        """
        Same scenario as test_import_rename_parent_and_child_both_renamed,
        but with replace=True: the delete sweep must not interfere with the
        parent/child rename, since both are protected by previous_id.
        """
        old_pk_1 = self.taxonomy.tag_set.get(external_id="tag_1").pk
        old_pk_2 = self.taxonomy.tag_set.get(external_id="tag_2").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_10", "value": "Tag 1 Renamed", "previous_id": "tag_1"},
            {"id": "tag_20", "value": "Tag 2 Renamed", "previous_id": "tag_2", "parent_id": "tag_10"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
            replace=True,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert result

        parent_tag = Tag.objects.get(pk=old_pk_1)
        child_tag = Tag.objects.get(pk=old_pk_2)
        assert parent_tag.external_id == "tag_10"
        assert child_tag.external_id == "tag_20"
        assert child_tag.parent is not None
        assert child_tag.parent.pk == parent_tag.pk

    def test_import_swap_external_ids(self) -> None:
        """
        A 2-tag swap (tag_1 <-> tag_3, both root tags so parent handling
        doesn't complicate the assertions) has no valid plain execution
        order: either rename collides with the DB's per-statement unique
        constraint on (taxonomy, external_id). Each tag must be staged
        through a placeholder id first (see ADR 0010 amendment).
        """
        old_pk_1 = self.taxonomy.tag_set.get(external_id="tag_1").pk
        old_pk_3 = self.taxonomy.tag_set.get(external_id="tag_3").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_3", "value": "Tag 1", "previous_id": "tag_1"},
            {"id": "tag_1", "value": "Tag 3", "previous_id": "tag_3"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert result

        # Both tags keep their original pks: this was a rename, not a
        # delete-and-recreate.
        tag_1 = Tag.objects.get(pk=old_pk_1)
        tag_3 = Tag.objects.get(pk=old_pk_3)
        assert tag_1.external_id == "tag_3"
        assert tag_1.value == "Tag 1"
        assert tag_3.external_id == "tag_1"
        assert tag_3.value == "Tag 3"

        output = import_export_api.export_tags(self.taxonomy, self.parser_format)
        exported_tags = json.loads(output).get("tags")
        for tag in exported_tags:
            assert not tag.get("id", "").startswith("oel-import-staging:")
        exported_by_value = {tag["value"]: tag["id"] for tag in exported_tags}
        assert exported_by_value["Tag 1"] == "tag_3"
        assert exported_by_value["Tag 3"] == "tag_1"

    def test_import_three_cycle_external_ids(self) -> None:
        """
        End-to-end 3-cycle: tag_1 -> tag_2 -> tag_3 -> tag_1. Same staging
        mechanism as a 2-tag swap, generalized to any cycle length.
        """
        old_pk_1 = self.taxonomy.tag_set.get(external_id="tag_1").pk
        old_pk_2 = self.taxonomy.tag_set.get(external_id="tag_2").pk
        old_pk_3 = self.taxonomy.tag_set.get(external_id="tag_3").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_2", "value": "Tag 1", "previous_id": "tag_1"},
            {"id": "tag_3", "value": "Tag 2", "previous_id": "tag_2"},
            {"id": "tag_1", "value": "Tag 3", "previous_id": "tag_3"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert result

        assert Tag.objects.get(pk=old_pk_1).external_id == "tag_2"
        assert Tag.objects.get(pk=old_pk_2).external_id == "tag_3"
        assert Tag.objects.get(pk=old_pk_3).external_id == "tag_1"

        output = import_export_api.export_tags(self.taxonomy, self.parser_format)
        exported_tags = json.loads(output).get("tags")
        for tag in exported_tags:
            assert not tag.get("id", "").startswith("oel-import-staging:")

    def test_import_swap_external_ids_with_colliding_values_rejected(self) -> None:
        """
        A contended external_id swap where each row also takes the other
        tag's value: must cleanly reject, not raise an IntegrityError.
        Value swaps are an explicit, documented limitation (see ADR 0010
        amendment): (taxonomy, value) has the same unique-constraint shape
        as (taxonomy, external_id), but staging is only implemented for
        external_id.
        """
        old_pk_1 = self.taxonomy.tag_set.get(external_id="tag_1").pk
        old_pk_3 = self.taxonomy.tag_set.get(external_id="tag_3").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_3", "value": "Tag 3", "previous_id": "tag_1"},
            {"id": "tag_1", "value": "Tag 1", "previous_id": "tag_3"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert "Duplicated tag value" in log
        assert not result

        # Nothing changed: neither tag's external_id or value moved.
        tag_1 = Tag.objects.get(pk=old_pk_1)
        tag_3 = Tag.objects.get(pk=old_pk_3)
        assert tag_1.external_id == "tag_1"
        assert tag_1.value == "Tag 1"
        assert tag_3.external_id == "tag_3"
        assert tag_3.value == "Tag 3"

    def test_import_duplicate_final_id_rejected(self) -> None:
        """
        Two rows can't claim the same final id: a tag_1<->tag_3 swap plus
        an unrelated third row also targeting id=tag_1 is ambiguous. Must
        reject outright at the plan step, not resolve by row order
        (previously: a silent overwrite or an uncaught crash, depending on
        which row came first).
        """
        old_pk_1 = self.taxonomy.tag_set.get(external_id="tag_1").pk
        old_pk_3 = self.taxonomy.tag_set.get(external_id="tag_3").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_3", "value": "Tag 1", "previous_id": "tag_1"},
            {"id": "tag_1", "value": "Tag 3", "previous_id": "tag_3"},
            {"id": "tag_1", "value": "Something Else Entirely"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert "Duplicate id" in log
        assert not result

        # Nothing changed: neither tag's external_id or value moved.
        tag_1 = Tag.objects.get(pk=old_pk_1)
        tag_3 = Tag.objects.get(pk=old_pk_3)
        assert tag_1.external_id == "tag_1"
        assert tag_1.value == "Tag 1"
        assert tag_3.external_id == "tag_3"
        assert tag_3.value == "Tag 3"

    def test_import_duplicate_final_id_rejected_regardless_of_order(self) -> None:
        """
        Same collision as test_import_duplicate_final_id_rejected, with
        the unrelated row moved to the front: rejection must not depend on
        row order. Previously this ordering hit an uncaught
        Tag.DoesNotExist crash at execute time instead of a clean
        plan-time rejection.
        """
        old_pk_1 = self.taxonomy.tag_set.get(external_id="tag_1").pk
        old_pk_3 = self.taxonomy.tag_set.get(external_id="tag_3").pk

        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_1", "value": "Something Else Entirely"},
            {"id": "tag_3", "value": "Tag 1", "previous_id": "tag_1"},
            {"id": "tag_1", "value": "Tag 3", "previous_id": "tag_3"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert "Duplicate id" in log
        assert not result

        tag_1 = Tag.objects.get(pk=old_pk_1)
        tag_3 = Tag.objects.get(pk=old_pk_3)
        assert tag_1.external_id == "tag_1"
        assert tag_1.value == "Tag 1"
        assert tag_3.external_id == "tag_3"
        assert tag_3.value == "Tag 3"

    def test_import_duplicate_final_id_rejected_case_insensitive(self) -> None:
        """
        Two rows claiming the same final id in different case ("tag_50" and
        "TAG_50") must be rejected as a duplicate: external_id is
        case-insensitive at the DB layer (NOCASE / utf8mb4_unicode_ci), so
        these collide even though the Python-side strings differ.
        """
        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_50", "value": "Tag 1 Renamed", "previous_id": "tag_1"},
            {"id": "TAG_50", "value": "Tag 3 Renamed", "previous_id": "tag_3"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert "Duplicate id" in log
        assert not result

        # Nothing changed: neither tag was renamed.
        tag_1 = self.taxonomy.tag_set.get(external_id="tag_1")
        tag_3 = self.taxonomy.tag_set.get(external_id="tag_3")
        assert tag_1.value == "Tag 1"
        assert tag_3.value == "Tag 3"

    def test_import_plain_row_targets_vacated_id_rejected(self) -> None:
        """
        A plain row (no previous_id) with id="tag_1", alongside a different
        row renaming "tag_1" away (previous_id="tag_1", id="tag_50"), must be
        rejected at the plan step: the plain row's id would otherwise resolve
        to the very tag the other row is renaming away from, not to a
        distinct tag that happens to share that id.
        """
        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_50", "value": "Tag 1", "previous_id": "tag_1"},
            {"id": "tag_1", "value": "Brand New"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert "is renamed away by row" in log
        assert not result

        tag_1 = self.taxonomy.tag_set.get(external_id="tag_1")
        assert tag_1.value == "Tag 1"
        assert not self.taxonomy.tag_set.filter(external_id="tag_50").exists()

    def test_import_plain_row_targets_vacated_id_rejected_regardless_of_order(self) -> None:
        """
        Same collision as test_import_plain_row_targets_vacated_id_rejected,
        with the plain row moved to the front of the file: rejection must
        not depend on row order.
        """
        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_1", "value": "Brand New"},
            {"id": "tag_50", "value": "Tag 1", "previous_id": "tag_1"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        assert "is renamed away by row" in log
        assert not result

        tag_1 = self.taxonomy.tag_set.get(external_id="tag_1")
        assert tag_1.value == "Tag 1"
        assert not self.taxonomy.tag_set.filter(external_id="tag_50").exists()

    def test_import_rename_referencing_stale_old_id_rejected(self) -> None:
        """
        Regression: a plain rename of tag_1 to tag_50, with a different
        row's parent_id referencing tag_1's OLD id, must be rejected at
        the plan step, not crash at execute time. The rename comes first
        in the file, so an accepted stale reference would hit an uncaught
        Tag.DoesNotExist once no tag holds "tag_1" any more.
        """
        importFile = BytesIO(json.dumps({"tags": [
            {"id": "tag_50", "value": "Tag 1", "previous_id": "tag_1"},
            {"id": "tag_60", "value": "Tag 60", "parent_id": "tag_1"},
        ]}).encode())
        result, task, _plan = import_export_api.import_tags(
            self.taxonomy,
            importFile,
            self.parser_format,
        )
        assert not result
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Parent tag tag_1 is renamed to tag_50 in this file; use tag_50 as the parent_id." in log
        assert "Traceback" not in log

        assert self.taxonomy.tag_set.filter(external_id="tag_1").exists()
        assert not self.taxonomy.tag_set.filter(external_id="tag_50").exists()
        assert not self.taxonomy.tag_set.filter(external_id="tag_60").exists()

    @ddt.data(ParserFormat.JSON, ParserFormat.CSV)
    def test_import_stale_parent_id_error_names_row(self, parser_format: ParserFormat) -> None:
        rows = [
            {"id": "tag_4", "value": "Tag 4", "parent_id": "tag_1", "previous_id": ""},
            {"id": "tag_2", "value": "Tag 2", "parent_id": "tag_1", "previous_id": ""},
            {"id": "tag_3", "value": "Tag 1", "parent_id": "", "previous_id": "tag_1"},
            {"id": "tag_1", "value": "Tag 3", "parent_id": "", "previous_id": "tag_3"},
        ]
        if parser_format == ParserFormat.JSON:
            content = json.dumps({"tags": [{key: value for key, value in row.items() if value} for row in rows]})
        else:
            content = "id,value,parent_id,previous_id\n" + "".join(
                f"{row['id']},{row['value']},{row['parent_id']},{row['previous_id']}\n" for row in rows
            )
        result, _task, _plan = import_export_api.import_tags(self.taxonomy, BytesIO(content.encode()), parser_format)
        assert not result
        log = import_export_api.get_last_import_log(self.taxonomy)
        # The staging and detach actions and the row that renames onto tag_1 are built before
        # the tag_2 row, so its action is #6 while the error still names its file row.
        assert (
            "Action error in 'update_parent' (#6): "
            "Row 2: Parent tag tag_1 is renamed to tag_3 in this file; use tag_3 as the parent_id."
        ) in log

    def test_import_same_value_without_external_id(self) -> None:
        new_taxonomy = Taxonomy(name="New taxonomy")
        new_taxonomy.save()

        # Tag with no external_id
        Tag.objects.create(
            value="same_value",
            taxonomy=new_taxonomy,
        )

        # Import with one tag with the same value
        importFile = BytesIO(json.dumps({"tags": [{"id": "imported_tag", "value": "same_value"}]}).encode())

        result, _tasks, _plan = import_export_api.import_tags(
            new_taxonomy,
            importFile,
            ParserFormat.JSON,
            replace=True,
        )
        assert result


class TestImportAcceptanceScenarios(TestCase):
    """
    The acceptance scenarios of #673 and #858, run the way the Studio import wizard runs an
    import: a full replace import of a CSV or JSON file, checked through the import log and
    the exported file.

    Rows are written as ``TagItem`` objects, whose fields are the file's columns. Some #673
    scenarios are covered by tests in ``TestImportExportApi`` and are not repeated here:

    - "re-import is idempotent (previous_id equals the new id)":
      ``test_import_rename_external_id_previous_id_equals_id_is_noop``.
    - "previous_id is present but matches no existing tag (likely typo)":
      ``test_import_rename_external_id_unmatched_previous_id_rejected``.
    - "a rename claims an id the same import frees by removing its holder":
      ``test_import_rename_external_id_reuses_id_freed_by_replace_delete``. The case with
      a child row naming that id is here, in both row orders.
    - "a parent and its child are both renamed in one import":
      ``test_import_rename_parent_and_child_both_renamed``.
    """

    formats = (ParserFormat.JSON, ParserFormat.CSV)
    taxonomy: Taxonomy

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        cls.taxonomy = Taxonomy.objects.create(name="Competencies")

    def _render(self, rows: list[TagItem], parser_format: ParserFormat) -> BytesIO:
        """
        Render ``rows`` as an import file of the given format, leaving out unset fields.
        """
        file_rows = [
            {
                key: value
                for key, value in (
                    ("id", row.id),
                    ("value", row.value),
                    ("parent_id", row.parent_id),
                    ("previous_id", row.previous_id),
                )
                if value
            }
            for row in rows
        ]
        if parser_format == ParserFormat.JSON:
            return BytesIO(json.dumps({"tags": file_rows}).encode())
        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["id", "value", "parent_id", "previous_id"])
        writer.writeheader()
        writer.writerows(file_rows)
        return BytesIO(buffer.getvalue().encode())

    def _import(self, rows: list[TagItem], parser_format: ParserFormat, plan_only: bool = False):
        """
        Run a full replace import and return (result, log, plan).
        """
        result, task, plan = import_export_api.import_tags(
            self.taxonomy,
            self._render(rows, parser_format),
            parser_format,
            replace=True,
            plan_only=plan_only,
        )
        log = import_export_api.get_last_import_log(self.taxonomy)
        assert log == task.log
        assert "Traceback" not in log
        return result, log, plan

    def _seed(self, rows: list[TagItem]) -> dict[str, int]:
        """
        Create the starting tags and return their pks by external_id.
        """
        result, _log, _plan = self._import(rows, ParserFormat.JSON)
        assert result
        return {tag.external_id: tag.pk for tag in self.taxonomy.tag_set.all()}

    def _associate(self, pks: dict[str, int], *external_ids: str) -> dict[int, int]:
        """
        Tag a content object with each tag and return the ObjectTag pks by tag pk.
        """
        object_tags = {}
        for external_id in external_ids:
            tag = Tag.objects.get(pk=pks[external_id])
            object_tag = ObjectTag.objects.create(
                object_id=f"object-{tag.pk}", taxonomy=self.taxonomy, tag=tag, _value=tag.value,
            )
            object_tags[tag.pk] = object_tag.pk
        return object_tags

    def _assert_associations_kept(self, object_tags: dict[int, int]) -> None:
        """
        Check each ObjectTag still points to the same tag, not a recreated one.
        """
        for tag_pk, object_tag_pk in object_tags.items():
            assert ObjectTag.objects.get(pk=object_tag_pk).tag_id == tag_pk

    def _export(self, parser_format: ParserFormat) -> str:
        """
        Export the taxonomy in the given format.
        """
        return import_export_api.export_tags(self.taxonomy, parser_format)

    def _exported_rows(self, parser_format: ParserFormat) -> list[dict]:
        """
        Parse the export and check it only carries the id, value and parent_id fields.
        """
        output = self._export(parser_format)
        if parser_format == ParserFormat.JSON:
            rows = json.loads(output)["tags"]
            fields = {key for row in rows for key in row}
        else:
            reader = csv.DictReader(StringIO(output))
            assert reader.fieldnames == ["id", "value", "parent_id"]
            rows = list(reader)
            fields = set(reader.fieldnames)
        assert fields <= {"id", "value", "parent_id"}
        return [{"id": row["id"], "value": row["value"], "parent_id": row.get("parent_id") or None} for row in rows]

    def _exported_ids(self, parser_format: ParserFormat) -> dict[str, str]:
        """
        Return the exported id of each tag, by tag value.
        """
        return {row["value"]: row["id"] for row in self._exported_rows(parser_format)}

    def _exported_parents(self, parser_format: ParserFormat) -> dict[str, str | None]:
        """
        Return the exported parent_id of each tag, by tag value.
        """
        return {row["value"]: row["parent_id"] for row in self._exported_rows(parser_format)}

    @contextmanager
    def _rolled_back(self):
        """
        Run a block in a savepoint that is rolled back, so a scenario can be repeated.
        """
        with transaction.atomic():
            yield
            transaction.set_rollback(True)

    def _assert_rejected(
        self,
        rows: list[TagItem],
        parser_format: ParserFormat,
        *,
        messages: Sequence[str],
        error_count: int | None = None,
    ) -> str:
        """
        Check the import is rejected, its log has each of ``messages``, and nothing changed.

        With ``error_count``, also check the plan has exactly that many errors.
        """
        snapshot = set(self.taxonomy.tag_set.values_list("pk", "external_id", "value", "parent_id"))
        before = self._export(parser_format)
        result, log, plan = self._import(rows, parser_format)
        assert not result
        for message in messages:
            assert message in log
        if error_count is not None:
            assert plan is not None
            assert len(plan.errors) == error_count
        assert set(self.taxonomy.tag_set.values_list("pk", "external_id", "value", "parent_id")) == snapshot
        assert self._export(parser_format) == before
        return log

    def test_rename_shows_in_export_without_duplicate_or_previous_id(self) -> None:
        """
        #673 scenarios "rename an external_id, verified via CSV export", "rename an
        external_id, verified via JSON export" and "renaming an external_id preserves the
        competency's content associations": the export shows the tag once, with its new id,
        no row for the old id and no previous_id field, and the tag keeps its pk and its
        content associations.

        Before      After
        COMP-A      COMP-B (same tag)
        """
        pks = self._seed([TagItem(id="COMP-A", value="Alpha")])
        object_tags = self._associate(pks, "COMP-A")
        for parser_format in self.formats:
            with self.subTest(parser_format=parser_format), self._rolled_back():
                count = len(self._exported_rows(parser_format))
                result, _log, _plan = self._import(
                    [TagItem(id="COMP-B", value="Alpha", previous_id="COMP-A")], parser_format,
                )
                assert result
                assert [row["id"] for row in self._exported_rows(parser_format)] == ["COMP-B"]
                assert len(self._exported_rows(parser_format)) == count
                assert self.taxonomy.tag_set.count() == 1
                assert Tag.objects.get(pk=pks["COMP-A"]).external_id == "COMP-B"
                self._assert_associations_kept(object_tags)

    def test_new_id_without_previous_id_is_created(self) -> None:
        """
        #673 scenario "no previous_id, new id (create behavior preserved)": a row with a new
        id and no previous_id creates a tag that shows in the export.

        Before      After
        COMP-A      COMP-A
                    COMP-Z
        """
        self._seed([TagItem(id="COMP-A", value="Alpha")])
        for parser_format in self.formats:
            with self.subTest(parser_format=parser_format), self._rolled_back():
                result, _log, _plan = self._import(
                    [TagItem(id="COMP-A", value="Alpha"), TagItem(id="COMP-Z", value="Zulu")], parser_format,
                )
                assert result
                assert self._exported_ids(parser_format) == {"Alpha": "COMP-A", "Zulu": "COMP-Z"}

    def test_rename_onto_id_of_kept_tag_rejected(self) -> None:
        """
        #673 scenarios "new id collides with a different tag's external_id in the same
        taxonomy" and "a rename onto an id whose holder the same import keeps is rejected":
        the rename is rejected in either row order, whether the file keeps the holder as a
        plain row or as a row whose previous_id equals its id.

        Before      File rows
        COMP-Z      COMP-Z (previous_id COMP-A)
        COMP-A      COMP-Z (the holder, kept)
        """
        self._seed([TagItem(id="COMP-Z", value="Xray"), TagItem(id="COMP-A", value="Yank")])
        rename = TagItem(id="COMP-Z", value="Yank", previous_id="COMP-A")
        keeps = {
            "plain": TagItem(id="COMP-Z", value="Xray"),
            "previous_id equal to id": TagItem(id="COMP-Z", value="Xray", previous_id="COMP-Z"),
        }
        for parser_format in self.formats:
            for name, keep in keeps.items():
                for rows in ([rename, keep], [keep, rename]):
                    with self.subTest(parser_format=parser_format, keep=name, first=rows[0].value):
                        self._assert_rejected(rows, parser_format, messages=["COMP-Z"])

    def test_swap_keeps_tags_and_associations(self) -> None:
        """
        #673 scenario "two competencies swap external_ids in one import": the swap succeeds
        in either row order, creates and deletes nothing, keeps both tags' pks and content
        associations, and the export shows the swapped ids.

        Before      After
        COMP-A      COMP-B (was COMP-A)
        COMP-B      COMP-A (was COMP-B)
        """
        pks = self._seed([TagItem(id="COMP-A", value="Alpha"), TagItem(id="COMP-B", value="Beta")])
        object_tags = self._associate(pks, "COMP-A", "COMP-B")
        rows = [
            TagItem(id="COMP-B", value="Alpha", previous_id="COMP-A"),
            TagItem(id="COMP-A", value="Beta", previous_id="COMP-B"),
        ]
        for parser_format in self.formats:
            for order in itertools.permutations(range(2)):
                with self.subTest(parser_format=parser_format, order=order), self._rolled_back():
                    result, _log, _plan = self._import([rows[i] for i in order], parser_format)
                    assert result
                    assert self.taxonomy.tag_set.count() == 2
                    assert Tag.objects.get(pk=pks["COMP-A"]).external_id == "COMP-B"
                    assert Tag.objects.get(pk=pks["COMP-B"]).external_id == "COMP-A"
                    self._assert_associations_kept(object_tags)
                    assert self._exported_ids(parser_format) == {"Alpha": "COMP-B", "Beta": "COMP-A"}

    def test_three_way_rotation_in_any_row_order(self) -> None:
        """
        #673 scenario "three competencies rotate external_ids in one import": the rotation
        succeeds in all six row orders, and each tag holds the id its own row specified.

        Before      After
        COMP-A      COMP-B (was COMP-A)
        COMP-B      COMP-C (was COMP-B)
        COMP-C      COMP-A (was COMP-C)
        """
        pks = self._seed([
            TagItem(id="COMP-A", value="Alpha"),
            TagItem(id="COMP-B", value="Beta"),
            TagItem(id="COMP-C", value="Gamma"),
        ])
        rows = [
            TagItem(id="COMP-B", value="Alpha", previous_id="COMP-A"),
            TagItem(id="COMP-C", value="Beta", previous_id="COMP-B"),
            TagItem(id="COMP-A", value="Gamma", previous_id="COMP-C"),
        ]
        for parser_format in self.formats:
            for order in itertools.permutations(range(3)):
                with self.subTest(parser_format=parser_format, order=order), self._rolled_back():
                    result, _log, _plan = self._import([rows[i] for i in order], parser_format)
                    assert result
                    assert Tag.objects.get(pk=pks["COMP-A"]).external_id == "COMP-B"
                    assert Tag.objects.get(pk=pks["COMP-B"]).external_id == "COMP-C"
                    assert Tag.objects.get(pk=pks["COMP-C"]).external_id == "COMP-A"
                    assert self._exported_ids(parser_format) == {
                        "Alpha": "COMP-B", "Beta": "COMP-C", "Gamma": "COMP-A",
                    }

    def test_rename_chain_colliding_with_kept_tag_rejected_whole(self) -> None:
        """
        #673 scenario "a rename chain that collides with a competency the file keeps is
        rejected whole": the import is rejected in all six row orders, with the id in the log
        and all three tags unchanged.

        Before      File rows
        COMP-A      COMP-B (previous_id COMP-A)
        COMP-B      COMP-C (previous_id COMP-B)
        COMP-C      COMP-C (kept)
        """
        self._seed([
            TagItem(id="COMP-A", value="Alpha"),
            TagItem(id="COMP-B", value="Beta"),
            TagItem(id="COMP-C", value="Gamma"),
        ])
        rows = [
            TagItem(id="COMP-B", value="Alpha", previous_id="COMP-A"),
            TagItem(id="COMP-C", value="Beta", previous_id="COMP-B"),
            TagItem(id="COMP-C", value="Gamma"),
        ]
        for parser_format in self.formats:
            for order in itertools.permutations(range(3)):
                with self.subTest(parser_format=parser_format, order=order):
                    self._assert_rejected([rows[i] for i in order], parser_format, messages=["COMP-C"])

    def test_child_names_new_id_of_renamed_parent(self) -> None:
        """
        #673 scenario "a child row references its renamed parent's new external_id": the
        import succeeds in either row order, the child stays under the same parent tag, and
        the export shows it nested under the new id.

        Before          After
        COMP-P          COMP-P2 (was COMP-P)
            COMP-K          COMP-K
        """
        pks = self._seed([TagItem(id="COMP-P", value="Parent"), TagItem(id="COMP-K", value="Kid", parent_id="COMP-P")])
        rows = [
            TagItem(id="COMP-P2", value="Parent", previous_id="COMP-P"),
            TagItem(id="COMP-K", value="Kid", parent_id="COMP-P2"),
        ]
        for parser_format in self.formats:
            for order in itertools.permutations(range(2)):
                with self.subTest(parser_format=parser_format, order=order), self._rolled_back():
                    result, _log, _plan = self._import([rows[i] for i in order], parser_format)
                    assert result
                    assert Tag.objects.get(pk=pks["COMP-K"]).parent_id == pks["COMP-P"]
                    assert self._exported_ids(parser_format)["Parent"] == "COMP-P2"
                    assert self._exported_parents(parser_format)["Kid"] == "COMP-P2"

    def test_export_has_no_previous_id_and_round_trips_without_changes(self) -> None:
        """
        #673 scenario "export does not carry previous_id (round-trip safety), CSV and JSON":
        the export only has id, value and parent_id, and importing it back unchanged plans no
        change.

        Before and after
        COMP-P
            COMP-K
        COMP-A
        """
        self._seed([
            TagItem(id="COMP-P", value="Parent"),
            TagItem(id="COMP-K", value="Kid", parent_id="COMP-P"),
            TagItem(id="COMP-A", value="Alpha"),
        ])
        for parser_format in self.formats:
            with self.subTest(parser_format=parser_format):
                exported = self._exported_rows(parser_format)
                assert len(exported) == 3
                result, _log, plan = self._import(
                    [TagItem(id=row["id"], value=row["value"], parent_id=row["parent_id"]) for row in exported],
                    parser_format,
                    plan_only=True,
                )
                assert result
                assert plan is not None
                assert {action.name for action in plan.actions} == {"without_changes"}

    def test_every_rejected_row_is_in_the_one_log(self) -> None:
        """
        Several rejected rows of different kinds each have their message in the same log,
        because the wizard shows the log as one block (#673). The row numbers count data
        rows, so they are the same in JSON and CSV.

        Before          File rows
        COMP-P          1: COMP-P2 (previous_id COMP-P)
            COMP-K1     2: COMP-K1 (parent_id COMP-P, stale)
            COMP-K2     3: COMP-K2 (parent_id COMP-P, stale)
        COMP-A          4: COMP-B (previous_id COMP-GHOST, unmatched)
        """
        self._seed([
            TagItem(id="COMP-P", value="Parent"),
            TagItem(id="COMP-K1", value="Kid 1", parent_id="COMP-P"),
            TagItem(id="COMP-K2", value="Kid 2", parent_id="COMP-P"),
            TagItem(id="COMP-A", value="Alpha"),
        ])
        rows = [
            TagItem(id="COMP-P2", value="Parent", previous_id="COMP-P"),
            TagItem(id="COMP-K1", value="Kid 1", parent_id="COMP-P"),
            TagItem(id="COMP-K2", value="Kid 2", parent_id="COMP-P"),
            TagItem(id="COMP-B", value="Alpha", previous_id="COMP-GHOST"),
        ]
        for parser_format in self.formats:
            with self.subTest(parser_format=parser_format):
                self._assert_rejected(
                    rows,
                    parser_format,
                    messages=[
                        "Row 2: Parent tag COMP-P is renamed to COMP-P2 in this file; use COMP-P2 as the parent_id.",
                        "Row 3: Parent tag COMP-P is renamed to COMP-P2 in this file; use COMP-P2 as the parent_id.",
                        "COMP-GHOST",
                    ],
                )

    def test_rename_onto_freed_id_forming_parent_cycle_rejected(self) -> None:
        """
        A rename onto an id that the same import frees can close a parent cycle through the
        renamed tag. Both cases below are rejected at the Plan step with exactly one error,
        the cycle error on the cycle's first row, in either row order.

        Self parent:
        Before      File rows
        COMP-X      COMP-X (previous_id COMP-Y, parent_id COMP-X)
        COMP-Y

        Two renames naming each other as parent:
        Before      File rows
        COMP-A      COMP-A (previous_id COMP-C, parent_id COMP-B)
        COMP-B      COMP-B (previous_id COMP-D, parent_id COMP-A)
        COMP-C
        COMP-D
        """
        self._seed([TagItem(id="COMP-X", value="Xray"), TagItem(id="COMP-Y", value="Yank")])
        self_parent = [TagItem(id="COMP-X", value="Yank", parent_id="COMP-X", previous_id="COMP-Y")]
        for parser_format in self.formats:
            with self.subTest(parser_format=parser_format, case="self parent"):
                self._assert_rejected(
                    self_parent,
                    parser_format,
                    messages=["Row 1's parent_id (COMP-X) creates a parent cycle."],
                    error_count=1,
                )
        self._seed([
            TagItem(id="COMP-A", value="Alpha"), TagItem(id="COMP-B", value="Beta"),
            TagItem(id="COMP-C", value="Gamma"), TagItem(id="COMP-D", value="Delta"),
        ])
        rows = [
            TagItem(id="COMP-A", value="Gamma", parent_id="COMP-B", previous_id="COMP-C"),
            TagItem(id="COMP-B", value="Delta", parent_id="COMP-A", previous_id="COMP-D"),
        ]
        for parser_format in self.formats:
            for order in itertools.permutations(range(2)):
                with self.subTest(parser_format=parser_format, order=order, case="two renames"):
                    file_rows = [rows[i] for i in order]
                    self._assert_rejected(
                        file_rows,
                        parser_format,
                        messages=[f"Row 1's parent_id ({file_rows[0].parent_id}) creates a parent cycle."],
                        error_count=1,
                    )

    def test_child_of_deleted_holder_moves_under_tag_renamed_onto_its_id(self) -> None:
        """
        The file omits COMP-X, so COMP-X is deleted, and renames COMP-Y onto the id COMP-X.
        The child of the deleted tag names COMP-X, so it ends up under the renamed tag, not
        as a root, in either row order.

        Before          After
        COMP-X          COMP-X (was COMP-Y)
            COMP-C          COMP-C
        COMP-Y
        """
        pks = self._seed([
            TagItem(id="COMP-X", value="Xray"),
            TagItem(id="COMP-Y", value="Yank"),
            TagItem(id="COMP-C", value="Child", parent_id="COMP-X"),
        ])
        object_tags = self._associate(pks, "COMP-C")
        rows = [
            TagItem(id="COMP-X", value="Yank", previous_id="COMP-Y"),
            TagItem(id="COMP-C", value="Child", parent_id="COMP-X"),
        ]
        for parser_format in self.formats:
            for order in itertools.permutations(range(2)):
                with self.subTest(parser_format=parser_format, order=order), self._rolled_back():
                    result, _log, _plan = self._import([rows[i] for i in order], parser_format)
                    assert result
                    assert not Tag.objects.filter(pk=pks["COMP-X"]).exists()
                    assert Tag.objects.get(pk=pks["COMP-C"]).parent_id == pks["COMP-Y"]
                    assert Tag.objects.get(pk=pks["COMP-Y"]).external_id == "COMP-X"
                    self._assert_associations_kept(object_tags)
                    assert self._exported_parents(parser_format) == {"Yank": None, "Child": "COMP-X"}

    def test_kept_tags_under_deleted_ancestors_become_roots(self) -> None:
        """
        A kept tag whose parent the import deletes becomes a root, also when that parent sits
        under a deleted ancestor, whose delete would otherwise cascade to the kept tag.

        Before (three cases)                                After
        deleted_root > deleted_child > kept_tag             kept_tag
        deleted_1 > deleted_2 > kept_upper > deleted_3      kept_upper
            > kept_lower                                    kept_lower
        deleted_root > kept_child                           kept_child
        """
        cases = {
            "grandchild of a deleted chain": (
                [
                    TagItem(id="deleted_root", value="Deleted root"),
                    TagItem(id="deleted_child", value="Deleted child", parent_id="deleted_root"),
                    TagItem(id="kept_tag", value="Kept tag", parent_id="deleted_child"),
                ],
                ["kept_tag"],
            ),
            "kept tags at two depths": (
                [
                    TagItem(id="deleted_1", value="Deleted 1"),
                    TagItem(id="deleted_2", value="Deleted 2", parent_id="deleted_1"),
                    TagItem(id="kept_upper", value="Kept upper", parent_id="deleted_2"),
                    TagItem(id="deleted_3", value="Deleted 3", parent_id="kept_upper"),
                    TagItem(id="kept_lower", value="Kept lower", parent_id="deleted_3"),
                ],
                ["kept_upper", "kept_lower"],
            ),
            "child of a deleted root": (
                [
                    TagItem(id="deleted_root", value="Deleted root"),
                    TagItem(id="kept_child", value="Kept child", parent_id="deleted_root"),
                ],
                ["kept_child"],
            ),
        }
        for name, (before, kept_ids) in cases.items():
            # The file lists only the kept tags, without a parent.
            kept = [TagItem(id=tag.id, value=tag.value) for tag in before if tag.id in kept_ids]
            for parser_format in self.formats:
                with self.subTest(case=name, parser_format=parser_format), self._rolled_back():
                    pks = self._seed(before)
                    result, _log, _plan = self._import(kept, parser_format)
                    assert result
                    assert set(self.taxonomy.tag_set.values_list("pk", flat=True)) == {pks[t.id] for t in kept}
                    assert all(tag.parent_id is None for tag in self.taxonomy.tag_set.all())
                    assert self._exported_parents(parser_format) == {t.value: None for t in kept}

    def _assert_tree_after_import(
        self,
        *,
        rows: list[TagItem],
        expected_parents: dict[str, str | None],
        pks: dict[str, int],
        orders: Sequence[tuple[int, ...]],
        parser_formats: tuple[ParserFormat, ...],
        static_rows: tuple[TagItem, ...] = (),
    ) -> None:
        """
        Import ``rows`` in each of ``orders`` and each of ``parser_formats``, followed by
        ``static_rows`` (the other tags that the replace import has to keep). Check that every
        tag in ``expected_parents`` (external_id to parent external_id) ends up under that
        parent in the database and the export, and that each tag in ``pks`` (external_id
        after the import to pk) keeps its pk. Each import is rolled back.
        """
        for parser_format in parser_formats:
            for order in orders:
                with self.subTest(parser_format=parser_format, order=order), self._rolled_back():
                    result, _log, _plan = self._import([rows[i] for i in order] + list(static_rows), parser_format)
                    assert result
                    for tag_id, pk in pks.items():
                        assert Tag.objects.get(pk=pk).external_id == tag_id
                    db_parents = {
                        tag.external_id: tag.parent.external_id if tag.parent else None
                        for tag in self.taxonomy.tag_set.select_related("parent")
                    }
                    assert db_parents == expected_parents
                    exported = {row["id"]: row["parent_id"] for row in self._exported_rows(parser_format)}
                    assert exported == expected_parents

    def test_ancestry_inversion_in_any_row_order(self) -> None:
        """
        A child of old_root becomes old_root's ancestor, with a new tag between them. That
        only works if new_root leaves old_root's subtree before old_root moves under it.

        Before              After
        old_root            new_root
            sibling             new_middle
            new_root                old_root
                                    sibling
        """
        pks = self._seed([
            TagItem(id="old_root", value="Old root"),
            TagItem(id="sibling", value="Sibling", parent_id="old_root"),
            TagItem(id="new_root", value="New root", parent_id="old_root"),
        ])
        rows = [
            TagItem(id="old_root", value="Old root", parent_id="new_middle"),
            TagItem(id="sibling", value="Sibling", parent_id="new_middle"),
            TagItem(id="new_root", value="New root"),
            TagItem(id="new_middle", value="New middle", parent_id="new_root"),
        ]
        expected = {"old_root": "new_middle", "sibling": "new_middle", "new_root": None, "new_middle": "new_root"}
        orders = list(itertools.permutations(range(4)))
        # All 24 row orders in JSON. The CSV parser yields the same rows in the same order, so
        # 4 orders in CSV are enough and keep the test fast.
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=pks, orders=orders, parser_formats=(ParserFormat.JSON,),
        )
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=pks, orders=orders[::6], parser_formats=(ParserFormat.CSV,),
        )

    def test_parent_and_child_swap_places(self) -> None:
        """
        A parent and its child swap places, in either row order.

        Before              After
        old_parent          old_child
            old_child           old_parent
        """
        pks = self._seed([
            TagItem(id="old_parent", value="Old parent"),
            TagItem(id="old_child", value="Old child", parent_id="old_parent"),
        ])
        rows = [
            TagItem(id="old_parent", value="Old parent", parent_id="old_child"),
            TagItem(id="old_child", value="Old child"),
        ]
        self._assert_tree_after_import(
            rows=rows,
            expected_parents={"old_parent": "old_child", "old_child": None},
            pks=pks,
            orders=list(itertools.permutations(range(2))),
            parser_formats=self.formats,
        )

    def test_three_level_chain_reversed(self) -> None:
        """
        A three-level chain is turned upside down, in all six row orders.

        Before              After
        top                 bottom
            middle              middle
                bottom              top
        """
        pks = self._seed([
            TagItem(id="top", value="Top"),
            TagItem(id="middle", value="Middle", parent_id="top"),
            TagItem(id="bottom", value="Bottom", parent_id="middle"),
        ])
        rows = [
            TagItem(id="bottom", value="Bottom"),
            TagItem(id="middle", value="Middle", parent_id="bottom"),
            TagItem(id="top", value="Top", parent_id="middle"),
        ]
        self._assert_tree_after_import(
            rows=rows,
            expected_parents={"bottom": None, "middle": "bottom", "top": "middle"},
            pks=pks,
            orders=list(itertools.permutations(range(3))),
            parser_formats=self.formats,
        )

    def test_tag_moved_under_its_former_grandchild(self) -> None:
        """
        A root is moved under its former grandchild, which stays under middle, now a root,
        in all six row orders.

        Before              After
        top                 middle
            middle              bottom
                bottom              top
        """
        pks = self._seed([
            TagItem(id="top", value="Top"),
            TagItem(id="middle", value="Middle", parent_id="top"),
            TagItem(id="bottom", value="Bottom", parent_id="middle"),
        ])
        rows = [
            TagItem(id="top", value="Top", parent_id="bottom"),
            TagItem(id="middle", value="Middle"),
            TagItem(id="bottom", value="Bottom", parent_id="middle"),
        ]
        self._assert_tree_after_import(
            rows=rows,
            expected_parents={"top": "bottom", "middle": None, "bottom": "middle"},
            pks=pks,
            orders=list(itertools.permutations(range(3))),
            parser_formats=self.formats,
        )

    # The chain level_1 > level_2 > level_3 > level_4 > deep_tag: deep_tag is at the fifth
    # level, so a child of deep_tag is at the sixth level, the deepest the taxonomy allows.
    deep_chain = (
        TagItem(id="level_1", value="Level 1"),
        TagItem(id="level_2", value="Level 2", parent_id="level_1"),
        TagItem(id="level_3", value="Level 3", parent_id="level_2"),
        TagItem(id="level_4", value="Level 4", parent_id="level_3"),
    )
    deep_tag = TagItem(id="deep_tag", value="Deep tag", parent_id="level_4")

    def test_deep_tag_moved_to_root_with_new_children_in_any_row_order(self) -> None:
        """
        The deepest tag of a chain moves to the root while new tags are put under it.
        Creating new_grandchild before deep_tag moves would put it at a seventh level, so the
        import moves deep_tag to the root first.

        Before                          After
        level_1 > ... > level_4         level_1 > ... > level_4
            deep_tag                    deep_tag
                                            new_child
                                                new_grandchild
        """
        pks = self._seed([*self.deep_chain, self.deep_tag])
        rows = [
            TagItem(id="new_grandchild", value="New grandchild", parent_id="new_child"),
            TagItem(id="new_child", value="New child", parent_id="deep_tag"),
            TagItem(id="deep_tag", value="Deep tag"),
        ]
        expected = {
            "level_1": None, "level_2": "level_1", "level_3": "level_2", "level_4": "level_3",
            "deep_tag": None, "new_child": "deep_tag", "new_grandchild": "new_child",
        }
        orders = list(itertools.permutations(range(3)))
        # All 6 row orders in JSON. The CSV parser yields the same rows in the same order, so
        # 3 orders in CSV are enough and keep the test fast.
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=pks, orders=orders,
            parser_formats=(ParserFormat.JSON,), static_rows=self.deep_chain,
        )
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=pks, orders=orders[::2],
            parser_formats=(ParserFormat.CSV,), static_rows=self.deep_chain,
        )

    def test_deep_tag_under_renamed_parent_moved_to_root_in_any_row_order(self) -> None:
        """
        The same move as above while deep_tag's old parent is renamed in the same import.
        deep_tag keeps its pk.

        Before                          After
        level_1 > level_2 > level_3     level_1 > level_2 > level_3
            level_4                         renamed_level_4 (was level_4)
                deep_tag                deep_tag
                                            new_child
                                                new_grandchild
        """
        pks = self._seed([*self.deep_chain, self.deep_tag])
        rows = [
            TagItem(id="new_grandchild", value="New grandchild", parent_id="new_child"),
            TagItem(id="new_child", value="New child", parent_id="deep_tag"),
            TagItem(id="deep_tag", value="Deep tag"),
            TagItem(id="renamed_level_4", value="Level 4", parent_id="level_3", previous_id="level_4"),
        ]
        expected = {
            "level_1": None, "level_2": "level_1", "level_3": "level_2", "renamed_level_4": "level_3",
            "deep_tag": None, "new_child": "deep_tag", "new_grandchild": "new_child",
        }
        kept_pks = {tag_id: pk for tag_id, pk in pks.items() if tag_id != "level_4"} | {
            "renamed_level_4": pks["level_4"],
        }
        # All 24 row orders, in JSON only: the test above already runs this kind of move
        # through the CSV parser.
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=kept_pks, orders=list(itertools.permutations(range(4))),
            parser_formats=(ParserFormat.JSON,), static_rows=self.deep_chain[:3],
        )

    def test_subtree_moved_under_deep_tag_before_it_is_shallowed(self) -> None:
        """
        A three-level subtree moves under moved_up, which itself moves to the root. Attaching
        subtree_root while moved_up is still at the fourth level would put
        subtree_grandchild at a seventh level.

        Before                          After
        chain_1 > chain_2 > chain_3     chain_1 > chain_2 > chain_3
            moved_up                    moved_up
        subtree_root                        subtree_root
            subtree_child                       subtree_child
                subtree_grandchild                  subtree_grandchild
        """
        chain = (
            TagItem(id="chain_1", value="Chain 1"),
            TagItem(id="chain_2", value="Chain 2", parent_id="chain_1"),
            TagItem(id="chain_3", value="Chain 3", parent_id="chain_2"),
        )
        pks = self._seed([
            *chain,
            TagItem(id="moved_up", value="Moved up", parent_id="chain_3"),
            TagItem(id="subtree_root", value="Subtree root"),
            TagItem(id="subtree_child", value="Subtree child", parent_id="subtree_root"),
            TagItem(id="subtree_grandchild", value="Subtree grandchild", parent_id="subtree_child"),
        ])
        rows = [
            TagItem(id="subtree_root", value="Subtree root", parent_id="moved_up"),
            TagItem(id="moved_up", value="Moved up"),
            TagItem(id="subtree_child", value="Subtree child", parent_id="subtree_root"),
            TagItem(id="subtree_grandchild", value="Subtree grandchild", parent_id="subtree_child"),
        ]
        expected = {
            "chain_1": None, "chain_2": "chain_1", "chain_3": "chain_2", "moved_up": None,
            "subtree_root": "moved_up", "subtree_child": "subtree_root", "subtree_grandchild": "subtree_child",
        }
        orders = list(itertools.permutations(range(4)))
        # All 24 row orders in JSON. The CSV parser yields the same rows in the same order, so
        # 4 orders in CSV are enough and keep the test fast.
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=pks, orders=orders,
            parser_formats=(ParserFormat.JSON,), static_rows=chain,
        )
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=pks, orders=orders[::6],
            parser_formats=(ParserFormat.CSV,), static_rows=chain,
        )

    def test_three_new_levels_under_moved_deep_tag(self) -> None:
        """
        Three new tags are chained under deep_tag, which moves to the root.

        Before                          After
        level_1 > ... > level_4         level_1 > ... > level_4
            deep_tag                    deep_tag
                                            new_child
                                                new_grandchild
                                                    new_great_grandchild
        """
        pks = self._seed([*self.deep_chain, self.deep_tag])
        rows = [
            TagItem(id="deep_tag", value="Deep tag"),
            TagItem(id="new_child", value="New child", parent_id="deep_tag"),
            TagItem(id="new_grandchild", value="New grandchild", parent_id="new_child"),
            TagItem(id="new_great_grandchild", value="New great-grandchild", parent_id="new_grandchild"),
        ]
        expected = {
            "level_1": None, "level_2": "level_1", "level_3": "level_2", "level_4": "level_3", "deep_tag": None,
            "new_child": "deep_tag", "new_grandchild": "new_child", "new_great_grandchild": "new_grandchild",
        }
        # All 24 row orders, in JSON only: the moves of a deep tag above already run through
        # the CSV parser.
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=pks, orders=list(itertools.permutations(range(4))),
            parser_formats=(ParserFormat.JSON,), static_rows=self.deep_chain,
        )

    def test_import_that_leaves_tags_too_deep_is_rejected(self) -> None:
        """
        An import that would put a tag at the seventh level cannot be applied in any order,
        so it is rejected at the Plan step with that one error, and nothing changes.

        Child of the deepest tag:
        Before                          File rows
        level_1 > ... > level_6         the same six tags, and new_leaf (parent_id level_6)

        Chain moved under another:
        Before                          File rows
        deep_1 > ... > deep_4           the same eight tags, with moved_1 (parent_id deep_4)
        moved_1 > ... > moved_4
        """
        def chain(prefix: str, length: int) -> list[TagItem]:
            return [
                TagItem(id=f"{prefix}_{i}", value=f"{prefix} {i}", parent_id=f"{prefix}_{i - 1}" if i > 1 else None)
                for i in range(1, length + 1)
            ]

        six_levels = chain("level", 6)
        deep, moved = chain("deep", 4), chain("moved", 4)
        cases = {
            "child of the deepest tag": (
                six_levels, six_levels + [TagItem(id="new_leaf", value="New leaf", parent_id="level_6")], "new_leaf",
            ),
            "chain moved under another": (
                deep + moved,
                deep + [TagItem(id="moved_1", value="moved 1", parent_id="deep_4")] + moved[1:],
                "moved_3",
            ),
        }
        for name, (before, rows, too_deep_tag) in cases.items():
            message = f"This import would leave tag {too_deep_tag} more than 6 levels deep."
            self._seed(before)
            for parser_format in self.formats:
                with self.subTest(case=name, parser_format=parser_format):
                    log = self._assert_rejected(rows, parser_format, messages=[message], error_count=1)
                    assert log.count(message) == 1
            self.taxonomy.tag_set.all().delete()

    swapped_parents = (
        TagItem(id="COMP-A", value="Alpha"),
        TagItem(id="COMP-AK", value="Alpha kid", parent_id="COMP-A"),
        TagItem(id="COMP-B", value="Beta"),
        TagItem(id="COMP-BK", value="Beta kid", parent_id="COMP-B"),
    )
    swap = (
        TagItem(id="COMP-B", value="Alpha", previous_id="COMP-A"),
        TagItem(id="COMP-A", value="Beta", previous_id="COMP-B"),
    )

    def test_swap_with_children_in_any_row_order(self) -> None:
        """
        #858: two parents swap ids while their children name the parents' final ids. The
        import gives the same result in every row order, and each child stays under the
        same parent tag.

        Before              After
        COMP-A              COMP-B (was COMP-A)
            COMP-AK             COMP-AK (parent_id COMP-B)
        COMP-B              COMP-A (was COMP-B)
            COMP-BK             COMP-BK (parent_id COMP-A)
        """
        pks = self._seed(list(self.swapped_parents))
        rows = [
            *self.swap,
            TagItem(id="COMP-AK", value="Alpha kid", parent_id="COMP-B"),
            TagItem(id="COMP-BK", value="Beta kid", parent_id="COMP-A"),
        ]
        expected = {"COMP-B": None, "COMP-A": None, "COMP-AK": "COMP-B", "COMP-BK": "COMP-A"}
        moved_pks = {
            "COMP-B": pks["COMP-A"], "COMP-A": pks["COMP-B"], "COMP-AK": pks["COMP-AK"], "COMP-BK": pks["COMP-BK"],
        }
        orders = list(itertools.permutations(range(4)))
        # All 24 row orders in JSON. The CSV parser yields the same rows in the same order, so
        # 4 orders in CSV are enough and keep the test fast.
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=moved_pks, orders=orders, parser_formats=(ParserFormat.JSON,),
        )
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=moved_pks, orders=orders[::6], parser_formats=(ParserFormat.CSV,),
        )

    def test_swap_children_naming_stale_ids_rejected_in_any_row_order(self) -> None:
        """
        #858: children that name their parents' pre-import ids in a swap are rejected in
        every row order. Each error names the child's row and its parent's final id, and
        says how to move under the tag that takes the id instead. Nothing changes.

        Before              File rows
        COMP-A              COMP-B (previous_id COMP-A)
            COMP-AK         COMP-A (previous_id COMP-B)
        COMP-B              COMP-AK (parent_id COMP-A)
            COMP-BK         COMP-BK (parent_id COMP-B)
        """
        self._seed(list(self.swapped_parents))
        rows = [
            *self.swap,
            TagItem(id="COMP-AK", value="Alpha kid", parent_id="COMP-A"),
            TagItem(id="COMP-BK", value="Beta kid", parent_id="COMP-B"),
        ]
        final_parent_ids = {"COMP-AK": "COMP-B", "COMP-BK": "COMP-A"}
        orders = list(itertools.permutations(range(4)))
        # All 24 row orders in JSON, because each error must name the row's own number. The
        # CSV parser yields the same rows in the same order, so 4 orders in CSV are enough.
        for parser_format, format_orders in ((ParserFormat.JSON, orders), (ParserFormat.CSV, orders[::6])):
            for order in format_orders:
                with self.subTest(parser_format=parser_format, order=order):
                    file_rows = [rows[i] for i in order]
                    self._assert_rejected(
                        file_rows,
                        parser_format,
                        messages=[
                            f"Row {position}: Parent tag {row.parent_id} is renamed to {final_parent_ids[row.id]} "
                            f"in this file; use {final_parent_ids[row.id]} as the parent_id. To move under the tag "
                            f"that takes the id {row.parent_id} in this file instead, import the id changes and "
                            "the move as two separate files."
                            for position, row in enumerate(file_rows, start=1)
                            if row.id in final_parent_ids
                        ],
                        error_count=2,
                    )

    def test_swap_child_naming_parent_id_in_another_case(self) -> None:
        """
        Ids are case-insensitive. In a swap, a child naming its parent's stale id in another
        case is rejected once, with its row number and the parent's new id, like the exact
        spelling. Children naming the final ids in another case are accepted and stay
        under their parents.

        Before              After (accepted file)
        COMP-A              COMP-B (was COMP-A)
            COMP-AK             COMP-AK (parent_id comp-b)
        COMP-B              COMP-A (was COMP-B)
            COMP-BK             COMP-BK (parent_id comp-a)

        In the rejected file, COMP-AK says parent_id comp-a, the stale id of its parent.
        """
        pks = self._seed(list(self.swapped_parents))
        swap = list(self.swap)
        stale = swap + [
            TagItem(id="COMP-AK", value="Alpha kid", parent_id="comp-a"),
            TagItem(id="COMP-BK", value="Beta kid", parent_id="comp-a"),
        ]
        accepted = swap + [
            TagItem(id="COMP-AK", value="Alpha kid", parent_id="comp-b"),
            TagItem(id="COMP-BK", value="Beta kid", parent_id="comp-a"),
        ]
        expected = {"COMP-B": None, "COMP-A": None, "COMP-AK": "COMP-B", "COMP-BK": "COMP-A"}
        moved_pks = {
            "COMP-B": pks["COMP-A"], "COMP-A": pks["COMP-B"], "COMP-AK": pks["COMP-AK"], "COMP-BK": pks["COMP-BK"],
        }
        # A sample of 4 row orders (renames first, children first, reversed, mixed): the
        # tests of the exact spelling above already run all 24 orders.
        orders = [(0, 1, 2, 3), (2, 3, 0, 1), (3, 2, 1, 0), (2, 0, 3, 1)]
        self._assert_tree_after_import(
            rows=accepted, expected_parents=expected, pks=moved_pks, orders=orders, parser_formats=self.formats,
        )
        for parser_format in self.formats:
            for order in orders:
                with self.subTest(parser_format=parser_format, order=order, stale=True):
                    file_rows = [stale[i] for i in order]
                    position = file_rows.index(stale[2]) + 1
                    log = self._assert_rejected(
                        file_rows,
                        parser_format,
                        messages=[
                            f"Row {position}: Parent tag comp-a is renamed to COMP-B in this file; "
                            "use COMP-B as the parent_id. To move under the tag that takes the id comp-a "
                            "in this file instead, import the id changes and the move as two separate files.",
                        ],
                    )
                    assert log.count("is renamed to") == 1

    def test_plain_rename_child_naming_parent_id_in_another_case_reported_once(self) -> None:
        """
        When the parent is renamed without a swap, a child naming the parent's old id in
        another case gets only the parent validation error, in either row order and format.

        Before          File rows
        COMP-P          COMP-P2 (previous_id COMP-P)
            COMP-K      COMP-K (parent_id comp-p)
        """
        self._seed([TagItem(id="COMP-P", value="Parent"), TagItem(id="COMP-K", value="Kid", parent_id="COMP-P")])
        rows = [
            TagItem(id="COMP-P2", value="Parent", previous_id="COMP-P"),
            TagItem(id="COMP-K", value="Kid", parent_id="comp-p"),
        ]
        for parser_format in self.formats:
            for order in itertools.permutations(range(2)):
                with self.subTest(parser_format=parser_format, order=order):
                    file_rows = [rows[i] for i in order]
                    log = self._assert_rejected(
                        file_rows,
                        parser_format,
                        messages=[
                            f"Row {file_rows.index(rows[1]) + 1}: Parent tag comp-p is renamed to COMP-P2 "
                            "in this file; use COMP-P2 as the parent_id.",
                        ],
                        error_count=1,
                    )
                    assert "To move under" not in log
