"""
This module provides functionality to create a zip file containing the learning package data,
including a TOML representation of the learning package and its entities.
"""

import hashlib
import time
import tomllib
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, List, Literal, Optional, Tuple

from django.contrib.auth.models import User as UserType  # pylint: disable=imported-auth-user
from django.db import transaction
from django.db.models import Prefetch, QuerySet
from django.utils.text import slugify
from rest_framework import serializers

from openedx_content.models_api import (
    Collection,
    ComponentType,
    ComponentVersion,
    ComponentVersionMedia,
    LearningPackage,
    Media,
    PublishableEntity,
    PublishableEntityVersion,
)

from ..collections import api as collections_api
from ..components import api as components_api
from ..containers import api as containers_api
from ..media import api as media_api
from ..publishing import api as publishing_api
from ..sections.models import Section
from ..subsections.models import Subsection
from ..units.models import Unit
from .serializers import (
    CollectionSerializer,
    ComponentSerializer,
    ComponentVersionSerializer,
    ContainerSerializer,
    ContainerVersionSerializer,
    LearningPackageMetadataSerializer,
    LearningPackageSerializer,
)
from .toml import (
    parse_collection_toml,
    parse_learning_package_toml,
    parse_publishable_entity_toml,
    toml_collection,
    toml_learning_package,
    toml_publishable_entity,
)

TOML_PACKAGE_NAME = "package.toml"
DEFAULT_USERNAME = "command"


def slugify_hashed_filename(identifier: str) -> str:
    """
    Generate a filesystem-safe filename from an identifier.

    Why:
        Identifiers may contain characters that are invalid or ambiguous
        in filesystems (e.g., slashes, colons, case differences).
        Additionally, two different identifiers might normalize to the same
        slug after cleaning. To avoid collisions and ensure uniqueness,
        we append a short blake2b hash.

    What:
        - Slugify the identifier (preserves most characters, only strips
          filesystem-invalid ones).
        - Append a short hash for uniqueness.
        - Result: human-readable but still unique and filesystem-safe filename.
    """
    slug = slugify(identifier, allow_unicode=True)
    # Short digest ensures uniqueness without overly long filenames
    short_hash = hashlib.blake2b(
        identifier.encode("utf-8"),
        digest_size=3,
    ).hexdigest()
    return f"{slug}_{short_hash}"


