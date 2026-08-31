"""
Tests for turning extracted archive data into a validated model.

The contract this module has to hold up:

* ``validate`` never raises. Everything it finds goes onto ``.errors``, so that
  someone repairing an archive by hand sees every problem in one pass.
* Errors name the archive file they came from. Pydantic reports against the
  combined document we assemble internally, which nobody editing an archive has
  ever seen, so we translate those locations back into file paths.
* Every cross-reference that ``loading.py`` relies on is checked here, so that a
  broken archive produces a readable error instead of a traceback from the
  middle of a database write.

These tests are strictly for the validation module, and therefore don't need
Django to run.
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from fsspec.implementations.dirfs import DirFileSystem

from openedx_content.applets.backup_restore import validation
from openedx_content.applets.backup_restore.errors import (
    DuplicateEntityVersionError,
    InvalidTOMLError,
    MalformedEntityRefError,
    MissingFileError,
    MissingEntityVersionError,
    RestoreFailedError,
    SchemaError,
    UnknownContainerTypeError,
    UnresolvedChildError,
)
from openedx_content.applets.backup_restore.payload import UnvalidatedLearningPackageInput

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
CREATED = datetime(2026, 4, 8, 15, 22, 12, 780012, tzinfo=timezone.utc)


def component(key: str, **overrides) -> dict:
    """
    Raw data for a Component entity (i.e. one with no container).

    Each entity carries its own key and source file, the same way collections
    do, which is what lets validation report duplicates and attribute errors to
    a file.
    """
    return {
        "key": key,
        "src_path": f"entities/{key}.toml",
        "can_stand_alone": True,
        "created": CREATED,
        "versions": [],
        **overrides,
    }


def container(key: str, kind: str, **overrides) -> dict:
    """Raw data for a Container entity of the given kind."""
    return component(key, container={kind: {}}, **overrides)


def version(version_num: int, children=None, **overrides) -> dict:
    raw = {"version_num": version_num, "title": f"v{version_num}", **overrides}
    if children is not None:
        raw["container"] = {"children": children}
    return raw


def unvalidated(entities=None, collections=None, errors=None, root=None, **overrides):
    """Build an UnvalidatedLearningPackageInput without going through files."""
    raw_data = {
        "meta": {"format_version": 1},
        "learning_package": {"key": "lib:Axim:FunLib"},
        "entities": entities if entities is not None else [],
        "collections": collections if collections is not None else [],
        **overrides,
    }
    return UnvalidatedLearningPackageInput(
        raw_data=raw_data,
        errors=errors or [],
        # Built the way PayloadExtractor builds it: always a DirFileSystem, whose
        # .path is the wrapper folder inside the archive (or "" if there is none).
        fs=DirFileSystem(path=root or "/", fs=DirFileSystem(FIXTURES_ROOT)),
    )


class ValidateSuccessTest(TestCase):
    """Archives that validate cleanly."""

    def test_minimal_package(self):
        result = validation.validate(unvalidated())

        assert result.errors == []
        assert result.data is not None
        assert result.data.learning_package.key == "lib:Axim:FunLib"

    def test_fs_is_passed_through(self):
        """The loader needs the filesystem later, to read static assets."""
        source = unvalidated()
        result = validation.validate(source)
        assert result.fs is source.fs

    def test_well_formed_container_tree(self):
        result = validation.validate(unvalidated(entities=[
            container("section-1", "section", versions=[version(1, ["subsection-1"])]),
            container("subsection-1", "subsection", versions=[version(1, ["unit-1"])]),
            container("unit-1", "unit", versions=[version(1, ["xblock.v1:html:abc"])]),
            component("xblock.v1:html:abc", versions=[version(1)]),
        ]))

        assert result.errors == []


class ExtractionErrorsCarryThroughTest(TestCase):
    """
    Extraction errors are already BackupRestoreErrors, so they come through as
    themselves -- not flattened into some other error record.
    """

    def test_error_type_and_path_are_preserved(self):
        extraction_error = InvalidTOMLError(
            "Entity", details="bad token", path="entities/broken.toml"
        )
        result = validation.validate(unvalidated(errors=[extraction_error]))

        assert result.errors[0] is extraction_error
        assert isinstance(result.errors[0], InvalidTOMLError)
        assert result.errors[0].path == "entities/broken.toml"

    def test_extraction_errors_are_reported_alongside_schema_errors(self):
        result = validation.validate(
            unvalidated(
                errors=[MissingFileError("Root Package", path="package.toml")],
                learning_package={},  # also missing its key
            )
        )

        error_types = {type(err) for err in result.errors}
        assert MissingFileError in error_types
        assert SchemaError in error_types


class SchemaErrorSourceMappingTest(TestCase):
    """
    Pydantic's `loc` gets translated back into "which file is wrong".
    """

    def test_learning_package_errors_point_at_package_toml(self):
        result = validation.validate(unvalidated(learning_package={}))

        assert result.data is None
        error = result.errors[0]
        assert isinstance(error, SchemaError)
        assert error.path == "package.toml"
        assert error.location == ("learning_package", "key")

    def test_meta_errors_point_at_package_toml(self):
        result = validation.validate(unvalidated(meta={}))

        error = result.errors[0]
        assert error.path == "package.toml"
        assert error.location == ("meta", "format_version")

    def test_entity_errors_point_at_the_entity_file(self):
        result = validation.validate(unvalidated(
            entities=[container("unit1-b7eafb", "unit", versions=[{"version_num": 1}])],
        ))

        error = result.errors[0]
        assert error.path == "entities/unit1-b7eafb.toml"
        # The list index is dropped, since the file only describes one entity.
        assert error.location == ("versions", 0, "title")

    def test_entity_errors_fall_back_when_path_is_unknown(self):
        """An entity with no recorded source file still gets its error reported."""
        result = validation.validate(unvalidated(
            entities=[
                container("unit-1", "unit", versions=[{"version_num": 1}], src_path=None)
            ],
        ))

        assert result.errors[0].path is None
        assert result.errors[0].location == ("versions", 0, "title")

    def test_collection_errors_point_at_the_collection_file(self):
        result = validation.validate(unvalidated(
            collections=[{"key": "no-title", "src_path": "collections/broken.toml"}],
        ))

        error = result.errors[0]
        assert error.path == "collections/broken.toml"
        assert error.location == ("title",)

    def test_error_message_includes_location(self):
        result = validation.validate(unvalidated(
            entities=[container("unit1", "unit", versions=[{"version_num": 1}])],
        ))

        assert "entities/unit1.toml: versions.0.title" in str(result.errors[0])


class ConsistencyCheckTest(TestCase):
    """
    Cross-references pydantic can't express. Each of these would otherwise be an
    uncaught exception in the middle of loading.
    """

    def test_unresolved_child(self):
        result = validation.validate(unvalidated(
            entities=[container("unit1", "unit", versions=[version(1, ["nope"])])],
        ))

        assert len(result.errors) == 1
        error = result.errors[0]
        assert isinstance(error, UnresolvedChildError)
        assert error.path == "entities/unit1.toml"
        assert "nope" in error.message

    def test_draft_pointing_at_a_missing_version(self):
        result = validation.validate(unvalidated(entities=[
            container("unit-1", "unit", draft={"version_num": 7}, versions=[version(1)]),
        ]))

        assert len(result.errors) == 1
        assert isinstance(result.errors[0], MissingEntityVersionError)
        assert "[entity.draft]" in result.errors[0].message

    def test_published_pointing_at_a_missing_version(self):
        result = validation.validate(unvalidated(entities=[
            container(
                "unit-1", "unit", published={"version_num": 7}, versions=[version(1)]
            ),
        ]))

        assert len(result.errors) == 1
        assert isinstance(result.errors[0], MissingEntityVersionError)
        assert "[entity.published]" in result.errors[0].message

    def test_draft_and_published_may_be_absent(self):
        """An entity that was created and then reset to published has neither."""
        result = validation.validate(unvalidated(entities=[
            container("unit-1", "unit", versions=[version(1)]),
        ]))
        assert result.errors == []

    def test_duplicate_version_num(self):
        result = validation.validate(unvalidated(entities=[
            container("unit-1", "unit", versions=[version(2), version(2)]),
        ]))

        duplicate_errors = [
            err for err in result.errors if isinstance(err, DuplicateEntityVersionError)
        ]
        assert len(duplicate_errors) == 1
        assert "version 2" in duplicate_errors[0].message

    def test_malformed_component_ref(self):
        """
        Component refs are "{namespace}:{type}:{code}" -- the loader splits on
        the colons to work out what kind of block to build.
        """
        result = validation.validate(unvalidated(entities=[
            component("not-a-component-ref", versions=[version(1)]),
        ]))

        assert len(result.errors) == 1
        assert isinstance(result.errors[0], MalformedEntityRefError)

    def test_container_refs_are_not_required_to_have_colons(self):
        """Only Components derive meaning from the shape of their ref."""
        result = validation.validate(unvalidated(entities=[
            container("unit1-b7eafb", "unit", versions=[version(1)]),
        ]))
        assert result.errors == []

    def test_unknown_container_type(self):
        result = validation.validate(unvalidated(
            entities=[component("thing1", container={"chapter": {}})],
        ))

        assert len(result.errors) == 1
        error = result.errors[0]
        assert isinstance(error, UnknownContainerTypeError)
        assert error.path == "entities/thing1.toml"
        assert "chapter" in error.message

    def test_consistency_checks_are_skipped_when_the_schema_is_broken(self):
        """
        If we couldn't build a model, there's nothing to cross-reference. We
        report the schema errors rather than inventing consistency errors on top
        of data we know is malformed.
        """
        result = validation.validate(unvalidated(
            meta={},
            entities=[container("unit-1", "unit", versions=[version(1, ["nope"])])],
        ))

        assert result.data is None
        assert all(isinstance(err, SchemaError) for err in result.errors)

    def test_all_problems_are_reported_together(self):
        result = validation.validate(unvalidated(entities=[
            container(
                "unit-1", "unit", draft={"version_num": 9}, versions=[version(1, ["nope"])]
            ),
            component("bad-component-ref", versions=[version(1)]),
        ]))

        error_types = {type(err) for err in result.errors}
        assert error_types == {
            UnresolvedChildError,
            MissingEntityVersionError,
            MalformedEntityRefError,
        }


class RestoreFailedErrorTest(TestCase):
    """Tests for how a batch of errors is reported."""

    def test_as_text_matches_the_legacy_log_format(self):
        error = RestoreFailedError([
            MissingFileError("Root Package", path="package.toml"),
            InvalidTOMLError("Entity", details="bad token", path="entities/x.toml"),
        ])

        assert error.as_text() == (
            "Errors encountered during restore:\n"
            "package.toml: Root Package file not found at expected path\n"
            "entities/x.toml: Cannot decode TOML for Entity: bad token\n"
        )

    def test_errors_are_kept_for_inspection(self):
        original = MissingFileError("Root Package", path="package.toml")
        error = RestoreFailedError([original])

        assert error.errors == [original]


class SourceMappingFallbackTest(TestCase):
    """
    Errors we can't attribute to a file still get reported.

    These paths shouldn't come up in practice, but silently dropping an error
    because we couldn't work out where it came from would be much worse than
    reporting it without a filename.
    """

    def test_unrecognized_location_has_no_path(self):
        result = validation.validate(unvalidated(entities="not-a-list"))

        assert result.data is None
        error = result.errors[0]
        assert error.path is None
        assert error.location == ("entities",)
        assert str(error).startswith("entities:")

    def test_collection_without_a_src_path(self):
        result = validation.validate(unvalidated(
            collections=[{"key": "no-title"}],  # no src_path to attribute it to
        ))

        assert result.errors[0].path is None

    def test_schema_error_without_a_location(self):
        error = SchemaError("something went wrong", path="package.toml")
        assert str(error) == "package.toml: something went wrong"


class ArchiveRootPassthroughTest(TestCase):
    """
    The detected archive root travels with the validated input.

    It has no effect on validation -- every path is already relative to it -- but
    the error report says which folder we picked, since that isn't obvious from
    the paths alone.
    """

    def test_root_is_carried_through(self):
        """
        The root travels on the filesystem itself, not as a separate field.

        DirFileSystem already records what it was rooted at, so there is nothing
        for validation to copy across.
        """
        assert validation.validate(unvalidated(root="MyLib")).fs.path == "MyLib"

    def test_no_root_by_default(self):
        assert validation.validate(unvalidated()).fs.path == ""

    def test_as_text_names_the_root_when_there_is_one(self):
        error = RestoreFailedError(
            [MissingFileError("Root Package", path="package.toml")],
            archive_root="MyLib",
        )

        assert error.as_text() == (
            "Errors encountered during restore:\n"
            "Archive root: MyLib/\n"
            "package.toml: Root Package file not found at expected path\n"
        )

    def test_as_text_omits_the_root_when_there_isn_t_one(self):
        error = RestoreFailedError([MissingFileError("Root Package", path="package.toml")])

        assert "Archive root" not in error.as_text()


class DuplicateEntityTest(TestCase):
    """
    Two files defining the same entity key.

    This is checked here rather than during extraction for the same reason
    duplicate Collections are: entities are kept in a list, so both definitions
    survive extraction intact and validation can name both files.
    """

    def test_duplicate_entity_keys_are_rejected(self):
        result = validation.validate(unvalidated(entities=[
            component("xblock.v1:html:abc", src_path="entities/first.toml"),
            component("xblock.v1:html:abc", src_path="entities/second.toml"),
        ]))

        assert result.data is None
        assert len(result.errors) == 1
        error = result.errors[0]
        assert isinstance(error, SchemaError)
        # The message has to name both files to be actionable.
        assert "entities/first.toml" in error.message
        assert "entities/second.toml" in error.message
        assert "xblock.v1:html:abc" in error.message

    def test_distinct_entity_keys_are_fine(self):
        result = validation.validate(unvalidated(entities=[
            component("xblock.v1:html:abc"),
            component("xblock.v1:html:def"),
        ]))

        assert result.errors == []

    def test_missing_entity_key(self):
        """
        An entity file with no key at all.

        Extraction used to reject this, because the key became a dict key and it
        had nowhere to put an entity without one. Now it's a required field like
        any other.
        """
        result = validation.validate(unvalidated(
            entities=[{"created": CREATED, "src_path": "entities/no_key.toml"}],
        ))

        assert result.data is None
        error = result.errors[0]
        assert isinstance(error, SchemaError)
        assert error.path == "entities/no_key.toml"
        assert error.location == ("key",)


class ErrorTextTest(TestCase):
    """How individual errors render into the restore log."""

    def test_an_error_with_no_path_omits_it(self):
        """
        Not everything is attributable to a single file.

        A duplicate key is reported against the whole section, so there is no
        one path to name -- and "None: ..." in a log file helps nobody.
        """
        error = UnknownContainerTypeError("Entity declares an unsupported container")

        assert str(error) == "Entity declares an unsupported container"

    def test_an_error_with_a_path_names_it(self):
        error = UnknownContainerTypeError("nope", path="entities/thing.toml")

        assert str(error) == "entities/thing.toml: nope"
