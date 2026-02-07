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
