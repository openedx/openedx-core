"""
Test for import/export API
"""
import csv
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

    @ddt.data(False, True)
    def test_import_swap_external_ids(self, reversed_rows: bool) -> None:
        """
        A 2-tag swap (tag_1 <-> tag_3, both root tags so parent handling
        doesn't complicate the assertions) has no valid plain execution
        order: either rename collides with the DB's per-statement unique
        constraint on (taxonomy, external_id). Each tag must be staged
        through a placeholder id first (see ADR 0010 amendment).
        """
        old_pk_1 = self.taxonomy.tag_set.get(external_id="tag_1").pk
        old_pk_3 = self.taxonomy.tag_set.get(external_id="tag_3").pk

        rows = [
            {"id": "tag_3", "value": "Tag 1", "previous_id": "tag_1"},
            {"id": "tag_1", "value": "Tag 3", "previous_id": "tag_3"},
        ]
        importFile = BytesIO(json.dumps({"tags": rows[::-1] if reversed_rows else rows}).encode())
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

    @ddt.data((0, 1, 2), (2, 1, 0), (1, 0, 2))
    def test_import_three_cycle_external_ids(self, order: tuple[int, ...]) -> None:
        """
        End-to-end 3-cycle: tag_1 -> tag_2 -> tag_3 -> tag_1. Same staging
        mechanism as a 2-tag swap, generalized to any cycle length.
        """
        old_pk_1 = self.taxonomy.tag_set.get(external_id="tag_1").pk
        old_pk_2 = self.taxonomy.tag_set.get(external_id="tag_2").pk
        old_pk_3 = self.taxonomy.tag_set.get(external_id="tag_3").pk

        rows = [
            {"id": "tag_2", "value": "Tag 1", "previous_id": "tag_1"},
            {"id": "tag_3", "value": "Tag 2", "previous_id": "tag_2"},
            {"id": "tag_1", "value": "Tag 3", "previous_id": "tag_3"},
        ]
        importFile = BytesIO(json.dumps({"tags": [rows[i] for i in order]}).encode())
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
    The #673 and #858 acceptance scenarios, run as the import wizard runs them: a full replace
    import checked through the log and the export. Rows are written as ``TagItem`` objects.
    """

    taxonomy: Taxonomy

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        cls.taxonomy = Taxonomy.objects.create(name="Competencies")

    @staticmethod
    def _orders(count: int) -> list[tuple[int, ...]]:
        """
        Return the file order, the reversed order and one mixed order of ``count`` rows.

        Both parsers hand the plan the same rows, so these orders run in JSON only.
        """
        forward = tuple(range(count))
        return list(dict.fromkeys([forward, forward[::-1], forward[1::2] + forward[0::2]]))

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

    def _import(self, rows: list[TagItem], parser_format: ParserFormat = ParserFormat.JSON, plan_only: bool = False):
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
        result, _log, _plan = self._import(rows)
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

    def _exported_rows(self) -> list[dict]:
        """
        Parse the JSON export and check it only carries the id, value and parent_id fields.
        """
        rows = json.loads(import_export_api.export_tags(self.taxonomy, ParserFormat.JSON))["tags"]
        fields = {key for row in rows for key in row}
        assert fields <= {"id", "value", "parent_id"}
        return [{"id": row["id"], "value": row["value"], "parent_id": row.get("parent_id") or None} for row in rows]

    def _exported_ids(self) -> dict[str, str]:
        """
        Return the exported id of each tag, by tag value.
        """
        return {row["value"]: row["id"] for row in self._exported_rows()}

    def _exported_parents(self) -> dict[str, str | None]:
        """
        Return the exported parent_id of each tag, by tag value.
        """
        return {row["value"]: row["parent_id"] for row in self._exported_rows()}

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
        *,
        messages: Sequence[str],
        error_count: int | None = None,
        parser_format: ParserFormat = ParserFormat.JSON,
    ) -> str:
        """
        Check the import is rejected, its log has each of ``messages``, and nothing changed.

        With ``error_count``, also check the plan has exactly that many errors.
        """
        snapshot = set(self.taxonomy.tag_set.values_list("pk", "external_id", "value", "parent_id"))
        before = import_export_api.export_tags(self.taxonomy, ParserFormat.JSON)
        result, log, plan = self._import(rows, parser_format)
        assert not result
        for message in messages:
            assert message in log
        if error_count is not None:
            assert plan is not None
            assert len(plan.errors) == error_count
        assert set(self.taxonomy.tag_set.values_list("pk", "external_id", "value", "parent_id")) == snapshot
        assert import_export_api.export_tags(self.taxonomy, ParserFormat.JSON) == before
        return log

    def _assert_tree_after_import(
        self,
        *,
        rows: list[TagItem],
        expected_parents: dict[str, str | None],
        pks: dict[str, int],
        static_rows: tuple[TagItem, ...] = (),
    ) -> None:
        """
        Import ``rows`` in each of ``_orders``, followed by ``static_rows``, and roll each import back.

        Check that every tag ends up under the parent in ``expected_parents`` (external_id to
        parent external_id) in the database and the export, and that each tag in ``pks``
        (external_id after the import to pk) keeps its pk.
        """
        for order in self._orders(len(rows)):
            with self.subTest(order=order), self._rolled_back():
                result, _log, _plan = self._import([rows[i] for i in order] + list(static_rows))
                assert result
                for tag_id, pk in pks.items():
                    assert Tag.objects.get(pk=pk).external_id == tag_id
                db_parents = {
                    tag.external_id: tag.parent.external_id if tag.parent else None
                    for tag in self.taxonomy.tag_set.select_related("parent")
                }
                assert db_parents == expected_parents
                exported = {row["id"]: row["parent_id"] for row in self._exported_rows()}
                assert exported == expected_parents

    def test_rename_shows_in_export_without_duplicate_or_previous_id(self) -> None:
        """
        A rename keeps the tag's pk and content associations, and the export lists the tag once,
        with its new id and no previous_id.
        """
        pks = self._seed([TagItem(id="COMP-A", value="Alpha")])
        object_tags = self._associate(pks, "COMP-A")
        result, _log, _plan = self._import([TagItem(id="COMP-B", value="Alpha", previous_id="COMP-A")])
        assert result
        assert [row["id"] for row in self._exported_rows()] == ["COMP-B"]
        assert self.taxonomy.tag_set.count() == 1
        assert Tag.objects.get(pk=pks["COMP-A"]).external_id == "COMP-B"
        self._assert_associations_kept(object_tags)

    def test_rename_onto_id_of_kept_tag_rejected(self) -> None:
        """
        A rename onto the id of a tag that the file keeps is rejected in either row order, whether
        the kept tag's row is plain or has a previous_id equal to its id.
        """
        self._seed([TagItem(id="COMP-Z", value="Xray"), TagItem(id="COMP-A", value="Yank")])
        rename = TagItem(id="COMP-Z", value="Yank", previous_id="COMP-A")
        keeps = {
            "plain": TagItem(id="COMP-Z", value="Xray"),
            "previous_id equal to id": TagItem(id="COMP-Z", value="Xray", previous_id="COMP-Z"),
        }
        for name, keep in keeps.items():
            for rows in ([rename, keep], [keep, rename]):
                with self.subTest(keep=name, first=rows[0].value):
                    self._assert_rejected(rows, messages=["COMP-Z"])

    def test_rename_chain_colliding_with_kept_tag_rejected_whole(self) -> None:
        """
        A chain of renames whose last id is held by a tag the file keeps is rejected as a whole,
        and none of the tags changes.
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
        for order in self._orders(len(rows)):
            with self.subTest(order=order):
                self._assert_rejected([rows[i] for i in order], messages=["COMP-C"])

    def test_child_names_new_id_of_renamed_parent(self) -> None:
        """
        A child that names its renamed parent's new id stays under that parent in either row
        order, and the export nests it under the new id.
        """
        pks = self._seed([TagItem(id="COMP-P", value="Parent"), TagItem(id="COMP-K", value="Kid", parent_id="COMP-P")])
        rows = [
            TagItem(id="COMP-P2", value="Parent", previous_id="COMP-P"),
            TagItem(id="COMP-K", value="Kid", parent_id="COMP-P2"),
        ]
        for order in self._orders(len(rows)):
            with self.subTest(order=order), self._rolled_back():
                result, _log, _plan = self._import([rows[i] for i in order])
                assert result
                assert Tag.objects.get(pk=pks["COMP-K"]).parent_id == pks["COMP-P"]
                assert self._exported_ids()["Parent"] == "COMP-P2"
                assert self._exported_parents()["Kid"] == "COMP-P2"

    def test_every_rejected_row_is_in_the_one_log(self) -> None:
        """
        Rejected rows of different kinds each have their message in the same log, because the
        import wizard shows the log as one block.
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
        self._assert_rejected(
            rows,
            messages=[
                "Row 2: Parent tag COMP-P is renamed to COMP-P2 in this file; use COMP-P2 as the parent_id.",
                "Row 3: Parent tag COMP-P is renamed to COMP-P2 in this file; use COMP-P2 as the parent_id.",
                "Unknown previous_id (COMP-GHOST). No tag with that external_id exists in this taxonomy.",
            ],
        )

    def test_child_of_deleted_holder_moves_under_tag_renamed_onto_its_id(self) -> None:
        """
        When the file deletes a tag and renames another tag onto its id, a child row that names
        the id ends up under the renamed tag, not at the root, in either row order.
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
        for order in self._orders(len(rows)):
            with self.subTest(order=order), self._rolled_back():
                result, _log, _plan = self._import([rows[i] for i in order])
                assert result
                assert not Tag.objects.filter(pk=pks["COMP-X"]).exists()
                assert Tag.objects.get(pk=pks["COMP-C"]).parent_id == pks["COMP-Y"]
                assert Tag.objects.get(pk=pks["COMP-Y"]).external_id == "COMP-X"
                self._assert_associations_kept(object_tags)
                assert self._exported_parents() == {"Yank": None, "Child": "COMP-X"}

    def test_kept_tags_under_deleted_ancestors_become_roots(self) -> None:
        """
        A kept tag whose parent the import deletes becomes a root with the same pk, also when
        that parent sits under a deleted ancestor whose delete would cascade to the kept tag.
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
            with self.subTest(case=name), self._rolled_back():
                pks = self._seed(before)
                result, _log, _plan = self._import(kept)
                assert result
                assert set(self.taxonomy.tag_set.values_list("pk", flat=True)) == {pks[t.id] for t in kept}
                assert all(tag.parent_id is None for tag in self.taxonomy.tag_set.all())
                assert self._exported_parents() == {t.value: None for t in kept}

    # deep_tag is at the fifth level, so a child of deep_tag is at the sixth, the deepest allowed.
    deep_chain = (
        TagItem(id="level_1", value="Level 1"),
        TagItem(id="level_2", value="Level 2", parent_id="level_1"),
        TagItem(id="level_3", value="Level 3", parent_id="level_2"),
        TagItem(id="level_4", value="Level 4", parent_id="level_3"),
    )
    deep_tag = TagItem(id="deep_tag", value="Deep tag", parent_id="level_4")

    def test_deep_tag_under_renamed_parent_moved_to_root(self) -> None:
        """
        The deepest tag of a chain moves to the root with two new levels under it while its old
        parent is renamed, and it keeps its pk.
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
        self._assert_tree_after_import(
            rows=rows, expected_parents=expected, pks=kept_pks, static_rows=self.deep_chain[:3],
        )

    def test_three_new_levels_under_moved_deep_tag(self) -> None:
        """
        Three new levels are chained under the deepest tag of a chain, which moves to the root.
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
        self._assert_tree_after_import(rows=rows, expected_parents=expected, pks=pks, static_rows=self.deep_chain)

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

    def test_swap_with_children_naming_final_ids(self) -> None:
        """
        Two parents swap ids while their children name the parents' final ids, and each child
        stays under the same parent tag.
        """
        pks = self._seed(list(self.swapped_parents))
        rows = [
            *self.swap,
            TagItem(id="COMP-AK", value="Alpha kid", parent_id="COMP-B"),
            TagItem(id="COMP-BK", value="Beta kid", parent_id="COMP-A"),
        ]
        moved_pks = {
            "COMP-B": pks["COMP-A"], "COMP-A": pks["COMP-B"], "COMP-AK": pks["COMP-AK"], "COMP-BK": pks["COMP-BK"],
        }
        self._assert_tree_after_import(
            rows=rows,
            expected_parents={"COMP-B": None, "COMP-A": None, "COMP-AK": "COMP-B", "COMP-BK": "COMP-A"},
            pks=moved_pks,
        )

    def test_swap_children_naming_stale_ids_rejected(self) -> None:
        """
        Children that name their parents' pre-import ids in a swap are rejected. Each error names
        the child's data row, which is the same in JSON and CSV, its parent's final id, and how
        to move under the tag that takes the id instead.
        """
        self._seed(list(self.swapped_parents))
        rows = [
            *self.swap,
            TagItem(id="COMP-AK", value="Alpha kid", parent_id="COMP-A"),
            TagItem(id="COMP-BK", value="Beta kid", parent_id="COMP-B"),
        ]
        final_parent_ids = {"COMP-AK": "COMP-B", "COMP-BK": "COMP-A"}
        orders = self._orders(len(rows))
        runs = [(ParserFormat.JSON, order) for order in orders] + [(ParserFormat.CSV, orders[-1])]
        for parser_format, order in runs:
            with self.subTest(parser_format=parser_format, order=order):
                file_rows = [rows[i] for i in order]
                self._assert_rejected(
                    file_rows,
                    messages=[
                        f"Row {position}: Parent tag {row.parent_id} is renamed to {final_parent_ids[row.id]} "
                        f"in this file; use {final_parent_ids[row.id]} as the parent_id. To move under the tag "
                        f"that takes the id {row.parent_id} in this file instead, import the id changes and "
                        "the move as two separate files."
                        for position, row in enumerate(file_rows, start=1)
                        if row.id in final_parent_ids
                    ],
                    error_count=2,
                    parser_format=parser_format,
                )

    def test_swap_child_naming_parent_id_in_another_case(self) -> None:
        """
        In a swap, a child naming its parent's pre-import id in another case is rejected once, like
        the exact spelling, and children naming the final ids in another case are accepted.
        """
        pks = self._seed(list(self.swapped_parents))
        stale = [
            *self.swap,
            TagItem(id="COMP-AK", value="Alpha kid", parent_id="comp-a"),
            TagItem(id="COMP-BK", value="Beta kid", parent_id="comp-a"),
        ]
        accepted = [
            *self.swap,
            TagItem(id="COMP-AK", value="Alpha kid", parent_id="comp-b"),
            TagItem(id="COMP-BK", value="Beta kid", parent_id="comp-a"),
        ]
        moved_pks = {
            "COMP-B": pks["COMP-A"], "COMP-A": pks["COMP-B"], "COMP-AK": pks["COMP-AK"], "COMP-BK": pks["COMP-BK"],
        }
        self._assert_tree_after_import(
            rows=accepted,
            expected_parents={"COMP-B": None, "COMP-A": None, "COMP-AK": "COMP-B", "COMP-BK": "COMP-A"},
            pks=moved_pks,
        )
        for order in self._orders(len(stale)):
            with self.subTest(order=order, stale=True):
                file_rows = [stale[i] for i in order]
                position = file_rows.index(stale[2]) + 1
                log = self._assert_rejected(
                    file_rows,
                    messages=[
                        f"Row {position}: Parent tag comp-a is renamed to COMP-B in this file; "
                        "use COMP-B as the parent_id. To move under the tag that takes the id comp-a "
                        "in this file instead, import the id changes and the move as two separate files.",
                    ],
                )
                assert log.count("is renamed to") == 1
