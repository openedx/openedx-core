"""
Errors raised while backing up or restoring a Learning Package.

Everything in this applet raises something that descends from
:class:`BackupRestoreError`, so callers that don't care about the specifics can
catch a single type. The subclasses exist to give useful debug output and to let
tests assert on a specific failure rather than on message text.

The hierarchy is grouped by the pipeline stage that detects the problem:

* :class:`ArchiveNotReadableError` -- ``archive.py``, we can't open the thing at
  all.
* :class:`ExtractionError` -- ``payload.py``, the archive's files are malformed
  in a way that stops us assembling the input document (unparseable TOML,
  duplicate entity definitions, missing mandatory files).
* :class:`SchemaError` -- ``validation.py``, a field failed pydantic validation.
* :class:`ConsistencyError` -- ``validation.py``, a cross-reference check that
  pydantic can't express (e.g. a container pointing at a child that isn't in the
  archive).

:class:`RestoreFailedError` is the aggregate that the public API raises. It
holds all of the individual errors found during a single restore attempt.
"""
from __future__ import annotations


class BackupRestoreError(Exception):
    """
    Base class for every error this applet raises.

    Args:
        message: Human-readable description of what went wrong.
        path: Archive-relative path of the file the problem was found in, e.g.
            ``"entities/unit1-b7eafb.toml"``. ``None`` when the error isn't
            attributable to a particular file.
    """

    def __init__(self, message, path=None):
        super().__init__(message)
        self.message = message
        self.path = path

    def __str__(self):
        return f"{self.path}: {self.message}" if self.path else self.message


class ArchiveNotReadableError(BackupRestoreError):
    """We could not open the archive at all (not a directory, not a zip file)."""


# --- Extraction Errors (payload.py) ---


class ExtractionError(BackupRestoreError):
    """
    Any error during the extraction process.

    At the moment, any error is fatal. The point of the different errors is to
    provide useful debug logging and to let us write tests that look for
    specific errors.
    """


class InvalidTOMLError(ExtractionError):
    def __init__(self, file_description, details, path):
        message = f"Cannot decode TOML for {file_description}: {details}"
        super().__init__(message, path=path)


class TableNotFoundError(ExtractionError):
    def __init__(self, file_description, table, path):
        self.table = table
        message = f"Table [{table}] not found in {file_description}."
        super().__init__(message, path=path)


class FieldsNotInTable(ExtractionError):
    def __init__(self, file_description, fields, path):
        self.fields = sorted(fields)
        message = f"{file_description} has fields not in a table: {', '.join(fields)}"
        super().__init__(message, path=path)


class MissingFileError(ExtractionError):
    """
    A file we require is not in the archive.
    """

    def __init__(self, file_description, path):
        message = f"{file_description} file not found at expected path"
        super().__init__(message, path=path)


class UnsupportedFormatError(ExtractionError):
    """The archive declares a ``format_version`` we don't know how to read."""


# --- Validation Errors (validation.py) ---


class SchemaError(BackupRestoreError):
    """
    A field failed pydantic validation.

    One of these is created for each entry in a ``pydantic.ValidationError``, so
    that we can attribute the failure to the archive file it came from instead
    of reporting a JSON pointer into a document the user never sees.

    Args:
        message: pydantic's error message for this entry.
        path: Archive-relative path of the source file, if we can work it out.
        location: The part of pydantic's ``loc`` tuple that is meaningful
            *within* that file, e.g. ``("versions", 0, "title")``.
    """

    def __init__(self, message, path=None, location=()):
        self.location = tuple(location)
        super().__init__(message, path=path)

    def __str__(self):
        parts = []
        if self.path:
            parts.append(str(self.path))
        if self.location:
            parts.append(".".join(str(part) for part in self.location))
        parts.append(self.message)
        return ": ".join(parts)


class ConsistencyError(BackupRestoreError):
    """
    A cross-reference in the archive doesn't hold up.

    These are the checks that pydantic can't express, because they involve more
    than one part of the document at once.
    """


class UnresolvedChildError(ConsistencyError):
    """A container version lists a child that isn't defined in the archive."""


class MissingEntityVersionError(ConsistencyError):
    """An entity's draft or published pointer names a version that isn't in the archive."""


class DuplicateEntityVersionError(ConsistencyError):
    """An entity declares the same ``version_num`` more than once."""


class MalformedEntityRefError(ConsistencyError):
    """An entity ref isn't in a shape we know how to load."""


class UnknownContainerTypeError(ConsistencyError):
    """The archive declares a container type this version of the code can't load."""


# --- Aggregate ---


class RestoreFailedError(BackupRestoreError):
    """
    The restore could not be completed. Holds every error we found.

    We deliberately gather as many problems as we can before raising, so that
    someone fixing up an archive by hand doesn't have to discover their mistakes
    one run at a time.
    """

    def __init__(
        self,
        errors: list[BackupRestoreError],
        archive_root: str | None = None,
    ):
        self.errors = list(errors)
        # The folder inside the archive we treated as the root, when the archive
        # wrapped its contents in one. Every path below is relative to it, so
        # saying so once up front saves a lot of confusion.
        self.archive_root = archive_root
        super().__init__(f"Restore failed with {len(self.errors)} error(s).")

    def __str__(self):
        return self.as_text()

    def as_text(self) -> str:
        """
        Render every error as a block of text suitable for a log file.

        The format matches what the pre-pydantic implementation wrote out, so
        that existing consumers of the restore log keep working.
        """
        lines = ["Errors encountered during restore:"]
        if self.archive_root:
            lines.append(f"Archive root: {self.archive_root}/")
        lines.extend(str(err) for err in self.errors)
        return "\n".join(lines) + "\n"
