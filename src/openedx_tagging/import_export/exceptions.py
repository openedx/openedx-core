"""
Exceptions for tag import/export actions
"""
from __future__ import annotations

import typing

from django.utils.translation import gettext as _

if typing.TYPE_CHECKING:
    from .actions import ImportAction


class TagImportError(Exception):
    """
    Base exception for import
    """

    def __init__(self, message: str = ""):
        super().__init__()
        self.message = message

    def __str__(self):
        return str(self.message)

    def __repr__(self):
        return f"{self.__class__.__name__}({str(self)})"


class TagParserError(TagImportError):
    """
    Base exception for parsers
    """

    def __init__(self, tag: dict | None, **kargs):  # pylint: disable=unused-argument
        super().__init__()
        self.message = _("Import parser error on {tag}").format(tag=tag)


class ImportActionError(TagImportError):
    """
    Base exception for actions
    """

    def __init__(self, action: ImportAction, message: str, **kargs):
        super().__init__(**kargs)
        self.message = _(
            "Action error in '{name}' (#{index}): {message}"
        ).format(name=action.name, index=action.index, message=message)


class DuplicateFinalIdError(TagImportError):
    """
    Exception raised when two or more rows in the same import claim the
    same final id, so it's ambiguous which row's changes should land on
    that tag.
    """

    def __init__(self, tag_id: str, row_indexes: list[int], **kargs):
        super().__init__(**kargs)
        self.message = _(
            "Duplicate id ({tag_id}): rows {row_indexes} all claim it as "
            "their final id. Each row's id must be unique within a single import."
        ).format(
            tag_id=tag_id,
            row_indexes=", ".join(f"#{index}" for index in row_indexes),
        )


class ImportActionConflict(ImportActionError):
    """
    Exception used when exists a conflict between actions
    """

    def __init__(
        self,
        action: ImportAction,
        conflict_action_index: int,
        message: str,
        **kargs,
    ):
        super().__init__(action, message, **kargs)
        self.message = _(
            "Conflict with '{action_name}' (#{action_index}) "
            "and action #{conflict_action_index}: {message}"
        ).format(
            action_name=action.name,
            action_index=action.index,
            conflict_action_index=conflict_action_index,
            message=message,
        )


class InvalidFormat(TagParserError):
    """
    Exception used when there is an error with the format
    """

    def __init__(self, tag: dict | None, input_format: str, message: str, **kargs):
        super().__init__(tag, **kargs)
        self.message = _("Invalid '{format}' format: {message}").format(format=input_format, message=message)


class FieldJSONError(TagParserError):
    """
    Exception used when missing a required field on the .json
    """

    def __init__(self, tag: dict | None, field: str, **kargs):
        super().__init__(tag, **kargs)
        self.message = _("Missing '{field}' field on {tag}").format(field=field, tag=tag)


class EmptyJSONField(TagParserError):
    """
    Exception used when a required field is empty on the .json
    """

    def __init__(self, tag: dict | None, field: str, **kargs):
        super().__init__(tag, **kargs)
        self.message = _("Empty '{field}' field on {tag}").format(field=field, tag=tag)


class EmptyCSVField(TagParserError):
    """
    Exception used when a required field is empty on the .csv
    """

    def __init__(self, tag: dict | None, field: str, row: int, **kargs):
        super().__init__(tag, **kargs)
        self.message = _("Empty '{field}' field on the row {row}").format(field=field, row=row)


class InvalidJSONField(TagParserError):
    """
    Exception used when a field has an invalid type on the .json
    """

    def __init__(self, tag: dict | None, field: str, **kargs):
        super().__init__(tag, **kargs)
        self.message = _("Invalid '{field}' field on {tag}").format(field=field, tag=tag)


class InvalidCSVField(TagParserError):
    """
    Exception used when a field has an invalid type on the .csv
    """

    def __init__(self, tag: dict | None, field: str, row: int, **kargs):
        super().__init__(tag, **kargs)
        self.message = _("Invalid '{field}' field on the row {row}").format(field=field, row=row)
