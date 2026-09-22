"""
API Serializers for taxonomies
"""
from __future__ import annotations

from typing import Any, Type
from urllib.parse import urlencode

from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.reverse import reverse

from openedx_tagging.api import TaxonomyType
from openedx_tagging.data import TagData
from openedx_tagging.import_export.parsers import ParserFormat
from openedx_tagging.models import ObjectTag, Tag, TagImportTask, Taxonomy
from openedx_tagging.models.utils import RESERVED_TAG_CHARS
from openedx_tagging.rules import ObjectTagPermissionItem

from ..utils import UserPermissionsHelper


class TaxonomyListQueryParamsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for the query params for the GET view
    """

    enabled = serializers.BooleanField(required=False)


class TaxonomyExportQueryParamsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for the query params for the GET view
    """
    download = serializers.BooleanField(required=False, default=False)
    output_format = serializers.RegexField(r"(?i)^(json|csv)$", allow_blank=False)


class UserPermissionsSerializerMixin(UserPermissionsHelper):
    """
    Provides methods for serializing user permissions.

    To use this mixin:

    1. Add it to your serializer's list of subclasses
    2. Add `can_<action>` fields for each permission/action you want to serialize.

    and this mixin will take care of the rest.

    Notes:
    * Assumes the serialized model should be used to check permissions (override _model to change).
    * Requires the current request to be passed into the serializer context (override _request to change).
    """
    @property
    def _model(self) -> Type:
        """
        Returns the model that is being serialized
        """
        return self.Meta.model  # type: ignore[attr-defined]

    @property
    def _request(self) -> Request:
        """
        Returns the current request from the serializer context.
        """
        return self.context.get('request')  # type: ignore[attr-defined]


