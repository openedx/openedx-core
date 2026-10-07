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
            "Duplicate id ({tag_id}): file rows {row_indexes} all claim it as "
            "their final id. Each row's id must be unique within a single import."
        ).format(tag_id=tag_id, row_indexes=", ".join(str(index) for index in row_indexes))


class StaleIdTargetedByPlainRowError(TagImportError):
    """
    Exception raised when a plain row (no previous_id) in an import targets
    an id that a different row in the same import is renaming away from,
    rather than the already-supported case of a rename row landing on it.
    """

    def __init__(self, tag_id: str, plain_row: int, rename_row: int, **kargs):
        super().__init__(**kargs)
        self.message = _(
            "Row {plain_row}'s id ({tag_id}) is renamed away by row {rename_row} in this "
            "same import. A plain row (no previous_id) may not target an id another row "
            "is renaming away from."
        ).format(tag_id=tag_id, plain_row=plain_row, rename_row=rename_row)


class ParentReferencesContendedVacatedIdError(TagImportError):
    """
    Exception raised when a row's parent_id names an id that one row
    vacates via rename while a different row claims that same id as its
    own final id in the same import (a swap or cycle). It's ambiguous
    whether the reference means the original tag or its replacement, so
    the row is rejected instead of guessed at.
    """

    def __init__(self, parent_id: str, child_row: int, source_row: int, new_id: str, target_row: int, **kargs):
        super().__init__(**kargs)
        self.message = _(
            "Row {child_row}'s parent_id ({parent_id}) is ambiguous: row {source_row} renames "
            "that id away (to '{new_id}'), while row {target_row} renames a different tag onto "
            "'{parent_id}' in this same import. Reference the parent by its tag's final id "
            "instead of the vacated one."
        ).format(child_row=child_row, parent_id=parent_id, source_row=source_row, new_id=new_id, target_row=target_row)


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
