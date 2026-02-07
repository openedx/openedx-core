"""
Test for import/export parsers
"""
from __future__ import annotations

import json
from io import BytesIO

import ddt  # type: ignore[import]
from django.test.testcases import TestCase

from openedx_tagging.import_export.exceptions import TagParserError
from openedx_tagging.import_export.parsers import CSVParser, JSONParser, Parser, ParserFormat, get_parser
from openedx_tagging.models import Taxonomy

from .mixins import TestImportExportMixin


class TestParser(TestCase):
    """
    Test for general parser functions
    """

    def test_get_parser(self) -> None:
        for parser_format in ParserFormat:
            parser = get_parser(parser_format)
            self.assertEqual(parser.format, parser_format)

    def test_parser_not_found(self) -> None:
        with self.assertRaises(ValueError):
            get_parser(None)  # type: ignore[arg-type]

    def test_not_implemented(self) -> None:
        taxonomy = Taxonomy(name="Test taxonomy")
        taxonomy.save()
        with self.assertRaises(NotImplementedError):
            Parser.parse_import(BytesIO())
        with self.assertRaises(NotImplementedError):
            Parser.export(taxonomy)

    def test_tag_parser_error(self) -> None:
        tag = {"id": 'tag_id', "value": "tag_value"}
        expected_str = f"Import parser error on {tag}"
        expected_repr = f"TagParserError(Import parser error on {tag})"
        error = TagParserError(tag)
        assert str(error) == expected_str
        assert repr(error) == expected_repr


@ddt.ddt
class TestJSONParser(TestImportExportMixin, TestCase):
    """
    Test for .json parser
    """

    def test_invalid_json(self) -> None:
        json_data = "{This is an invalid json}"
        json_file = BytesIO(json_data.encode())
        tags, errors = JSONParser.parse_import(json_file)
        assert len(tags) == 0
        assert len(errors) == 1
        assert "Expecting property name enclosed in double quotes" in str(errors[0])

    def test_load_data_errors(self) -> None:
        json_data = {"invalid": [
            {"id": "tag_1", "name": "Tag 1"},
        ]}

        json_file = BytesIO(json.dumps(json_data).encode())

        tags, errors = JSONParser.parse_import(json_file)
        self.assertEqual(len(tags), 0)
        self.assertEqual(len(errors), 1)
        self.assertEqual(
            str(errors[0]),
            "Invalid '.json' format: Missing 'tags' field on the .json file"
        )

    @ddt.data(
        (
            {"tags": [
                {"id": "tag_1", "value": "Tag 1", "comment": "This field is ignored."},  # Valid
            ]},
            []
        ),
        (
            {"tags": [
                {"id": "tag_1", "value": "Tag 1"},  # Valid
            ]},
            []
        ),
        (
            {"tags": [
                {"id": "tag_1"},
                {"value": "tag_1"},
                {},
            ]},
            [
                "Missing 'value' field on {'id': 'tag_1'}",
                "Missing 'id' field on {'value': 'tag_1'}",
                "Missing 'value' field on {}",
                "Missing 'id' field on {}",
            ]
        ),
        (
            {"tags": [
                {"id": "", "value": "tag 1"},
                {"id": "tag_2", "value": ""},
                {"id": "tag_3", "value": "tag 3", "parent_id": ""},  # Valid
            ]},
            [
                "Empty 'id' field on {'id': '', 'value': 'tag 1'}",
                "Empty 'value' field on {'id': 'tag_2', 'value': ''}",
            ]
        )
    )
    @ddt.unpack
    def test_parse_tags_errors(self, json_data: dict, expected_errors: list[str]):
        json_file = BytesIO(json.dumps(json_data).encode())

        _, errors = JSONParser.parse_import(json_file)
        self.assertEqual(len(errors), len(expected_errors))

        for error in errors:
            self.assertIn(str(error), expected_errors)

    def test_parse_tags(self) -> None:
        expected_tags = [
            {"id": "tag_1", "value": "tag 1"},
            {"id": "tag_2", "value": "tag 2"},
            {"id": "tag_3", "value": "tag 3", "parent_id": "tag_1"},
            {"id": "tag_4", "value": "tag 4"},
        ]
        json_data = {"tags": expected_tags}

        json_file = BytesIO(json.dumps(json_data).encode())

        tags, errors = JSONParser.parse_import(json_file)
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(tags), 4)

        # Result tags must be in the same order of the file
        for index, expected_tag in enumerate(expected_tags):
            self.assertEqual(
                tags[index].id,
                expected_tag.get('id')
            )
            self.assertEqual(
                tags[index].value,
                expected_tag.get('value')
            )
            self.assertEqual(
                tags[index].parent_id,
                expected_tag.get('parent_id')
            )
            self.assertEqual(
                tags[index].index,
                index + JSONParser.inital_row
            )

    def test_export_data(self) -> None:
        result = JSONParser.export(self.taxonomy)
        tags = json.loads(result).get("tags")
        assert len(tags) == self.taxonomy.tag_set.count()
        for tag in tags:
            taxonomy_tag = self.taxonomy.tag_set.get(external_id=tag.get("id"))
            assert tag.get("value") == taxonomy_tag.value
            if tag.get("parent_id"):
                assert tag.get("parent_id") == taxonomy_tag.parent.external_id

    def test_import_with_export_output(self) -> None:
        output = JSONParser.export(self.taxonomy)
        json_file = BytesIO(output.encode())
        tags, errors = JSONParser.parse_import(json_file)
        output_tags = json.loads(output).get("tags")

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(tags), len(output_tags))

        for tag in tags:
            output_tag = None
            for out_tag in output_tags:
                if out_tag.get("id") == tag.id:
                    output_tag = out_tag
                    # Don't break because test coverage
            assert output_tag
            assert output_tag.get("value") == tag.value
            if output_tag.get("parent_id"):
                assert output_tag.get("parent_id") == tag.parent_id


