"""
Test for import/export API
"""
import json
from io import BytesIO

from django.test.testcases import TestCase

import openedx_tagging.import_export.api as import_export_api
from openedx_tagging.import_export import ParserFormat
from openedx_tagging.models import LanguageTaxonomy, Tag, TagImportTask, TagImportTaskState, Taxonomy

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
        self.system_taxonomy = Taxonomy(
            name="System taxonomy",
        )
        self.system_taxonomy.taxonomy_class = LanguageTaxonomy
        self.system_taxonomy = self.system_taxonomy.cast()
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

        # Check that import is invalid with system taxonomy
        with self.assertRaises(ValueError):
            import_export_api.import_tags(
                self.system_taxonomy,
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
