"""
Models that are only for use in tests.

These models are specifically for testing the `containers` API.
"""

from typing import override

from django.core.exceptions import ValidationError
from django.db import models

from openedx_content.models_api import (
    Container,
    ContainerVersion,
    PublishableEntity,
    PublishableEntityMixin,
    PublishableEntityVersionMixin,
)


class TestEntity(PublishableEntityMixin):
    """
    A generic entity that's not a container. Think of it like a Component, but
    for testing `containers` APIs without using the `components` API.
    """

    __test__ = False  # Tell pytest this is "an entity for testing" not "a test class for entities"


class TestEntityVersion(PublishableEntityVersionMixin):
    """
    A particular version of a TestEntity.
    """

    __test__ = False


@Container.register_subclass
class TestContainer(Container):
    """
    A Test Container that can hold anything
    """

    __test__ = False  # Tell pytest this is "a container for testing" not "a test class for containers"

    type_code = "test_generic"

    container = models.OneToOneField(Container, on_delete=models.CASCADE, parent_link=True, primary_key=True)

    @override
    @classmethod
    def validate_entity(cls, entity: PublishableEntity) -> None:
        """Allow any type of child"""


class TestContainerVersion(ContainerVersion):
    """
    A TestContainerVersion is a specific version of a TestContainer.
    """

    __test__ = False

    container_version = models.OneToOneField(
        ContainerVersion,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )


@Container.register_subclass
class ContainerContainer(Container):
    """
    A Test Container that can hold any container
    """

    type_code = "test_container_container"
    validate_entity_select_related = ("container",)

    # Test that we can name this field anything
    base_container = models.OneToOneField(Container, on_delete=models.CASCADE, parent_link=True, primary_key=True)

    @override
    @classmethod
    def validate_entity(cls, entity: PublishableEntity) -> None:
        """Allow any container as a child"""
        if not hasattr(entity, "container"):
            raise ValidationError("ContainerContainer only allows containers as children.")


class ContainerContainerVersion(ContainerVersion):
    """
    A ContainerContainerVersion is a specific version of a ContainerContainer.
    """

    container_version = models.OneToOneField(
        ContainerVersion,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )
