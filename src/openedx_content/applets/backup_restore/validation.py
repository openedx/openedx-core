"""
Archive-agnostic validation of the input data models.

This sits between ``payload.py`` (which knows about files) and ``loading.py``
(which knows about the database). It answers one question: is this input good
enough to load? It never raises -- every problem it finds is collected onto
``ValidatedLearningPackageInput.errors`` so that someone repairing an archive by
hand can see everything that's wrong in one pass.

There are three sources of errors here:

1. Errors that ``payload.py`` already found while reading the files. Those are
   ``ExtractionError`` instances and are carried through as-is.
2. Schema errors, from running the raw data through ``CompletePackageInputData``.
   Pydantic reports these against a path into the assembled document, so we
   translate that back into the archive file it came from.
3. Consistency errors -- cross-references that pydantic has no way to express,
   like a container listing a child that isn't anywhere in the archive.
"""
from __future__ import annotations

import attrs
from fsspec.implementations.dirfs import DirFileSystem
from pydantic import ValidationError

from .errors import (
    BackupRestoreError,
    DuplicateEntityVersionError,
    MalformedEntityRefError,
    MissingEntityVersionError,
    SchemaError,
    UnknownContainerTypeError,
    UnresolvedChildError,
)
from .payload import PayloadExtractor, UnvalidatedLearningPackageInput
from .schema import CompletePackageInputData


@attrs.define(frozen=True)
class ValidatedLearningPackageInput:
    """
    The result of validating an archive's contents.

    ``data`` is ``None`` when the input was too broken to build a model from at
    all. ``errors`` being non-empty means the restore must not proceed, even if
    ``data`` is populated -- a consistency error can be found on a document that
    is otherwise structurally valid.
    """
    data: CompletePackageInputData | None
    errors: list[BackupRestoreError]

    # The re-rooted filesystem from extraction. ``fs.path`` is the folder inside
    # the archive that was treated as its root, or "" if there wasn't one. See
    # UnvalidatedLearningPackageInput.fs comments for more details.
    fs: DirFileSystem


def validate(
    unvalidated_lp: UnvalidatedLearningPackageInput,
) -> ValidatedLearningPackageInput:
    """
    Validate extracted archive data, gathering every error we can find.
    """
    # Extraction errors are already BackupRestoreErrors, so they just come along.
    errors: list[BackupRestoreError] = list(unvalidated_lp.errors)

    try:
        data = CompletePackageInputData.model_validate(unvalidated_lp.raw_data)
    except ValidationError as val_err:
        data = None
        errors.extend(_schema_errors_for(val_err, unvalidated_lp))

    if data is not None:
        errors.extend(_consistency_errors_for(data))

    return ValidatedLearningPackageInput(
        data=data,
        fs=unvalidated_lp.fs,
        errors=errors,
    )


def _schema_errors_for(
    val_err: ValidationError,
    unvalidated_lp: UnvalidatedLearningPackageInput,
) -> list[SchemaError]:
    """
    Turn one pydantic ValidationError into one SchemaError per problem found.
    """
    return [
        SchemaError(
            message=entry["msg"],
            **_source_for_loc(entry["loc"], unvalidated_lp),
        )
        for entry in val_err.errors()
    ]


def _source_for_loc(
    loc: tuple,
    unvalidated_lp: UnvalidatedLearningPackageInput,
) -> dict:
    """
    Map a pydantic ``loc`` back to the archive file it came from.

    Pydantic reports errors against the combined document we assemble in
    ``payload.py``, e.g. ``("entities", 3, "versions", 0, "title")``. Nobody
    editing an archive has ever seen that document, so we split the ``loc`` into
    the file it came from and the location within that file.
    """
    match loc:
        case ("entities" | "collections" as section, int() as index, *rest):
            return {
                "path": _src_path_at(section, index, unvalidated_lp),
                "location": tuple(rest),
            }
        case ("meta" | "learning_package", *_):
            # TODO: This is a problematic abstraction leak
            return {"path": PayloadExtractor.ROOT_PACKAGE_PATH, "location": tuple(loc)}

    return {"path": None, "location": tuple(loc)}


def _src_path_at(
    section: str,
    index: int,
    unvalidated_lp: UnvalidatedLearningPackageInput,
) -> str | None:
    """
    Look up the source file of the item at ``index`` of ``section``.

    We can't read this off the validated model, because we only need it when
    validation has already failed.
    """
    raw_items = unvalidated_lp.raw_data.get(section, [])
    if 0 <= index < len(raw_items):
        raw_item = raw_items[index]
        if isinstance(raw_item, dict):
            return raw_item.get("src_path")
    return None


def _consistency_errors_for(
    data: CompletePackageInputData,
) -> list[BackupRestoreError]:
    """
    Check the cross-references that pydantic can't express.

    Every check here corresponds to something that would otherwise blow up in the
    middle of ``loading.py``, with a traceback pointing at our code instead of at
    the part of the archive that's actually wrong.
    """
    errors: list[BackupRestoreError] = []
    known_refs = {entity.key for entity in data.entities}

    for entity in sorted(data.entities, key=lambda e: e.key):
        entity_ref = entity.key
        path = entity.src_path

        # Check: is this a container type we actually know how to build?
        if isinstance(entity.container, dict):
            declared = ", ".join(sorted(entity.container)) or "(empty)"
            errors.append(
                UnknownContainerTypeError(
                    f'Entity "{entity_ref}" declares an unsupported container '
                    f"type: {declared}",
                    path=path,
                )
            )
        elif entity.container is None:
            # Not a container, so it's a Component, and we derive the component
            # type from the ref itself.
            if len(entity_ref.split(":")) != 3:
                errors.append(
                    MalformedEntityRefError(
                        f'Component ref "{entity_ref}" should be of the form '
                        '"{namespace}:{type}:{code}"',
                        path=path,
                    )
                )

        # Check: no version_num declared twice for the same entity.
        version_nums = [version.version_num for version in entity.versions]
        for duplicated in sorted({v for v in version_nums if version_nums.count(v) > 1}):
            errors.append(
                DuplicateEntityVersionError(
                    f'Entity "{entity_ref}" declares version {duplicated} more than once',
                    path=path,
                )
            )

        # Check: the draft/published pointers name versions that exist here.
        available = set(version_nums)
        for label, pointer in (("draft", entity.draft), ("published", entity.published)):
            if pointer.version_num is not None and pointer.version_num not in available:
                errors.append(
                    MissingEntityVersionError(
                        f'Entity "{entity_ref}" points [entity.{label}] at version '
                        f"{pointer.version_num}, which is not in the archive",
                        path=path,
                    )
                )

        # Check: every child of every container version is in the archive.
        for version in entity.versions:
            if version.container is None:
                continue
            for child_ref in version.container.children:
                if child_ref not in known_refs:
                    errors.append(
                        UnresolvedChildError(
                            f'Entity "{entity_ref}" v{version.version_num} lists child '
                            f'"{child_ref}", which is not defined in the archive',
                            path=path,
                        )
                    )

    return errors