class LearningPackageZipper:
    """
    A class to handle the zipping of learning content for backup and restore.
    """

    def __init__(
            self,
            learning_package: LearningPackage,
            user: UserType | None = None,
            origin_server: str | None = None):
        """
        Initialize the LearningPackageZipper.

        Args:
            learning_package (LearningPackage): The learning package to zip.
            user (UserType | None): The user initiating the backup.
            origin_server (str | None): The origin server for the backup.
        """
        self.learning_package = learning_package
        self.user = user
        self.origin_server = origin_server
        self.folders_already_created: set[Path] = set()
        self.entities_filenames_already_created: set[str] = set()
        self.utc_now = datetime.now(tz=timezone.utc)

    def _ensure_parent_folders(
        self,
        zip_file: zipfile.ZipFile,
        path: Path,
        timestamp: datetime,
    ) -> None:
        """
        Ensure all parent folders for the given path exist in the zip.
        """
        for parent in path.parents[::-1]:
            if parent != Path(".") and parent not in self.folders_already_created:
                folder_info = zipfile.ZipInfo(str(parent) + "/")
                folder_info.date_time = timestamp.timetuple()[:6]
                zip_file.writestr(folder_info, "")
                self.folders_already_created.add(parent)

    def add_folder_to_zip(
        self,
        zip_file: zipfile.ZipFile,
        folder: Path,
        timestamp: datetime | None = None,
    ) -> None:
        """
        Explicitly add an empty folder into the zip structure.
        """
        if folder in self.folders_already_created:
            return

        if timestamp is None:
            timestamp = self.utc_now

        self._ensure_parent_folders(zip_file, folder, timestamp)

        folder_info = zipfile.ZipInfo(str(folder) + "/")
        folder_info.date_time = timestamp.timetuple()[:6]
        zip_file.writestr(folder_info, "")
        self.folders_already_created.add(folder)

    def add_file_to_zip(
        self,
        zip_file: zipfile.ZipFile,
        file_path: Path,
        content: bytes | str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """
        Add a file into the zip structure.
        """
        if timestamp is None:
            timestamp = self.utc_now

        self._ensure_parent_folders(zip_file, file_path, timestamp)

        file_info = zipfile.ZipInfo(str(file_path))
        file_info.date_time = timestamp.timetuple()[:6]

        if isinstance(content, str):
            content = content.encode("utf-8")

        zip_file.writestr(file_info, content or b"")

    def get_publishable_entities(self) -> QuerySet[PublishableEntity]:
        """
        Retrieve the publishable entities associated with the learning package.
        Prefetches related data for efficiency.
        """
        lp_id = self.learning_package.id
        publishable_entities: QuerySet[PublishableEntity] = publishing_api.get_publishable_entities(lp_id)
        return (
            publishable_entities.select_related(  # type: ignore[no-redef]
                "asset",
                "container",
                "component__component_type",
                "draft__version__componentversion",
                "published__version__componentversion",
            )
            .prefetch_related(
                # We should re-evaluate the prefetching strategy here,
                # as the current approach may cause performance issues—
                # especially with large libraries (up to 100K items),
                # which is too large for this type of prefetch.
                Prefetch(
                    "draft__version__componentversion__componentversionmedia_set",
                    queryset=ComponentVersionMedia.objects.select_related("media"),
                    to_attr="prefetched_media",
                ),
                Prefetch(
                    "published__version__componentversion__componentversionmedia_set",
                    queryset=ComponentVersionMedia.objects.select_related("media"),
                    to_attr="prefetched_media",
                ),
            )
            .order_by("entity_ref")
        )

    def get_collections(self) -> QuerySet[Collection]:
        """
        Get the collections associated with the learning package.
        """
        return (
            collections_api.get_collections(self.learning_package.id)
            .prefetch_related("entities")
        )

    def get_versions_to_write(
            self, entity: PublishableEntity
        ) -> Tuple[List[PublishableEntityVersion],
                   Optional[PublishableEntityVersion],
                   Optional[PublishableEntityVersion]]:
        """
        Get the versions of a publishable entity that should be written to the zip file.
        It retrieves both draft and published versions.

        Returns:
            Tuple containing:
            - versions_to_write: List of PublishableEntityVersion to write.
            - draft_version: The current draft version, if any.
            - published_version: The current published version, if any.
        """
        draft_version: Optional[PublishableEntityVersion] = publishing_api.get_draft_version(entity)
        published_version: Optional[PublishableEntityVersion] = publishing_api.get_published_version(entity)

        versions_to_write = [draft_version] if draft_version else []

        if published_version and published_version != draft_version:
            versions_to_write.append(published_version)
        return versions_to_write, draft_version, published_version

    def get_entity_toml_filename(self, entity_ref: str) -> str:
        """
        Generate a unique TOML filename for a publishable entity.
        Ensures that the filename is unique within the zip file.

        Behavior:
        - If the slugified ref has not been used yet, use it as the filename.
        - If it has been used, append a short hash to ensure uniqueness.

        Args:
            entity_ref (str): The entity_ref of the publishable entity.

        Returns:
            str: A unique TOML filename for the entity.
        """
        slugify_name = slugify(entity_ref, allow_unicode=True)

        if slugify_name in self.entities_filenames_already_created:
            filename = slugify_hashed_filename(entity_ref)
        else:
            filename = slugify_name

        self.entities_filenames_already_created.add(slugify_name)
        return filename

    def get_latest_modified(self, versions_to_check: List[PublishableEntityVersion]) -> datetime:
        """
        Get the latest modification timestamp among the learning package and its entities.
        """
        latest = self.learning_package.updated
        for version in versions_to_check:
            if version and version.created > latest:
                latest = version.created
        return latest

    def create_zip(self, path: str) -> None:
        """
        Creates a zip file containing the learning package data.
        Args:
            path (str): The path where the zip file will be created.
        Raises:
            Exception: If the learning package cannot be found or if the zip creation fails.
        """

        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
            # Add the package.toml file
            package_toml_content: str = toml_learning_package(
                self.learning_package, self.utc_now, user=self.user, origin_server=self.origin_server
            )
            self.add_file_to_zip(zipf, Path(TOML_PACKAGE_NAME), package_toml_content, self.learning_package.updated)

            # Add the entities directory
            entities_folder = Path("entities")
            self.add_folder_to_zip(zipf, entities_folder, timestamp=self.learning_package.updated)

            # Add the collections directory
            collections_folder = Path("collections")
            self.add_folder_to_zip(zipf, collections_folder, timestamp=self.learning_package.updated)

            # ------ ENTITIES SERIALIZATION -------------

            # get the publishable entities
            publishable_entities: QuerySet[PublishableEntity] = self.get_publishable_entities()

            for entity in publishable_entities:
                # entity: PublishableEntity = entity  # Type hint for clarity

                # Get the versions to serialize for this entity
                versions_to_write, draft_version, published_version = self.get_versions_to_write(entity)

                latest_modified = self.get_latest_modified(versions_to_write)

                # Create a TOML representation of the entity
                entity_toml_content: str = toml_publishable_entity(
                    entity, versions_to_write, draft_version, published_version
                )

                if hasattr(entity, "container"):
                    entity_filename = self.get_entity_toml_filename(entity.entity_ref)
                    entity_toml_filename = f"{entity_filename}.toml"
                    entity_toml_path = entities_folder / entity_toml_filename
                    self.add_file_to_zip(zipf, entity_toml_path, entity_toml_content, timestamp=latest_modified)

                if hasattr(entity, "component"):
                    # Create the component folder structure for the entity. The structure is as follows:
                    # entities/
                    #     xblock.v1/  (component namespace)
                    #         html/  (component type)
                    #             my_component.toml  (entity TOML file)
                    #             my_component/  (component id)
                    #                 component_versions/
                    #                     v1/
                    #                         static/

                    entity_filename = self.get_entity_toml_filename(entity.component.component_code)

                    component_root_folder = (
                        # Example: "entities/xblock.v1/html/"
                        entities_folder
                        / entity.component.component_type.namespace
                        / entity.component.component_type.name
                    )

                    component_folder = (
                        # Example: "entities/xblock.v1/html/my_component_123456/"
                        component_root_folder
                        / entity_filename
                    )

                    component_version_folder = (
                        # Example: "entities/xblock.v1/html/my_component_123456/component_versions/"
                        component_folder
                        / "component_versions"
                    )

                    # Add the entity TOML file inside the component type folder as well
                    # Example: "entities/xblock.v1/html/my_component_123456.toml"
                    component_entity_toml_path = component_root_folder / f"{entity_filename}.toml"
                    self.add_file_to_zip(zipf, component_entity_toml_path, entity_toml_content, latest_modified)

                    # ------ COMPONENT VERSIONING -------------
                    # Focusing on draft and published versions only
                    for version in versions_to_write:
                        # Create a folder for the version
                        version_number = f"v{version.version_num}"
                        version_folder = component_version_folder / version_number
                        self.add_folder_to_zip(zipf, version_folder, timestamp=version.created)

                        # Add static folder for the version
                        static_folder = version_folder / "static"
                        self.add_folder_to_zip(zipf, static_folder, timestamp=version.created)

                        # ------ COMPONENT STATIC CONTENT -------------
                        component_version: ComponentVersion = version.componentversion

                        # Get media data associated with this version
                        prefetched_media: QuerySet[
                            ComponentVersionMedia
                        ] = component_version.prefetched_media  # type: ignore[attr-defined]

                        for component_version_media in prefetched_media:
                            media: Media = component_version_media.media

                            # component_version_media.path is the file name and extension
                            file_path = version_folder / component_version_media.path

                            if media.has_file and media.path:
                                # If has_file, we pull it from the file system
                                with media.read_file() as f:
                                    file_data = f.read()
                            elif not media.has_file and media.text:
                                # Otherwise, we use the text media as the file data
                                file_data = media.text
                            else:
                                # If no file and no text, we skip this media
                                continue
                            self.add_file_to_zip(zipf, file_path, file_data, timestamp=media.created)

                if hasattr(entity, "asset"):
                    # As shared assets are new, we're holding off on defining the serialization format for them.
                    # But we can implement this whenever it becomes needed (e.g. when libraries support assets)
                    raise NotImplementedError("Packages with assets cannot yet be imported/exported.")

            # ------ COLLECTION SERIALIZATION -------------
            collections = self.get_collections()

            for collection in collections:
                collection_hash_slug = self.get_entity_toml_filename(collection.collection_code)
                collection_toml_file_path = collections_folder / f"{collection_hash_slug}.toml"
                entity_refs_related = collection.entities.order_by("entity_ref").values_list("entity_ref", flat=True)
                self.add_file_to_zip(
                    zipf,
                    collection_toml_file_path,
                    toml_collection(collection, list(entity_refs_related)),
                    timestamp=collection.modified,
                )


@dataclass
class RestoreLearningPackageData:
    """
    Data about the restored learning package.
    """
    id: int  # The ID of the restored learning package
    package_ref: str  # The package_ref of the restored learning package (may be different if staged)
    archive_package_ref: str  # The original package_ref from the archive
    archive_org_code: str | None  # The org code parsed from archive_package_ref, or None if unparseable
    archive_package_code: str | None  # The package code parsed from archive_package_ref, or None if unparseable
    title: str
    num_containers: int
    num_sections: int
    num_subsections: int
    num_units: int
    num_components: int
    num_collections: int


@dataclass
class BackupMetadata:
    """
    Metadata about the backup operation.
    """
    format_version: int
    created_at: str
    created_by: str | None = None
    created_by_email: str | None = None
    original_server: str | None = None


@dataclass
class RestoreResult:
    """
    Result of the restore operation.
    """
    status: Literal["success", "error"]
    log_file_error: StringIO | None = None
    lp_restored_data: RestoreLearningPackageData | None = None
    backup_metadata: BackupMetadata | None = None


def unpack_package_ref(package_ref: str) -> tuple[str | None, str | None]:
    """
    Try to parse org_code and package_code from a package_ref.

    By convention, package_refs take the form ``"{prefix}:{org_code}:{package_code}"``,
    but this is only a convention — package_ref is opaque and the parse may fail.
    Returns ``(None, None)`` if the ref does not match the expected format.
    """
    parts = package_ref.split(":")
    if len(parts) < 3:
        return None, None
    _, org_code, package_code = parts[:3]
    return org_code, package_code


def generate_staged_package_ref(archive_package_ref: str, user: UserType) -> str:
    """
    Generate a staged learning package ref based on the archive's package_ref.

    We can't trust package_ref from the archive directly, because the archive
    could specify *any* arbitrary package_ref, and the user may or may not be
    permitted to create an Package using that ref. So, instead, this function
    generates a unique and semi-human-readable package_ref which is namespaced
    to the current user and appropriate to provisionally save the package under.
    The package_ref from the archive can then be presented to the user as a
    *suggestion*, which they may or may not choose to use.

    Please note that the ref returned by this function is valid for Packages is
    a generic sense, but it's not a valid Content Library key.  Callers who are
    restoring a Package for Library usage will need to replace this staged
    package_ref before being able to render the Library's content.

    Arguments:
        archive_package_ref (str): The original package_ref from the archive.
        user (UserType | None): The user performing the restore operation.

    Example:
        Input:  "lib:WGU:LIB_C001"
        Output: "lp-restore:dave:WGU:LIB_C001:1728575321"
    """
    username = user.username
    org_code, package_code = unpack_package_ref(archive_package_ref)
    timestamp = int(time.time() * 1000)  # Current time in milliseconds
    if org_code and package_code:
        return f"lp-restore:{username}:{org_code}:{package_code}:{timestamp}"
    # Fallback for non-conventional package_refs
    return f"lp-restore:{username}:{archive_package_ref}:{timestamp}"


class LearningPackageUnzipper:
    """
    Handles extraction and restoration of learning package data from a zip archive.

    Args:
        zipf (zipfile.ZipFile): The zip file containing the learning package data.
        user (UserType | None): The user performing the restore operation. Not necessarily the creator.
        generate_new_key (bool): Whether to generate a new key for the restored learning package.

    Returns:
        dict[str, Any]: The result of the restore operation, including any errors encountered.

    Responsibilities:
      - Parse and organize files from the zip structure.
      - Restore learning package, containers, components, and collections to the database.
      - Ensure atomicity of the restore process.

    Usage:
        unzipper = LearningPackageUnzipper(zip_file)
        result = unzipper.load()
    """

    def __init__(self, zipf: zipfile.ZipFile, package_ref: str | None = None, user: UserType | None = None):
        self.zipf = zipf
        self.user = user
        self.user_id = getattr(self.user, "id", None)
        self.package_ref = package_ref  # If provided, use this package_ref for the restored learning package
        self.learning_package_id: LearningPackage.ID | None = None  # Will be set upon restoration
        self.utc_now: datetime = datetime.now(timezone.utc)
        self.component_types_cache: dict[tuple[str, str], ComponentType] = {}
        self.errors: list[dict[str, Any]] = []
        # Maps for resolving relationships
        self.components_map_by_ref: dict[str, Any] = {}
        self.units_map_by_ref: dict[str, Any] = {}
        self.subsections_map_by_ref: dict[str, Any] = {}
        self.sections_map_by_ref: dict[str, Any] = {}
        self.all_publishable_entity_refs: set[str] = set()
        self.all_published_entities_versions: set[tuple[str, int]] = set()  # To track published entity versions

    # --------------------------
    # Public API
    # --------------------------

    @transaction.atomic
    def load(self) -> dict[str, Any]:
        """Extracts and restores all objects from the ZIP archive in an atomic transaction."""

        # Step 1: Validate presence of package.toml and basic structure
        _, organized_files = self.check_mandatory_files()
        if self.errors:
            # Early return if preliminary checks fail since mandatory files are missing
            result = RestoreResult(
                status="error",
                log_file_error=self._write_errors(),  # return a StringIO with the errors
                lp_restored_data=None,
                backup_metadata=None,
            )
            return asdict(result)

        # Step 2: Extract and validate learning package, entities and collections
        # Errors are collected and reported at the end
        # No saving to DB happens until all validations pass
        learning_package_validated = self._extract_learning_package(organized_files["learning_package"])
        lp_metadata = learning_package_validated.pop("metadata", {})

        components_validated = self._extract_entities(
            organized_files["components"], ComponentSerializer, ComponentVersionSerializer
        )
        containers_validated = self._extract_entities(
            organized_files["containers"], ContainerSerializer, ContainerVersionSerializer
        )

        collections_validated = self._extract_collections(
            organized_files["collections"]
        )

        # Step 3.1: If there are validation errors, return them without saving anything
        if self.errors:
            result = RestoreResult(
                status="error",
                log_file_error=self._write_errors(),  # return a StringIO with the errors
                lp_restored_data=None,
                backup_metadata=None,
            )
            return asdict(result)

        # Step 3.2: Save everything to the DB
        # All validations passed, we can proceed to save everything
        # Save the learning package first to get its ID
        archive_package_ref = learning_package_validated["package_ref"]
        learning_package = self._save(
            learning_package_validated,
            components_validated,
            containers_validated,
            collections_validated,
            component_static_files=organized_files["component_static_files"]
        )

        num_containers = sum(
            len(containers_validated.get(container_type, []))
            for container_type in ["section", "subsection", "unit"]
        )

        org_code, package_code = unpack_package_ref(archive_package_ref)
        result = RestoreResult(
            status="success",
            log_file_error=None,
            lp_restored_data=RestoreLearningPackageData(
                id=learning_package.id,
                package_ref=learning_package.package_ref,
                archive_package_ref=archive_package_ref,
                archive_org_code=org_code,
                archive_package_code=package_code,
                title=learning_package.title,
                num_containers=num_containers,
                num_sections=len(containers_validated.get("section", [])),
                num_subsections=len(containers_validated.get("subsection", [])),
                num_units=len(containers_validated.get("unit", [])),
                num_components=len(components_validated["components"]),
                num_collections=len(collections_validated["collections"]),
            ),
            backup_metadata=BackupMetadata(
                format_version=lp_metadata.get("format_version", 1),
                created_by=lp_metadata.get("created_by"),
                created_by_email=lp_metadata.get("created_by_email"),
                created_at=lp_metadata.get("created_at"),
                original_server=lp_metadata.get("origin_server"),
            ) if lp_metadata else None,
        )
        return asdict(result)

    def check_mandatory_files(self) -> Tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        Check for the presence of mandatory files in the zip archive.
        So far, the only mandatory file is package.toml.
        """
        organized_files = self._get_organized_file_list(self.zipf.namelist())

        if not organized_files["learning_package"]:
            self.errors.append({"file": TOML_PACKAGE_NAME, "errors": "Missing learning package file."})

        return self.errors, organized_files

    # --------------------------
    # Extract + Validate
    # --------------------------

    def _extract_learning_package(self, package_file: str) -> dict[str, Any]:
        """Extract and validate the learning package TOML file."""
        toml_content_text = self._read_file_from_zip(package_file)
        toml_content_dict = parse_learning_package_toml(toml_content_text)
        lp = toml_content_dict.get("learning_package")
        lp_metadata = toml_content_dict.get("meta")

        # Validate learning package data
        lp_serializer = LearningPackageSerializer(data=lp)
        if not lp_serializer.is_valid():
            self.errors.append({"file": f"{package_file} learning package section", "errors": lp_serializer.errors})

        # Validate metadata if present
        lp_metadata_serializer = LearningPackageMetadataSerializer(data=lp_metadata)
        if not lp_metadata_serializer.is_valid():
            self.errors.append({"file": f"{package_file} meta section", "errors": lp_metadata_serializer.errors})

        lp_validated = lp_serializer.validated_data if lp_serializer.is_valid() else {}
        lp_metadata = lp_metadata_serializer.validated_data if lp_metadata_serializer.is_valid() else {}
        lp_validated["metadata"] = lp_metadata
        return lp_validated

    def _extract_entities(
        self,
        entity_files: list[str],
        entity_serializer: type[serializers.Serializer],
        version_serializer: type[serializers.Serializer],
    ) -> dict[str, Any]:
        """Generic extraction + validation pipeline for containers or components."""
        results: dict[str, list[Any]] = defaultdict(list)

        for file in entity_files:
            if not file.endswith(".toml"):
                # Skip non-TOML files
                continue

            entity_data, draft_version, published_version = self._load_entity_data(file)
            serializer = entity_serializer(
                data={"created": self.utc_now, "created_by": None, **entity_data}
            )

            if not serializer.is_valid():
                self.errors.append({"file": file, "errors": serializer.errors})
                continue

            entity_data = serializer.validated_data
            self.all_publishable_entity_refs.add(entity_data["entity_ref"])
            entity_type = entity_data.pop("container_type", "components")
            results[entity_type].append(entity_data)

            valid_versions = self._validate_versions(
                entity_data,
                draft_version,
                published_version,
                version_serializer,
                file=file
            )
            if valid_versions["draft"]:
                results[f"{entity_type}_drafts"].append(valid_versions["draft"])
            if valid_versions["published"]:
                results[f"{entity_type}_published"].append(valid_versions["published"])

        return results

    def _extract_collections(
        self,
        collection_files: list[str],
    ) -> dict[str, Any]:
        """Extraction + validation pipeline for collections."""
        results: dict[str, list[Any]] = defaultdict(list)

        for file in collection_files:
            if not file.endswith(".toml"):
                # Skip non-TOML files
                continue
            toml_content = self._read_file_from_zip(file)
            collection_data = parse_collection_toml(toml_content)
            collection_data = collection_data.get("collection", {})
            serializer = CollectionSerializer(data={"created_by": None, **collection_data})
            if not serializer.is_valid():
                self.errors.append({"file": f"{file} collection section", "errors": serializer.errors})
                continue
            collection_validated = serializer.validated_data
            entities_list = collection_validated["entities"]
            for entity_ref in entities_list:
                if entity_ref not in self.all_publishable_entity_refs:
                    self.errors.append({
                        "file": file,
                        "errors": f"Entity ref {entity_ref} not found for collection {collection_validated.get('key')}"
                    })
            results["collections"].append(collection_validated)

        return results

    # --------------------------
    # Save Logic
    # --------------------------

    def _save(
        self,
        learning_package: dict[str, Any],
        components: dict[str, Any],
        containers: dict[str, Any],
        collections: dict[str, Any],
        *,
        component_static_files: dict[str, List[str]]
    ) -> LearningPackage:
        """Persist all validated entities in two phases: published then drafts."""

        # Important: If not using a specific LP ref/key, generate a temporary one
        # We cannot use the original key because it may generate security issues
        if not self.package_ref:
            # Generate a tmp ref for the staged learning package
            if not self.user:
                raise ValueError("User is required to generate a staged package_ref")
            learning_package["package_ref"] = generate_staged_package_ref(
                archive_package_ref=learning_package["package_ref"],
                user=self.user
            )
        else:
            learning_package["package_ref"] = self.package_ref

        learning_package_obj = publishing_api.create_learning_package(**learning_package)
        self.learning_package_id = learning_package_obj.id

        with publishing_api.bulk_draft_changes_for(learning_package_obj.id):
            self._save_components(learning_package_obj, components, component_static_files)
            self._save_units(learning_package_obj, containers)
            self._save_subsections(learning_package_obj, containers)
            self._save_sections(learning_package_obj, containers)
            self._save_collections(learning_package_obj, collections)
        publishing_api.publish_all_drafts(learning_package_obj.id)

        with publishing_api.bulk_draft_changes_for(learning_package_obj.id):
            self._save_draft_versions(components, containers, component_static_files)

        return learning_package_obj

    def _save_collections(self, learning_package, collections):
        """Save collections and their entities."""
        for valid_collection in collections.get("collections", []):
            entities = valid_collection.pop("entities", [])
            collection = collections_api.create_collection(
                learning_package.id, created_by=self.user_id, **valid_collection
            )
            collection = collections_api.add_to_collection(
                learning_package_id=learning_package.id,
                collection_code=collection.collection_code,
                entities_qset=publishing_api.get_publishable_entities(learning_package.id).filter(
                    entity_ref__in=entities
                )
            )

    def _save_components(self, learning_package, components, component_static_files):
        """Save components and published component versions."""
        for valid_component in components.get("components", []):
            entity_ref = valid_component.pop("entity_ref")
            component = components_api.create_component(learning_package.id, created_by=self.user_id, **valid_component)
            self.components_map_by_ref[entity_ref] = component

        for valid_published in components.get("components_published", []):
            entity_ref = valid_published.pop("entity_ref")
            version_num = valid_published["version_num"]  # Should exist, validated earlier
            component = self.components_map_by_ref[entity_ref]
            media_to_replace = self._resolve_static_files(
                version_num, entity_ref, component.component_type, component_static_files
            )
            self.all_published_entities_versions.add(
                (entity_ref, version_num)
            )  # Track published version
            components_api.create_next_component_version(
                component.publishable_entity.id,
                media_to_replace=media_to_replace,
                force_version_num=valid_published.pop("version_num", None),
                created_by=self.user_id,
                **valid_published
            )

    def _save_container(
        self,
        learning_package,
        containers,
        *,
        container_cls: containers_api.ContainerSubclass,
        container_map: dict,
        children_map: dict,
    ):
        """Internal logic for _save_units, _save_subsections, and _save_sections"""
        type_code = container_cls.type_code  # e.g. "unit"
        for data in containers.get(type_code, []):
            entity_ref = data.pop("entity_ref")
            container = containers_api.create_container(
                learning_package.id,
                # As of Verawood, the primary identity of a container is its
                # `container_code`.  By convention, this equals the `entity_ref`
                # (aka `[entity].key`). It's safe to assume that all v1
                # archives have an identical `entity_ref` and `container_code` for each
                # entity-container. BUT, this assumpion may not hold true v2+.
                container_code=entity_ref,
                **data,  # should this be allowed to override any of the following fields?
                created_by=self.user_id,
                container_cls=container_cls,
            )
            container_map[entity_ref] = container  # e.g. `self.units_map_by_ref[entity_ref] = unit`

        for valid_published in containers.get(f"{type_code}_published", []):
            entity_ref = valid_published.pop("entity_ref")
            children = self._resolve_children(valid_published, children_map)
            version_num = valid_published.pop("version_num", None)
            self.all_published_entities_versions.add((entity_ref, version_num))
            containers_api.create_next_container_version(
                container_map[entity_ref],
                force_version_num=version_num,
                **valid_published,  # should this be allowed to override any of the following fields?
                entities=children,
                created_by=self.user_id,
            )

    def _save_units(self, learning_package, containers):
        """Save units and published unit versions."""
        self._save_container(
            learning_package,
            containers,
            container_cls=Unit,
            container_map=self.units_map_by_ref,
            children_map=self.components_map_by_ref,
        )

    def _save_subsections(self, learning_package, containers):
        """Save subsections and published subsection versions."""
        self._save_container(
            learning_package,
            containers,
            container_cls=Subsection,
            container_map=self.subsections_map_by_ref,
            children_map=self.units_map_by_ref,
        )

    def _save_sections(self, learning_package, containers):
        """Save sections and published section versions."""
        self._save_container(
            learning_package,
            containers,
            container_cls=Section,
            container_map=self.sections_map_by_ref,
            children_map=self.subsections_map_by_ref,
        )

    def _save_draft_versions(self, components, containers, component_static_files):
        """Save draft versions for all entity types."""
        for valid_draft in components.get("components_drafts", []):
            entity_ref = valid_draft.pop("entity_ref")
            version_num = valid_draft["version_num"]  # Should exist, validated earlier
            if self._is_version_already_exists(entity_ref, version_num):
                continue
            component = self.components_map_by_ref[entity_ref]
            media_to_replace = self._resolve_static_files(
                version_num, entity_ref, component.component_type, component_static_files
            )
            components_api.create_next_component_version(
                component.publishable_entity.id,
                media_to_replace=media_to_replace,
                force_version_num=valid_draft.pop("version_num", None),
                # Drafts can diverge from published, so we allow ignoring previous media
                # Use case: published v1 had files A, B; draft v2 only has file A
                ignore_previous_media=True,
                created_by=self.user_id,
                **valid_draft
            )

        def _process_draft_containers(
            container_cls: containers_api.ContainerSubclass,
            container_map: dict,
            children_map: dict,
        ):
            for valid_draft in containers.get(f"{container_cls.type_code}_drafts", []):
                entity_ref = valid_draft.pop("entity_ref")
                version_num = valid_draft["version_num"]  # Should exist, validated earlier
                if self._is_version_already_exists(entity_ref, version_num):
                    continue
                children = self._resolve_children(valid_draft, children_map)
                del valid_draft["version_num"]
                containers_api.create_next_container_version(
                    container_map[entity_ref],
                    **valid_draft,  # should this be allowed to override any of the following fields?
                    entities=children,
                    force_version_num=version_num,
                    created_by=self.user_id,
                )

        _process_draft_containers(Unit, self.units_map_by_ref, children_map=self.components_map_by_ref)
        _process_draft_containers(Subsection, self.subsections_map_by_ref, children_map=self.units_map_by_ref)
        _process_draft_containers(Section, self.sections_map_by_ref, children_map=self.subsections_map_by_ref)

    # --------------------------
    # Utilities
    # --------------------------

    def _format_errors(self) -> str:
        """Return formatted error content as a string."""
        if not self.errors:
            return ""
        lines = [f"{err['file']}: {err['errors']}" for err in self.errors]
        return "Errors encountered during restore:\n" + "\n".join(lines) + "\n"

    def _write_errors(self) -> StringIO | None:
        """
        Write errors to a StringIO buffer.
        """
        content = self._format_errors()
        if not content:
            return None
        return StringIO(content)

    def _is_version_already_exists(self, entity_ref: str, version_num: int) -> bool:
        """
        Check if a version already exists for a given entity_ref and version number.

        Note:
            Skip creating draft if this version is already published
            Why? Because the version itself is already created and
            we don't want to create duplicate versions.
            Otherwise, we will raise an IntegrityError on PublishableEntityVersion
            due to unique constraints between publishable_entity and version_num.
        """
        identifier = (entity_ref, version_num)
        return identifier in self.all_published_entities_versions

    def _resolve_static_files(
            self,
            num_version: int,
            entity_ref: str,
            component_type: ComponentType,
            static_files_map: dict[str, List[str]]
    ) -> dict[str, bytes | int]:
        """Resolve static file paths into their binary media content."""
        resolved_files: dict[str, bytes | int] = {}

        static_file_key = f"{entity_ref}:v{num_version}"  # e.g., "xblock.v1:html:my_component_123456:v1"
        block_type = component_type.name  # e.g., "html"
        static_files = static_files_map.get(static_file_key, [])
        for static_file in static_files:
            local_key = static_file.split(f"v{num_version}/")[-1]
            with self.zipf.open(static_file, "r") as f:
                media_bytes = f.read()
            if local_key == "block.xml":
                # Special handling for block.xml to ensure
                # storing the value as a media instance
                if not self.learning_package_id:
                    raise ValueError("learning_package_id must be set before resolving static files.")
                text_media = media_api.get_or_create_text_media(
                    self.learning_package_id,
                    media_api.get_or_create_media_type(f"application/vnd.openedx.xblock.v1.{block_type}+xml").id,
                    text=media_bytes.decode("utf-8"),
                    created=self.utc_now,
                )
                resolved_files[local_key] = text_media.id
            else:
                resolved_files[local_key] = media_bytes
        return resolved_files

    def _resolve_children(self, entity_data: dict[str, Any], lookup_map: dict[str, Any]) -> list[Any]:
        """Resolve child entity refs into model instances."""
        children_refs = entity_data.pop("children", [])
        return [lookup_map[ref] for ref in children_refs if ref in lookup_map]

    def _load_entity_data(
        self, entity_file: str
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        """Load entity data and its versions from TOML."""
        entity_toml_txt = self._read_file_from_zip(entity_file)
        entity_toml_dict = parse_publishable_entity_toml(entity_toml_txt)
        entity_data = entity_toml_dict.get("entity", {})
        version_data = entity_toml_dict.get("version", [])
        return entity_data, *self._get_versions_to_write(version_data, entity_data)

    def _validate_versions(self, entity_data, draft, published, serializer_cls, *, file) -> dict[str, Any]:
        """Validate draft/published versions with serializer."""
        valid = {"draft": None, "published": None}
        for label, version in [("draft", draft), ("published", published)]:
            if not version:
                continue
            serializer = serializer_cls(
                data={
                    "entity_ref": entity_data["entity_ref"],
                    "created": self.utc_now,
                    "created_by": None,
                    **version
                }
            )
            if serializer.is_valid():
                valid[label] = serializer.validated_data
            else:
                self.errors.append({"file": file, "errors": serializer.errors})
        return valid

    def _read_file_from_zip(self, filename: str) -> str:
        """Read and decode a UTF-8 file from the zip archive."""
        with self.zipf.open(filename) as f:
            return f.read().decode("utf-8")

    def _get_organized_file_list(self, file_paths: list[str]) -> dict[str, Any]:
        """Organize file paths into categories: learning_package, containers, components, collections."""
        organized: dict[str, Any] = {
            "learning_package": None,
            "containers": [],
            "components": [],
            "component_static_files": defaultdict(list),
            "collections": [],
        }

        # This is going to map static file directory roots to the appropriate
        # entity refs.
        comp_paths_to_refs = {}

        # The ordering of the file processing is important because we need to
        # ensure that TOML files for a given component are processed before the
        # static files for that component. '.' sorts before '/', so "foo.toml"
        # will sort before "foo/component_versions/v1/static/figure1.webp" or
        # any other subdirectory of the "foo" component. Processing the TOML
        # first allows us to map the directory to a entity ref.
        for path in sorted(file_paths):
            if path.endswith("/"):
                # Skip directories
                continue
            if path == TOML_PACKAGE_NAME:
                organized["learning_package"] = path
            elif path.startswith("entities/") and str(Path(path).parent) == "entities" and path.endswith(".toml"):
                # Top-level entity TOML files are considered containers
                organized["containers"].append(path)
            elif path.startswith("entities/"):
                if path.endswith(".toml"):
                    # Component entity TOML files
                    organized["components"].append(path)
                    component_toml_str = self._read_file_from_zip(path)
                    component_toml = tomllib.loads(component_toml_str)
                    entity_ref = component_toml['entity']['key']
                    comp_path = path.removesuffix(".toml")

                    # This maps the root path of a component, e.g."entities/xblock.v1/html/my_component_a822bb"
                    # to the actual ref, e.g. "xblock.v1:html:my_component". The last part of the ref  will
                    # often correlate to the directory name, but does not have to (a hash is sometimes added).
                    comp_paths_to_refs[comp_path] = entity_ref

                else:
                    # Component static files
                    # Path structure: entities/<namespace>/<type>/<component_id>/component_versions/<version>/static/...
                    # Example: entities/xblock.v1/html/my_component_a822bb/component_versions/v1/static/...

                    # e.g. 'entities/xblock.v1/html/my_component_a822bb'
                    component_root_path = '/'.join(Path(path).parts[0:4])

                    try:
                        component_ref = comp_paths_to_refs[component_root_path]
                    except KeyError:
                        self.errors.append(
                            {
                                "file": path,
                                "errors": f"Missing component TOML file at {component_root_path}.toml"
                            }
                        )
                        continue

                    num_version = Path(path).parts[5] if len(Path(path).parts) > 5 else "v1"  # e.g., 'v1'

                    component_ref += f":{num_version}"
                    organized["component_static_files"][component_ref].append(path)

            elif path.startswith("collections/") and path.endswith(".toml"):
                # Collection TOML files
                organized["collections"].append(path)

        return organized

    def _get_versions_to_write(
        self,
        version_data: list[dict[str, Any]],
        entity_data: dict[str, Any]
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """Return the draft and published versions to write, based on entity data."""
        draft_num = entity_data.get("draft", {}).get("version_num")
        published_num = entity_data.get("published", {}).get("version_num")
        lookup = {v.get("version_num"): v for v in version_data}
        return (
            lookup.get(draft_num) if draft_num else None,
            lookup.get(published_num) if published_num else None,
        )