@ddt.ddt
class TestCSVParser(TestImportExportMixin, TestCase):
    """
    Test for .csv parser
    """

    @ddt.data(
        (
            "value\n",
            ["Invalid '.csv' format: Missing 'id' field on CSV headers"],
        ),
        (
            "id\n",
            ["Invalid '.csv' format: Missing 'value' field on CSV headers"],
        ),
        (
            "id_name,value_name\n",
            [
                "Invalid '.csv' format: Missing 'id' field on CSV headers",
                "Invalid '.csv' format: Missing 'value' field on CSV headers"
            ],
        ),
        (
            # Valid
            "id,value\n",
            []
        ),
        (
            # Valid
            "id,value,ignored\n",
            []
        )
    )
    @ddt.unpack
    def test_load_data_errors(self, csv_data: str, expected_errors: list[str]):
        csv_file = BytesIO(csv_data.encode())

        tags, errors = CSVParser.parse_import(csv_file)
        self.assertEqual(len(tags), 0)
        self.assertEqual(len(errors), len(expected_errors))

        for error in errors:
            self.assertIn(str(error), expected_errors)

    @ddt.data(
        (
            "id,value\ntag_1\ntag_2,\n",
            [
                "Empty 'value' field on the row 2",
                "Empty 'value' field on the row 3",
            ]
        ),
        (
            "id,value\ntag_1,tag 1\n",  # Valid
            []
        )
    )
    @ddt.unpack
    def test_parse_tags_errors(self, csv_data, expected_errors):
        csv_file = BytesIO(csv_data.encode())

        _, errors = CSVParser.parse_import(csv_file)
        self.assertEqual(len(errors), len(expected_errors))

        for error in errors:
            self.assertIn(str(error), expected_errors)

    def _build_csv(self, tags):
        """
        Builds a csv from 'tags' dict
        """
        csv = "id,value,parent_id\n"
        for tag in tags:
            csv += (
                f"{tag.get('id')},{tag.get('value')},"
                f"{tag.get('parent_id') or ''}\n"
            )
        return csv

    def test_parse_tags(self) -> None:
        expected_tags = [
            {"id": "tag_1", "value": "tag 1"},
            {"id": "tag_2", "value": "tag 2"},
            {"id": "tag_3", "value": "tag 3", "parent_id": "tag_1"},
            {"id": "tag_4", "value": "tag 4"},
        ]
        csv_data = self._build_csv(expected_tags)
        csv_file = BytesIO(csv_data.encode())
        tags, errors = CSVParser.parse_import(csv_file)

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(tags), 4)

        # Result tags must be in the same order of the file
        for index, expected_tag in enumerate(expected_tags):
            self.assertEqual(
                tags[index].id,
                expected_tag.get('id')
            )
            self.assertEqual(
                tags[index].value,
                expected_tag.get('value')
            )
            self.assertEqual(
                tags[index].parent_id,
                expected_tag.get('parent_id')
            )
            self.assertEqual(
                tags[index].index,
                index + CSVParser.inital_row
            )

    def test_import_with_export_output(self) -> None:
        output = CSVParser.export(self.taxonomy)
        csv_file = BytesIO(output.encode())
        tags, errors = CSVParser.parse_import(csv_file)
        self.assertEqual(len(errors), 0)
        assert len(tags) == self.taxonomy.tag_set.count()

        for tag in tags:
            taxonomy_tag = self.taxonomy.tag_set.get(external_id=tag.id)
            assert tag.value == taxonomy_tag.value
            if tag.parent_id:
                assert tag.parent_id == taxonomy_tag.parent.external_id
