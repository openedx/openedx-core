"""
Parsers to import and export tags
"""
from __future__ import annotations

import csv
import json
from enum import Enum
from io import StringIO, TextIOWrapper
from typing import BinaryIO

from django.utils.translation import gettext as _

from ..models import Taxonomy
from .exceptions import EmptyCSVField, EmptyJSONField, FieldJSONError, InvalidFormat, TagParserError
from .import_plan import TagItem


class ParserFormat(Enum):
    """
    Format of import tags to taxonomies
    """

    JSON = ".json"
    CSV = ".csv"


class Parser:
    """
    Base class to create a parser

    This contains the base functions to convert between
    a simple file format like CSV/JSON and a list of TagItems.
    It can convert in both directions, for use during import or export.

    If you want to add a new field, you can add it to
    `required_fields` or `optional_fields` depending on the field type

    To create a new Parser you need to implement `_load_data` and `_export_data`
    """

    required_fields = ["id", "value"]
    optional_fields = ["parent_id"]

    # Set the format associated to the parser
    format: ParserFormat
    # We can change the error when is missing a required field
    missing_field_error = TagParserError
    # We can change the error when a required field is empty
    empty_field_error = TagParserError
    # We can change the initial row/index
    inital_row = 1

    @classmethod
    def parse_import(cls, file: BinaryIO) -> tuple[list[TagItem], list[TagParserError]]:
        """
        Parse tags in file an returns tags ready for use in TagImportPlan

        Top function that calls `_load_data` and `_parse_tags`.
        Handle errors returned by both functions.
        """
        try:
            tags_data, load_errors = cls._load_data(file)
            if load_errors:
                return [], load_errors
        except Exception as error:
            raise error
        finally:
            file.close()

        return cls._parse_tags(tags_data)

    @classmethod
    def export(cls, taxonomy: Taxonomy) -> str:
        """
        Returns all tags in taxonomy.
        The output file can be used to recreate the taxonomy with `parse_import`
        """
        tags = cls._load_tags_for_export(taxonomy)
        return cls._export_data(tags, taxonomy)

    @classmethod
    def _load_data(cls, file: BinaryIO) -> tuple[list[dict], list[TagParserError]]:
        """
        Each parser implements this function according to its format.
        This function reads the file and returns a list with the values of each tag.

        This function does not do field validations, it only does validations of the
        file structure in the parser format. Field validations are done in `_parse_tags`
        """
        raise NotImplementedError

    @classmethod
    def _export_data(cls, tags: list[dict], taxonomy: Taxonomy) -> str:
        """
        Each parser implements this function according to its format.
        Returns a string with tags data in the parser format.
        Can use `taxonomy` to export taxonomy metadata.

        It must be implemented in such a way that the output of
        this function works with _load_data
        """
        raise NotImplementedError

    @classmethod
    def _parse_tags(
        cls, tags_data: list[dict]
    ) -> tuple[list[TagItem], list[TagParserError]]:
        """
        Validate the required fields of each tag.

        Return a list of TagItems
        and a list of validation errors.
        """
        tags = []
        errors = []
        row = cls.inital_row
        for tag in tags_data:
            has_error = False
            tag_data = {}

            # Verify the required fields
            for req_field in cls.required_fields:
                if req_field not in tag:
                    # Verify if the field exists
                    errors.append(
                        cls.missing_field_error(
                            tag,
                            field=req_field,
                            row=row,
                        )
                    )
                    has_error = True
                elif not tag.get(req_field):
                    # Verify if the value of the field is not empty
                    errors.append(
                        cls.empty_field_error(
                            tag,
                            field=req_field,
                            row=row,
                        )
                    )
                    has_error = True
                else:
                    tag_data[req_field] = tag[req_field]

            tag_data["index"] = row
            row += 1

            # Skip parse if there is an error
            if has_error:
                continue

            # Optional fields default to None
            for opt_field in cls.optional_fields:
                tag_data[opt_field] = tag.get(opt_field) or None

            tags.append(TagItem(**tag_data))

        return tags, errors

    @classmethod
    def _load_tags_for_export(cls, taxonomy: Taxonomy) -> list[dict]:
        """
        Returns a list of taxonomy's tags in the form of a dictionary
        with required and optional fields

        The tags are ordered by hierarchy, first, parents and then children.
        `get_filtered_tags` is in charge of returning this in a hierarchical
        way.
        """
        tags = taxonomy.get_filtered_tags().all()
        result = []
        for tag in tags:
            result_tag = {
                "id": tag["external_id"] or tag["_id"],
                "value": tag["value"],
            }
            if tag["parent_value"]:
                parent_tag = next(t for t in tags if t["value"] == tag["parent_value"])
                result_tag["parent_id"] = parent_tag["external_id"] or parent_tag["_id"]
            result.append(result_tag)
        return result


