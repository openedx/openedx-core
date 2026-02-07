"""
This module provides functionality to create a zip file containing the learning package data,
including a TOML representation of the learning package and its entities.
"""
import hashlib
import time
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
    ComponentVersionContent,
    Content,
    LearningPackage,
    PublishableEntity,
    PublishableEntityVersion,
)

from ..collections import api as collections_api
from ..components import api as components_api
from ..contents import api as contents_api
from ..publishing import api as publishing_api
from ..sections import api as sections_api
from ..subsections import api as subsections_api
from ..units import api as units_api
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
        lp_id = self.learning_package.pk
        publishable_entities: QuerySet[PublishableEntity] = publishing_api.get_publishable_entities(lp_id)
        return (
            publishable_entities  # type: ignore[no-redef]
            .select_related(
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
                    "draft__version__componentversion__componentversioncontent_set",
                    queryset=ComponentVersionContent.objects.select_related("content"),
                    to_attr="prefetched_contents",
                ),
                Prefetch(
                    "published__version__componentversion__componentversioncontent_set",
                    queryset=ComponentVersionContent.objects.select_related("content"),
                    to_attr="prefetched_contents",
                ),
            )
            .order_by("key")
        )

    def get_collections(self) -> QuerySet[Collection]:
        """
        Get the collections associated with the learning package.
        """
        return (
            collections_api.get_collections(self.learning_package.pk)
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

    def get_entity_toml_filename(self, entity_key: str) -> str:
        """
        Generate a unique TOML filename for a publishable entity.
        Ensures that the filename is unique within the zip file.

        Behavior:
        - If the slugified key has not been used yet, use it as the filename.
        - If it has been used, append a short hash to ensure uniqueness.

        Args:
            entity_key (str): The key of the publishable entity.

        Returns:
            str: A unique TOML filename for the entity.
        """
        slugify_name = slugify(entity_key, allow_unicode=True)

        if slugify_name in self.entities_filenames_already_created:
            filename = slugify_hashed_filename(entity_key)
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

        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
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

                if hasattr(entity, 'container'):
                    entity_filename = self.get_entity_toml_filename(entity.key)
                    entity_toml_filename = f"{entity_filename}.toml"
                    entity_toml_path = entities_folder / entity_toml_filename
                    self.add_file_to_zip(zipf, entity_toml_path, entity_toml_content, timestamp=latest_modified)

                if hasattr(entity, 'component'):
                    # Create the component folder structure for the entity. The structure is as follows:
                    # entities/
                    #     xblock.v1/  (component namespace)
                    #         html/  (component type)
                    #             my_component.toml  (entity TOML file)
                    #             my_component/  (component id)
                    #                 component_versions/
                    #                     v1/
                    #                         static/

                    entity_filename = self.get_entity_toml_filename(entity.component.local_key)

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

                        # Get content data associated with this version
                        contents: QuerySet[
                            ComponentVersionContent
                        ] = component_version.prefetched_contents  # type: ignore[attr-defined]

                        for component_version_content in contents:
                            content: Content = component_version_content.content

                            # Important: The component_version_content.key contains implicitly
                            # the file name and the file extension
                            file_path = version_folder / component_version_content.key

                            if content.has_file and content.path:
                                # If has_file, we pull it from the file system
                                with content.read_file() as f:
                                    file_data = f.read()
                            elif not content.has_file and content.text:
                                # Otherwise, we use the text content as the file data
                                file_data = content.text
                            else:
                                # If no file and no text, we skip this content
                                continue
                            self.add_file_to_zip(zipf, file_path, file_data, timestamp=content.created)

            # ------ COLLECTION SERIALIZATION -------------
            collections = self.get_collections()

            for collection in collections:
                collection_hash_slug = self.get_entity_toml_filename(collection.key)
                collection_toml_file_path = collections_folder / f"{collection_hash_slug}.toml"
                entity_keys_related = collection.entities.order_by("key").values_list("key", flat=True)
                self.add_file_to_zip(
                    zipf,
                    collection_toml_file_path,
                    toml_collection(collection, list(entity_keys_related)),
                    timestamp=collection.modified,
                )


@dataclass
class RestoreLearningPackageData:
    """
    Data about the restored learning package.
    """
    id: int  # The ID of the restored learning package
    key: str  # The key of the restored learning package (may be different if staged)
    archive_lp_key: str  # The original key from the archive
    archive_org_key: str  # The original organization key from the archive
    archive_slug: str  # The original slug from the archive
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


def unpack_lp_key(lp_key: str) -> tuple[str, str]:
    """
    Unpack a learning package key into its components.
    """
    parts = lp_key.split(":")
    if len(parts) < 3:
        raise ValueError(f"Invalid learning package key: {lp_key}")
    _, org_key, lp_slug = parts[:3]
    return org_key, lp_slug


def generate_staged_lp_key(archive_lp_key: str, user: UserType) -> str:
    """
    Generate a staged learning package key based on the given base key.

    Arguments:
        archive_lp_key (str): The original learning package key from the archive.
        user (UserType | None): The user performing the restore operation.

    Example:
        Input:  "lib:WGU:LIB_C001"
        Output: "lp-restore:dave:WGU:LIB_C001:1728575321"

    The timestamp at the end ensures the key is unique.
    """
    username = user.username
    org_key, lp_slug = unpack_lp_key(archive_lp_key)
    timestamp = int(time.time() * 1000)  # Current time in milliseconds
    return f"lp-restore:{username}:{org_key}:{lp_slug}:{timestamp}"


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

    def __init__(self, zipf: zipfile.ZipFile, key: str | None = None, user: UserType | None = None):
        self.zipf = zipf
        self.user = user
        self.user_id = getattr(self.user, "id", None)
        self.lp_key = key  # If provided, use this key for the restored learning package
        self.learning_package_id: int | None = None  # Will be set upon restoration
        self.utc_now: datetime = datetime.now(timezone.utc)
        self.component_types_cache: dict[tuple[str, str], ComponentType] = {}
        self.errors: list[dict[str, Any]] = []
        # Maps for resolving relationships
        self.components_map_by_key: dict[str, Any] = {}
        self.units_map_by_key: dict[str, Any] = {}
        self.subsections_map_by_key: dict[str, Any] = {}
        self.sections_map_by_key: dict[str, Any] = {}
        self.all_publishable_entities_keys: set[str] = set()
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
        archive_lp_key = learning_package_validated["key"]
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

        org_key, lp_slug = unpack_lp_key(archive_lp_key)
        result = RestoreResult(
            status="success",
            log_file_error=None,
            lp_restored_data=RestoreLearningPackageData(
                id=learning_package.id,
                key=learning_package.key,
                archive_lp_key=archive_lp_key,  # The original key from the backup archive
                archive_org_key=org_key,  # The original organization key from the backup archive
                archive_slug=lp_slug,  # The original slug from the backup archive
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
            self.all_publishable_entities_keys.add(entity_data["key"])
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
            for entity_key in entities_list:
                if entity_key not in self.all_publishable_entities_keys:
                    self.errors.append({
                        "file": file,
                        "errors": f"Entity key {entity_key} not found for collection {collection_validated.get('key')}"
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

        # Important: If not using a specific LP key, generate a temporary one
        # We cannot use the original key because it may generate security issues
        if not self.lp_key:
            # Generate a tmp key for the staged learning package
            if not self.user:
                raise ValueError("User is required to create lp_key")
            learning_package["key"] = generate_staged_lp_key(
                archive_lp_key=learning_package["key"],
                user=self.user
            )
        else:
            learning_package["key"] = self.lp_key

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
                key=collection.key,
                entities_qset=publishing_api.get_publishable_entities(learning_package.id).filter(key__in=entities)
            )

    def _save_components(self, learning_package, components, component_static_files):
        """Save components and published component versions."""
        for valid_component in components.get("components", []):
            entity_key = valid_component.pop("key")
            component = components_api.create_component(learning_package.id, created_by=self.user_id, **valid_component)
            self.components_map_by_key[entity_key] = component

        for valid_published in components.get("components_published", []):
            entity_key = valid_published.pop("entity_key")
            version_num = valid_published["version_num"]  # Should exist, validated earlier
            content_to_replace = self._resolve_static_files(version_num, entity_key, component_static_files)
            self.all_published_entities_versions.add(
                (entity_key, version_num)
            )  # Track published version
            components_api.create_next_component_version(
                self.components_map_by_key[entity_key].publishable_entity.id,
                content_to_replace=content_to_replace,
                force_version_num=valid_published.pop("version_num", None),
                created_by=self.user_id,
                **valid_published
            )

    def _save_units(self, learning_package, containers):
        """Save units and published unit versions."""
        for valid_unit in containers.get("unit", []):
            entity_key = valid_unit.get("key")
            unit = units_api.create_unit(learning_package.id, created_by=self.user_id, **valid_unit)
            self.units_map_by_key[entity_key] = unit

        for valid_published in containers.get("unit_published", []):
            entity_key = valid_published.pop("entity_key")
            children = self._resolve_children(valid_published, self.components_map_by_key)
            self.all_published_entities_versions.add(
                (entity_key, valid_published.get('version_num'))
            )  # Track published version
            units_api.create_next_unit_version(
                self.units_map_by_key[entity_key],
                force_version_num=valid_published.pop("version_num", None),
                components=children,
                created_by=self.user_id,
                **valid_published
            )

    def _save_subsections(self, learning_package, containers):
        """Save subsections and published subsection versions."""
        for valid_subsection in containers.get("subsection", []):
            entity_key = valid_subsection.get("key")
            subsection = subsections_api.create_subsection(
                learning_package.id, created_by=self.user_id, **valid_subsection
            )
            self.subsections_map_by_key[entity_key] = subsection

        for valid_published in containers.get("subsection_published", []):
            entity_key = valid_published.pop("entity_key")
            children = self._resolve_children(valid_published, self.units_map_by_key)
            self.all_published_entities_versions.add(
                (entity_key, valid_published.get('version_num'))
            )  # Track published version
            subsections_api.create_next_subsection_version(
                self.subsections_map_by_key[entity_key],
                units=children,
                force_version_num=valid_published.pop("version_num", None),
                created_by=self.user_id,
                **valid_published
            )

    def _save_sections(self, learning_package, containers):
        """Save sections and published section versions."""
        for valid_section in containers.get("section", []):
            entity_key = valid_section.get("key")
            section = sections_api.create_section(learning_package.id, created_by=self.user_id, **valid_section)
            self.sections_map_by_key[entity_key] = section

        for valid_published in containers.get("section_published", []):
            entity_key = valid_published.pop("entity_key")
            children = self._resolve_children(valid_published, self.subsections_map_by_key)
            self.all_published_entities_versions.add(
                (entity_key, valid_published.get('version_num'))
            )  # Track published version
            sections_api.create_next_section_version(
                self.sections_map_by_key[entity_key],
                subsections=children,
                force_version_num=valid_published.pop("version_num", None),
                created_by=self.user_id,
                **valid_published
            )

    def _save_draft_versions(self, components, containers, component_static_files):
        """Save draft versions for all entity types."""
        for valid_draft in components.get("components_drafts", []):
            entity_key = valid_draft.pop("entity_key")
            version_num = valid_draft["version_num"]  # Should exist, validated earlier
            if self._is_version_already_exists(entity_key, version_num):
                continue
            content_to_replace = self._resolve_static_files(version_num, entity_key, component_static_files)
            components_api.create_next_component_version(
                self.components_map_by_key[entity_key].publishable_entity.id,
                content_to_replace=content_to_replace,
                force_version_num=valid_draft.pop("version_num", None),
                # Drafts can diverge from published, so we allow ignoring previous content
                # Use case: published v1 had files A, B; draft v2 only has file A
                ignore_previous_content=True,
                created_by=self.user_id,
                **valid_draft
            )

        for valid_draft in containers.get("unit_drafts", []):
            entity_key = valid_draft.pop("entity_key")
            version_num = valid_draft["version_num"]  # Should exist, validated earlier
            if self._is_version_already_exists(entity_key, version_num):
                continue
            children = self._resolve_children(valid_draft, self.components_map_by_key)
            units_api.create_next_unit_version(
                self.units_map_by_key[entity_key],
                components=children,
                force_version_num=valid_draft.pop("version_num", None),
                created_by=self.user_id,
                **valid_draft
            )

        for valid_draft in containers.get("subsection_drafts", []):
            entity_key = valid_draft.pop("entity_key")
            version_num = valid_draft["version_num"]  # Should exist, validated earlier
            if self._is_version_already_exists(entity_key, version_num):
                continue
            children = self._resolve_children(valid_draft, self.units_map_by_key)
            subsections_api.create_next_subsection_version(
                self.subsections_map_by_key[entity_key],
                units=children,
                force_version_num=valid_draft.pop("version_num", None),
                created_by=self.user_id,
                **valid_draft
            )

        for valid_draft in containers.get("section_drafts", []):
            entity_key = valid_draft.pop("entity_key")
            version_num = valid_draft["version_num"]  # Should exist, validated earlier
            if self._is_version_already_exists(entity_key, version_num):
                continue
            children = self._resolve_children(valid_draft, self.subsections_map_by_key)
            sections_api.create_next_section_version(
                self.sections_map_by_key[entity_key],
                subsections=children,
                force_version_num=valid_draft.pop("version_num", None),
                created_by=self.user_id,
                **valid_draft
            )

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

    def _is_version_already_exists(self, entity_key: str, version_num: int) -> bool:
        """
        Check if a version already exists for a given entity key and version number.

        Note:
            Skip creating draft if this version is already published
            Why? Because the version itself is already created and
            we don't want to create duplicate versions.
            Otherwise, we will raise an IntegrityError on PublishableEntityVersion
            due to unique constraints between publishable_entity and version_num.
        """
        identifier = (entity_key, version_num)
        return identifier in self.all_published_entities_versions

    def _resolve_static_files(
            self,
            num_version: int,
            entity_key: str,
            static_files_map: dict[str, List[str]]
    ) -> dict[str, bytes | int]:
        """Resolve static file paths into their binary content."""
        resolved_files: dict[str, bytes | int] = {}

        static_file_key = f"{entity_key}:v{num_version}"  # e.g., "xblock.v1:html:my_component_123456:v1"
        block_type = entity_key.split(":")[1]  # e.g., "html"
        static_files = static_files_map.get(static_file_key, [])
        for static_file in static_files:
            local_key = static_file.split(f"v{num_version}/")[-1]
            with self.zipf.open(static_file, "r") as f:
                content_bytes = f.read()
            if local_key == "block.xml":
                # Special handling for block.xml to ensure
                # storing the value as a content instance
                if not self.learning_package_id:
                    raise ValueError("learning_package_id must be set before resolving static files.")
                text_content = contents_api.get_or_create_text_content(
                    self.learning_package_id,
                    contents_api.get_or_create_media_type(f"application/vnd.openedx.xblock.v1.{block_type}+xml").id,
                    text=content_bytes.decode("utf-8"),
                    created=self.utc_now,
                )
                resolved_files[local_key] = text_content.id
            else:
                resolved_files[local_key] = content_bytes
        return resolved_files

    def _resolve_children(self, entity_data: dict[str, Any], lookup_map: dict[str, Any]) -> list[Any]:
        """Resolve child entity keys into model instances."""
        children_keys = entity_data.pop("children", [])
        return [lookup_map[key] for key in children_keys if key in lookup_map]

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
                    "entity_key": entity_data["key"],
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

        for path in file_paths:
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
                else:
                    # Component static files
                    # Path structure: entities/<namespace>/<type>/<component_id>/component_versions/<version>/static/...
                    # Example: entities/xblock.v1/html/my_component_123456/component_versions/v1/static/...
                    component_key = Path(path).parts[1:4]  # e.g., ['xblock.v1', 'html', 'my_component_123456']
                    num_version = Path(path).parts[5] if len(Path(path).parts) > 5 else "v1"  # e.g., 'v1'
                    if len(component_key) == 3:
                        component_identifier = ":".join(component_key)
                        component_identifier += f":{num_version}"
                        organized["component_static_files"][component_identifier].append(path)
                    else:
                        self.errors.append({"file": path, "errors": "Invalid component static file path structure."})
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