class TaxonomySerializer(UserPermissionsSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for the Taxonomy model.
    """
    tags_count = serializers.SerializerMethodField()
    can_change_taxonomy = serializers.SerializerMethodField(method_name='get_can_change')
    can_delete_taxonomy = serializers.SerializerMethodField(method_name='get_can_delete')
    can_tag_object = serializers.SerializerMethodField()
    export_id = serializers.CharField(required=False)
    taxonomy_type = serializers.ChoiceField(
        choices=[TaxonomyType.TAGS.value, TaxonomyType.COMPETENCY.value],
        default=TaxonomyType.TAGS.value,
        write_only=True,
    )

    class Meta:
        model = Taxonomy
        fields = [
            "id",
            "name",
            "description",
            "enabled",
            "allow_multiple",
            "allow_free_text",
            "read_only",
            "visible_to_authors",
            "tags_count",
            "can_change_taxonomy",
            "can_delete_taxonomy",
            "can_tag_object",
            "export_id",
            "taxonomy_type",
        ]

    def get_tags_count(self, instance):
        """
        Return the "tags_count" annotation if present.

        Or just count the taxonomy's tags.
        """
        if hasattr(instance, 'tags_count'):
            return instance.tags_count
        return instance.tag_set.count()

    def get_can_tag_object(self, instance) -> bool | None:
        """
        Returns True if the current request user may create object tags on this taxonomy.

        (The object_id test is necessarily skipped because we don't have an object_id to check.)
        """
        perm_name = f'{self.app_label}.can_tag_object'
        perm_object = ObjectTagPermissionItem(taxonomy=instance, object_id="")
        return self._can(perm_name, perm_object)


class ObjectTagListQueryParamsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for the query params for the ObjectTag GET view
    """

    taxonomy = serializers.PrimaryKeyRelatedField(
        queryset=Taxonomy.objects.all(), required=False
    )


class ObjectTagMinimalSerializer(UserPermissionsSerializerMixin, serializers.ModelSerializer):
    """
    Minimal serializer for the ObjectTag model.
    """

    class Meta:
        model = ObjectTag
        fields = ["value", "lineage", "can_delete_objecttag"]

    lineage = serializers.ListField(child=serializers.CharField(), source="get_lineage", read_only=True)
    can_delete_objecttag = serializers.SerializerMethodField()

    def get_can_delete_objecttag(self, instance) -> bool | None:
        """
        SerializerMethodField callback for `can_delete_objecttag`.

        Delegates to `can_delete_object_tag` so subclasses can
        override a stable method name that isn't tied to DRF's
        SerializerMethodField naming convention.
        """
        return self.can_delete_object_tag(instance)

    def can_delete_object_tag(self, instance) -> bool | None:
        """
        Check if the user is authorized to delete the provided tag.
        """
        perm_name = f'{self.app_label}.remove_objecttag_objectid'
        return self._can(perm_name, instance.object_id)


class ObjectTagSerializer(ObjectTagMinimalSerializer):
    """
    Serializer for the ObjectTag model.
    """
    class Meta:
        model = ObjectTag
        fields = ObjectTagMinimalSerializer.Meta.fields + [
            "export_id",
            "taxonomy_id",
            # If the Tag or Taxonomy has been deleted, this ObjectTag shouldn't be shown to users.
            "is_deleted",
        ]


class ObjectTagsByTaxonomySerializer(UserPermissionsSerializerMixin, serializers.ModelSerializer):
    """
    Serialize a group of ObjectTags, grouped by taxonomy
    """
    class Meta:
        model = ObjectTag

    def to_representation(self, instance: list[ObjectTag]) -> dict:
        """
        Convert this list of ObjectTags to the serialized dictionary, grouped by Taxonomy
        """
        # Allows consumers like edx-platform to override this
        ObjectTagViewMinimalSerializer = self.context["view"].minimal_serializer_class

        by_object: dict[str, dict[str, Any]] = {}
        for obj_tag in instance:
            if obj_tag.object_id not in by_object:
                by_object[obj_tag.object_id] = {
                    "taxonomies": []
                }
            taxonomies = by_object[obj_tag.object_id]["taxonomies"]
            tax_entry = next((t for t in taxonomies if t["taxonomy_id"] == obj_tag.taxonomy_id), None)
            if tax_entry is None:
                tax_entry = {
                    "name": obj_tag.taxonomy.name if obj_tag.taxonomy else None,
                    "taxonomy_id": obj_tag.taxonomy_id,
                    "can_tag_object": self.can_tag_object(obj_tag),
                    "tags": [],
                    "export_id": obj_tag.export_id,
                }
                taxonomies.append(tax_entry)
            tax_entry["tags"].append(ObjectTagViewMinimalSerializer(obj_tag, context=self.context).data)
        return by_object

    def can_tag_object(self, obj_tag) -> bool | None:
        """
        Check if the user is authorized to tag the provided object.
        """
        perm_name = f"{self.app_label}.can_tag_object"
        return self._can(perm_name, obj_tag)


class ObjectTagUpdateByTaxonomySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer of a taxonomy item of ObjectTag UPDATE view.
    """
    taxonomy = serializers.PrimaryKeyRelatedField(
        queryset=Taxonomy.objects.all(), required=True
    )
    tags = serializers.ListField(child=serializers.CharField(), required=True)


class ObjectTagUpdateBodySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer of the body for the ObjectTag UPDATE view
    """
    tagsData = serializers.ListField(child=ObjectTagUpdateByTaxonomySerializer(), required=True)


def validate_tag_value(value, context, original_value=None):
    """
    Validates the incoming request early:
    - This tag is unique, not a duplicate. (The model only validates this if you call `full_clean()`.)
    - There are no forbidden / reserved characters present. There is an additional
    model-side validation for this as well, but we are keeping this so we can validate
    the incoming request immediately.
    """
    taxonomy_id = context.get("taxonomy_id")
    original_tag = Tag.objects.filter(taxonomy_id=taxonomy_id, value=original_value).first() if original_value else None
    tag_id = original_tag.pk if original_tag else None
    if taxonomy_id is not None:
        queryset = Tag.objects.filter(taxonomy_id=taxonomy_id, value=value)

        # Don't compare tag against itself when validating its updated value.
        if tag_id:
            queryset = queryset.exclude(pk=tag_id)

        # Check if tag value already exists within this taxonomy. If so, raise a validation error.
        if queryset.exists():
            raise serializers.ValidationError(
                f'Tag value "{value}" already exists in this taxonomy.', code='unique'
            )

    # validator checks there are no forbidden characters ">" or ";":
    for char in value:
        if char in RESERVED_TAG_CHARS:
            raise serializers.ValidationError('Tag values cannot contain "\t" or ">" or ";" characters.')
    return value


class TagDataSerializer(UserPermissionsSerializerMixin, serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for TagData dicts. Also can serialize Tag instances.

    Adds a link to get the sub tags
    """
    value = serializers.CharField()
    external_id = serializers.CharField(allow_null=True)
    child_count = serializers.IntegerField()
    depth = serializers.IntegerField()
    parent_value = serializers.CharField(allow_null=True)
    usage_count = serializers.IntegerField(required=False)
    # Internal database ID, if any. Generally should not be used; prefer 'value' which is unique within each taxonomy.
    # Free text taxonomies never have '_id' for their tags.
    _id = serializers.IntegerField(allow_null=True)

    sub_tags_url = serializers.SerializerMethodField()
    can_change_tag = serializers.SerializerMethodField()
    can_delete_tag = serializers.SerializerMethodField()

    def validate_value(self, value):
        """
        Runs validations for the tag value.
        """
        return validate_tag_value(value, self.context)

    def get_sub_tags_url(self, obj: TagData | Tag):
        """
        Returns URL for the list of child tags of the current tag.
        """
        child_count = obj.child_count if isinstance(obj, Tag) else obj["child_count"]
        if child_count > 0 and "taxonomy_id" in self.context:
            value = obj.value if isinstance(obj, Tag) else obj["value"]
            request = self.context["request"]
            query_params = request.query_params
            new_query_params = {"parent_tag": value}
            if "full_depth_threshold" in query_params:
                new_query_params["full_depth_threshold"] = query_params["full_depth_threshold"]
            if "search_term" in query_params:
                new_query_params["search_term"] = query_params["search_term"]
            url_namespace = request.resolver_match.namespace  # get the namespace, usually "oel_tagging"
            url = (
                reverse(f"{url_namespace}:taxonomy-tags", args=[str(self.context["taxonomy_id"])])
                + "?" + urlencode(new_query_params)
            )
            return request.build_absolute_uri(url)
        return None

    @property
    def _model(self) -> Type:
        """
        Returns the model used when checking permissions.
        """
        return Tag

    def get_can_change_tag(self, _instance) -> bool | None:
        """
        Returns True if the current user is allowed to edit/change this Tag instance.

        Because we're serializing TagData (not Tags), the view stores these permissions in the serializer
        context.
        """
        return self.context.get('can_change_tag')

    def get_can_delete_tag(self, _instance) -> bool | None:
        """
        Returns True if the current user is allowed to delete this Tag instance.

        Because we're serializing TagData (not Tags), the view stores these permissions in the serializer
        """
        return self.context.get('can_delete_tag')

    def to_representation(self, instance: TagData | Tag) -> dict:
        """
        Convert this TagData (or Tag model instance) to the serialized dictionary
        """
        data = super().to_representation(instance)
        if isinstance(instance, Tag):
            data["_id"] = instance.pk  # The ID field won't otherwise be detected.
            data["parent_value"] = instance.parent.value if instance.parent else None
        return data


class TaxonomyTagCreateBodySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer of the body for the Taxonomy Tags CREATE request
    """

    tag = serializers.CharField(required=True)
    parent_tag_value = serializers.CharField(required=False)
    external_id = serializers.CharField(required=False)

    def validate_tag(self, value):
        """
        Run validations for the tag value.
        """
        return validate_tag_value(value, self.context)


class TaxonomyTagUpdateBodySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer of the body for the Taxonomy Tags UPDATE request
    """

    tag = serializers.CharField(required=True)
    updated_tag_value = serializers.CharField(required=True)

    def validate_updated_tag_value(self, value):
        """
        Run validations for the updated tag value.
        """
        return validate_tag_value(value, self.context, original_value=self.initial_data.get("tag"))


class TaxonomyTagDeleteBodySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer of the body for the Taxonomy Tags DELETE request
    """

    tags = serializers.ListField(
        child=serializers.CharField(), required=True
    )
    with_subtags = serializers.BooleanField(required=False)

    def validate_tags(self, tags_list):
        """
        Make sure all tags are valid and exist before attempting deletion, to avoid partial deletes.
        """
        # Iterate through the list and make one bulk request that checks whether every tag.value exists
        taxonomy_id = self.context.get("taxonomy_id")
        existing_tags = set(
            Tag.objects.filter(taxonomy_id=taxonomy_id, value__in=tags_list)
            .values_list("value", flat=True)
        )
        missing_tags = set(tags_list) - existing_tags

        if missing_tags:
            raise serializers.ValidationError(
                f"Deletion aborted. The following tags do not exist and cannot be deleted:"
                f" {', '.join(missing_tags)}"
            )
        return tags_list


class TaxonomyImportBodySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer of the body for the Taxonomy Import request
    """
    file = serializers.FileField(required=True)

    def validate(self, attrs):
        """
        Validates the file extension and add parser_format to the data
        """
        filename = attrs["file"].name
        ext = filename.split('.')[-1]
        parser_format = getattr(ParserFormat, ext.upper(), None)
        if not parser_format:
            raise serializers.ValidationError({"file": f'File type not supported: {ext.lower()}'}, 'file')

        attrs['parser_format'] = parser_format
        return attrs


class TaxonomyImportNewBodySerializer(TaxonomyImportBodySerializer):  # pylint: disable=abstract-method
    """
    Serializer of the body for the Taxonomy Create and Import request
    """
    taxonomy_name = serializers.CharField(required=True)
    taxonomy_description = serializers.CharField(default="")
    taxonomy_export_id = serializers.CharField(required=False)
    taxonomy_type = serializers.ChoiceField(
        choices=[TaxonomyType.TAGS.value, TaxonomyType.COMPETENCY.value],
        default=TaxonomyType.TAGS.value,
        write_only=True,
    )


class TagImportTaskSerializer(serializers.ModelSerializer):
    """
    Serializer for the TagImportTask model.
    """
    class Meta:
        model = TagImportTask
        fields = [
            "id",
            "log",
            "status",
            "creation_date",
        ]


class TaxonomyImportPlanResponseSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for the response of the Taxonomy Import Plan request
    """
    task = TagImportTaskSerializer()
    plan = serializers.SerializerMethodField()
    error = serializers.CharField(required=False, allow_null=True)

    def get_plan(self, obj):
        """
        Returns the plan of the import
        """
        plan = obj.get("plan", None)
        if plan:
            return plan.plan()

        return None