class JSONParser(Parser):
    """
    Parser used with .json files

    Valid file:
    ```
      {
        "tags": [
          {
            "id": "tag_1",
            "value": "tag 1",
            "parent_id": "tag_2",
          }
        ]
      }
    ```
    """

    format = ParserFormat.JSON
    missing_field_error: type[TagParserError] = FieldJSONError
    empty_field_error: type[TagParserError] = EmptyJSONField
    inital_row = 0

    @classmethod
    def _load_data(cls, file: BinaryIO) -> tuple[list[dict], list[TagParserError]]:
        """
        Read a .json file and validates the root structure of the json
        """
        file.seek(0)
        try:
            tags_data = json.load(file)
        except json.JSONDecodeError as error:
            return [], [
                InvalidFormat(tag=None, input_format=cls.format.value, message=str(error))
            ]
        if "tags" not in tags_data:
            return [], [
                InvalidFormat(
                    tag=None,
                    input_format=cls.format.value,
                    message=_("Missing 'tags' field on the .json file"),
                )
            ]

        tags_data = tags_data.get("tags")
        return tags_data, []

    @classmethod
    def _export_data(cls, tags: list[dict], taxonomy: Taxonomy) -> str:
        """
        Export tags and taxonomy metadata in JSON format
        """
        json_result = {
            "name": taxonomy.name,
            "description": taxonomy.description,
            "tags": tags,
        }
        return json.dumps(json_result)


class CSVParser(Parser):
    """
    Parser used with .csv files

    Valid file:
    ```
    id,value,parent_id
    tag_1,tag 1,
    tag_2,tag 2,tag_1
    ```
    """

    format = ParserFormat.CSV
    empty_field_error: type[TagParserError] = EmptyCSVField
    inital_row = 2

    @classmethod
    def _load_data(cls, file: BinaryIO) -> tuple[list[dict], list[TagParserError]]:
        """
        Read a .csv file and validates the header fields
        """
        file.seek(0)
        text_tags = TextIOWrapper(file, encoding="utf-8")
        csv_reader = csv.DictReader(text_tags)
        header_fields = csv_reader.fieldnames
        errors = cls._verify_header(list(header_fields or []))
        if errors:
            return [], errors
        return list(csv_reader), []

    @classmethod
    def _export_data(cls, tags: list[dict], taxonomy: Taxonomy) -> str:
        """
        Export tags in CSV format

        """
        fields = cls.required_fields + cls.optional_fields

        with StringIO() as csv_buffer:
            csv_writer = csv.DictWriter(csv_buffer, fieldnames=fields)
            csv_writer.writeheader()

            for tag in tags:
                csv_writer.writerow(tag)

            csv_string = csv_buffer.getvalue()
            return csv_string

    @classmethod
    def _verify_header(cls, header_fields: list[str]) -> list[TagParserError]:
        """
        Verify that the header contains the required fields
        """
        errors: list[TagParserError] = []
        for req_field in cls.required_fields:
            if req_field not in header_fields:
                errors.append(
                    InvalidFormat(
                        tag=None,
                        input_format=cls.format.value,
                        message=_("Missing '{req_field}' field on CSV headers").format(req_field=req_field),
                    )
                )
        return errors


# Add parsers here
_parsers = [JSONParser, CSVParser]


def get_parser(parser_format: ParserFormat) -> type[Parser]:
    """
    Get the parser for the respective `format`

    Raise `ValueError` if no parser found
    """
    for parser in _parsers:
        if parser_format == parser.format:
            return parser

    raise ValueError(_("Parser not found for format {parser_format}").format(parser_format=parser_format))
