"""
The serializers module for restoration of authoring data.
"""
from datetime import timezone

from rest_framework import serializers

from ..components import api as components_api


class LearningPackageSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for learning packages.

    Note:
        The `key` field is serialized, but it is generally not trustworthy for restoration.
        During restore, a new key may be generated or overridden.
    """
    title = serializers.CharField(required=True)
    key = serializers.CharField(required=True)
    description = serializers.CharField(required=True, allow_blank=True)
    created = serializers.DateTimeField(required=True, default_timezone=timezone.utc)


class LearningPackageMetadataSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for learning package metadata.

    Note:
        This serializer handles data exported to an archive (e.g., during backup),
        but the metadata is not restored to the database and is meant solely for inspection.
    """
    format_version = serializers.IntegerField(required=True)
    created_by = serializers.CharField(required=False, allow_null=True)
    created_by_email = serializers.EmailField(required=False, allow_null=True)
    created_at = serializers.DateTimeField(required=True, default_timezone=timezone.utc)
    origin_server = serializers.CharField(required=False, allow_null=True)


class EntitySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for publishable entities.
    """
    can_stand_alone = serializers.BooleanField(required=True)
    key = serializers.CharField(required=True)
    created = serializers.DateTimeField(required=True, default_timezone=timezone.utc)


class EntityVersionSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for publishable entity versions.
    """
    title = serializers.CharField(required=True)
    entity_key = serializers.CharField(required=True)
    created = serializers.DateTimeField(required=True, default_timezone=timezone.utc)
    version_num = serializers.IntegerField(required=True)


class ComponentSerializer(EntitySerializer):  # pylint: disable=abstract-method
    """
    Serializer for components.
    Contains logic to convert entity_key to component_type and local_key.
    """

    def validate(self, attrs):
        """
        Custom validation logic:
        parse the entity_key into (component_type, local_key).
        """
        entity_key = attrs["key"]
        try:
            component_type_obj, local_key = components_api.get_or_create_component_type_by_entity_key(entity_key)
            attrs["component_type"] = component_type_obj
            attrs["local_key"] = local_key
        except ValueError as exc:
            raise serializers.ValidationError({"key": str(exc)})
        return attrs


class ComponentVersionSerializer(EntityVersionSerializer):  # pylint: disable=abstract-method
    """
    Serializer for component versions.
    """


class ContainerSerializer(EntitySerializer):  # pylint: disable=abstract-method
    """
    Serializer for containers.
    """
    container = serializers.DictField(required=True)

    def validate_container(self, value):
        """
        Custom validation logic for the container field.
        Ensures that the container dict has exactly one key which is one of
        "section", "subsection", or "unit" values.
        """
        errors = []
        if not isinstance(value, dict) or len(value) != 1:
            errors.append("Container must be a dict with exactly one key.")
        if len(value) == 1:  # Only check the key if there is exactly one
            container_type = list(value.keys())[0]
            if container_type not in ("section", "subsection", "unit"):
                errors.append(f"Invalid container value: {container_type}")
        if errors:
            raise serializers.ValidationError(errors)
        return value

    def validate(self, attrs):
        """
        Custom validation logic:
        parse the container dict to extract the container type.
        """
        container = attrs["container"]
        container_type = list(container.keys())[0]  # It is safe to do this after validate_container
        attrs["container_type"] = container_type
        attrs.pop("container")  # Remove the container field after processing
        return attrs


class ContainerVersionSerializer(EntityVersionSerializer):  # pylint: disable=abstract-method
    """
    Serializer for container versions.
    """
    container = serializers.DictField(required=True)

    def validate_container(self, value):
        """
        Custom validation logic for the container field.
        Ensures that the container dict has exactly one key "children" which is a list of strings.
        """
        errors = []
        if not isinstance(value, dict) or len(value) != 1:
            errors.append("Container must be a dict with exactly one key.")
        if "children" not in value:
            errors.append("Container must have a 'children' key.")
        if "children" in value and not isinstance(value["children"], list):
            errors.append("'children' must be a list.")
        if errors:
            raise serializers.ValidationError(errors)
        return value

    def validate(self, attrs):
        """
        Custom validation logic:
        parse the container dict to extract the children list.
        """
        children = attrs["container"]["children"]  # It is safe to do this after validate_container
        attrs["children"] = children
        attrs.pop("container")  # Remove the container field after processing
        return attrs


class CollectionSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for collections.
    """
    title = serializers.CharField(required=True)
    key = serializers.CharField(required=True)
    description = serializers.CharField(required=True, allow_blank=True)
    entities = serializers.ListField(
        child=serializers.CharField(),
        required=True,
        allow_empty=True,
    )
