"""
Test for import/export API
"""
import json
from io import BytesIO

from django.test.testcases import TestCase

import openedx_tagging.import_export.api as import_export_api
from openedx_tagging.import_export import ParserFormat
from openedx_tagging.models import Tag, TagImportTask, TagImportTaskState, Taxonomy

from .mixins import TestImportExportMixin


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

    def test_import_removing_no_external_id(self) -> None:
        new_taxonomy = Taxonomy(name="New taxonomy")
        new_taxonomy.save()
        tag1 = Tag.objects.create(
            id=1000,
            value="Tag 1",
            taxonomy=new_taxonomy,
        )
        tag2 = Tag.objects.create(
            id=1001,
            value="Tag 2",
            taxonomy=new_taxonomy,
        )
        tag3 = Tag.objects.create(
            id=1002,
            value="Tag 3",
            taxonomy=new_taxonomy,
        )
        tag1.save()
        tag2.save()
        tag3.save()
        # Import with empty tags, to remove all tags
        importFile = BytesIO(json.dumps({"tags": []}).encode())
        result, _tasks, _plan = import_export_api.import_tags(
            new_taxonomy,
            importFile,
            ParserFormat.JSON,
            replace=True,
        )
        assert result

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
        Same as `test_import_rename_external_id_then_export`, but through
        the .csv format: the follow-up export contains the new id, not the
        old id, and its header row has no `previous_id` column at all,
        since `previous_id` is import-only and never persisted (see ADR
        0010).
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
        The Studio taxonomy import wizard always runs with replace=True (a
        full replace), so a rename must be verified through that exact
        end-to-end path, not just at generate_actions() level (see
        test_import_plan.TestTagImportPlan.test_generate_actions_rename_external_id_replace_skips_delete
        for the plan-level check that the old id is excluded from the delete
        sweep).
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

    def test_import_rename_external_id_previous_id_equals_id_is_noop(self) -> None:
        """
        previous_id equal to id supports idempotent re-import.
        RenameTagExternalId.applies_for declines to fire in that case (see its
        unit-level coverage in test_actions.py), and normal update/no-op
        handling applies instead. This confirms the actual end-to-end
        scenario: re-importing a tag with previous_id set to its own current
        external_id succeeds with no error, and a follow-up export still shows
        the same id.
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
        Replace-mode import that omits tag_1 (so the delete sweep queues it
        for deletion) and, in the same file, renames tag_2 onto id="tag_1",
        reusing the external_id that tag_1's deletion is about to free up.
        This must succeed end-to-end: tag_1 being still physically present
        (but already queued for deletion) at validate time must not be
        treated as a real collision.

        The row's value ("Renamed From Tag 2") and parent_id ("tag_3") both
        genuinely differ from the doomed tag_1's current value ("Tag 1") and
        parent (None). This is deliberate: with matching values, RenameTag
        and UpdateParentTag's DB-only lookups (unaware that tag_1 is queued
        for deletion in this same import) would never fire in the first
        place, so the test would pass even without the fix that makes them
        skip a tag queued for deletion, and end up proving nothing about it.
        tag_3 gets its own no-op row so it survives as a valid parent target,
        instead of also being swept up by the same replace-mode delete.
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

    def test_import_swap_external_ids(self) -> None:
        """
        A 2-tag swap (tag_1 <-> tag_3 external_ids, both root tags with no
        parent, so parent handling doesn't complicate the assertions) has no
        valid plain execution order: renaming either tag onto the other's
        external_id first collides with a per-statement, non-deferred DB
        unique constraint on (taxonomy, external_id). Each tag must be
        staged through a placeholder id first (see ADR 0010 amendment).
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
        A contended external_id swap where each row ALSO tries to take on
        the other tag's current value: this must still cleanly reject, not
        raise an IntegrityError or otherwise crash. Value swaps are an
        explicit, documented limitation (see ADR 0010 amendment):
        (taxonomy, value) has the identical unique-constraint shape as
        (taxonomy, external_id), but staging is only implemented for
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
        Two rows in the same import cannot claim the same final id: a
        tag_1<->tag_3 swap plus an unrelated third row that also targets
        id=tag_1 is ambiguous, since the second row and the third row both
        claim tag_1 as their final id. This must be rejected outright at
        the plan step, not resolved by row order (previously: a silent
        overwrite of the row that landed second, or an uncaught crash,
        depending on which row came first in the file).
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
        Same collision as test_import_duplicate_final_id_rejected, but with
        the unrelated row moved to the front of the file: the rejection
        doesn't depend on row order. Previously, this exact ordering hit an
        uncaught Tag.DoesNotExist crash at execute time instead of a clean
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

    def test_import_rename_referencing_stale_old_id_rejected(self) -> None:
        """
        Regression: a plain (non-contended) rename of tag_1 to tag_50, with
        a different row's parent_id referencing tag_1's OLD id, must be
        rejected cleanly at the plan step -- not crash at execute time. The
        rename row comes first in the file, so if the stale reference were
        accepted, the second row's own execute() would raise an uncaught
        Tag.DoesNotExist once the rename runs before it, since no tag would
        hold external_id="tag_1" any more.
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
        assert "Unknown parent tag (tag_1)" in log
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
