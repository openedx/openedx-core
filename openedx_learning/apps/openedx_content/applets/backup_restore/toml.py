"""
TOML serialization for learning packages and publishable entities.
"""

from datetime import datetime
from typing import Any, Dict

import tomlkit
from django.contrib.auth.models import User as UserType  # pylint: disable=imported-auth-user

from ..collections.models import Collection
from ..publishing import api as publishing_api
from ..publishing.models import PublishableEntity, PublishableEntityVersion
from ..publishing.models.learning_package import LearningPackage


def toml_learning_package(
        learning_package: LearningPackage,
        timestamp: datetime,
        format_version: int = 1,
        user: UserType | None = None,
        origin_server: str | None = None
        ) -> str:
    """
    Create a TOML representation of the learning package.

    The resulting content looks like:
        [meta]
        format_version = 1
        created_by = "dormsbee"
        created_at = 2025-09-03T17:50:59.536190Z
        origin_server = "cms.test"

        [learning_package]
        title = "Components Test Case Learning Package"
        key = "ComponentTestCase-test-key"
        description = "This is a test learning package for components."
        created = 2025-09-03T17:50:59.536190Z
        updated = 2025-09-03T17:50:59.536190Z
    """
    doc = tomlkit.document()

    # Learning package main info
    section = tomlkit.table()
    section.add("title", learning_package.title)
    section.add("key", learning_package.key)
    section.add("description", learning_package.description)
    section.add("created", learning_package.created)
    section.add("updated", learning_package.updated)

    # Learning package metadata
    metadata = tomlkit.table()
    metadata.add("format_version", format_version)
    if user:
        metadata.add("created_by", user.username)
        metadata.add("created_by_email", user.email)
    metadata.add("created_at", timestamp)
    if origin_server:
        metadata.add("origin_server", origin_server)

    doc.add("meta", metadata)
    doc.add("learning_package", section)
    return tomlkit.dumps(doc)


def _get_toml_publishable_entity_table(
        entity: PublishableEntity,
        draft_version: PublishableEntityVersion | None,
        published_version: PublishableEntityVersion | None,
        include_versions: bool = True) -> tomlkit.items.Table:
    """
    Create a TOML representation of a publishable entity.

    The resulting content looks like:
        [entity]
        can_stand_alone = true
        key = "xblock.v1:problem:my_published_example"

        [entity.draft]
        version_num = 2

        [entity.published]
        version_num = 1

        [entity.container.section]

    Note: This function returns a tomlkit.items.Table, which represents
    a string-like TOML fragment rather than a complete TOML document.
    """
    entity_table = tomlkit.table()
    entity_table.add("can_stand_alone", entity.can_stand_alone)
    # Add key since the toml filename doesn't show the real key
    entity_table.add("key", entity.key)
    entity_table.add("created", entity.created)

    if not include_versions:
        return entity_table

    if draft_version:
        draft_table = tomlkit.table()
        draft_table.add("version_num", draft_version.version_num)
        entity_table.add("draft", draft_table)

    published_table = tomlkit.table()
    if published_version:
        published_table.add("version_num", published_version.version_num)
    else:
        published_table.add(tomlkit.comment("unpublished: no published_version_num"))
    entity_table.add("published", published_table)

    if hasattr(entity, "container"):
        container_table = tomlkit.table()
        container_types = ["section", "subsection", "unit"]

        for container_type in container_types:
            if hasattr(entity.container, container_type):
                container_table.add(container_type, tomlkit.table())
                break  # stop after the first match

        entity_table.add("container", container_table)

    return entity_table


def toml_publishable_entity(
        entity: PublishableEntity,
        versions_to_write: list[PublishableEntityVersion],
        draft_version: PublishableEntityVersion | None,
        published_version: PublishableEntityVersion | None) -> str:
    """
    Create a TOML representation of a publishable entity and its versions.

    The resulting content looks like:
        [entity]
        can_stand_alone = true
        key = "xblock.v1:problem:my_published_example"

        [entity.draft]
        version_num = 2

        [entity.published]
        version_num = 1

        [entity.container.section] (if applicable)

        # ### Versions

        [[version]]
        title = "My published problem"
        version_num = 1

        [version.container] (if applicable)
        children = []
    """
    # Create the TOML representation for the entity itself
    entity_table = _get_toml_publishable_entity_table(entity, draft_version, published_version)
    doc = tomlkit.document()
    doc.add("entity", entity_table)

    # Add versions as an array of tables (AoT)
    doc.add(tomlkit.nl())
    doc.add(tomlkit.comment("### Versions"))
    for entity_version in versions_to_write:
        version = tomlkit.aot()
        version_table = toml_publishable_entity_version(entity_version)
        version.append(version_table)
        doc.add("version", version)

    return tomlkit.dumps(doc)


def toml_publishable_entity_version(version: PublishableEntityVersion) -> tomlkit.items.Table:
    """
    Create a TOML representation of a publishable entity version.

    The resulting content looks like:
        [[version]]
        title = "My published problem"
        version_num = 1

        [version.container] (if applicable)
        children = []

    Note: This function returns a tomlkit.items.Table, which represents
    a string-like TOML fragment rather than a complete TOML document.
    """
    version_table = tomlkit.table()
    version_table.add("title", version.title)
    version_table.add("version_num", version.version_num)

    if hasattr(version, 'containerversion'):
        # If the version has a container version, add its children
        container_table = tomlkit.table()
        children = publishing_api.get_container_children_entities_keys(version.containerversion)
        container_table.add("children", children)
        version_table.add("container", container_table)
    return version_table


def toml_collection(collection: Collection, entity_keys: list[str]) -> str:
    """
    Create a TOML representation of a collection.

    The resulting content looks like:
        [collection]
        title = "Collection 1"
        key = "COL1"
        description = "Description of Collection 1"
        created = 2025-09-03T22:28:53.839362Z
        entities = [
            "xblock.v1:problem:my_published_example",
            "xblock.v1:html:my_draft_example",
        ]
    """
    doc = tomlkit.document()

    entities_array = tomlkit.array()
    entities_array.extend(entity_keys)
    entities_array.multiline(True)

    collection_table = tomlkit.table()
    collection_table.add("title", collection.title)
    collection_table.add("key", collection.key)
    collection_table.add("description", collection.description)
    collection_table.add("created", collection.created)
    collection_table.add("entities", entities_array)

    doc.add("collection", collection_table)

    return tomlkit.dumps(doc)


def parse_learning_package_toml(content: str) -> dict:
    """
    Parse the learning package TOML content and return a dict of its fields.
    """
    lp_data: Dict[str, Any] = tomlkit.parse(content)
    return lp_data


def parse_publishable_entity_toml(content: str) -> dict:
    """
    Parse the publishable entity TOML file and return a dict of its fields.
    """
    pe_data: Dict[str, Any] = tomlkit.parse(content)
    return pe_data


def parse_collection_toml(content: str) -> dict:
    """
    Parse the collection TOML content and return a dict of its fields.
    """
    collection_data: Dict[str, Any] = tomlkit.parse(content)
    return collection_data
