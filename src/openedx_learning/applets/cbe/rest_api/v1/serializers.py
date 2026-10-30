"""
Serializers for the CBE REST API, v1.
"""
from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from rest_framework import serializers

from ...models import CompetencyCriteriaGroup, CompetencyCriterion, CompetencyRuleProfile, LogicOperator


class CompetencyRuleProfileSerializer(serializers.ModelSerializer):
    """
    Read-only representation of a CompetencyRuleProfile.

    UNSTABLE: the rule profile family is incomplete, so the create, update, and archive
    endpoints still to come may change this shape without a deprecation cycle.

    ``rule_payload`` is emitted verbatim as stored. For ``Grade``, the only rule type
    supported in this phase, that shape is ``{"op": ..., "value": ..., "scale": ...}``:
    ``op`` is one of ``gte``, ``lte``, or ``eq``, and ``value`` is a fraction between 0.0
    and 1.0 inclusive. ``GradeRulePayload`` and ``validate_rule_payload`` in the
    ``rule_payloads`` module own that shape, so it is not renormalized here. ``scale`` is
    always ``"percent"`` for now; it tells a client how to display ``value`` (``0.8`` means 80%).

    ``scope_code`` and the raw ``organization``, ``course``, and ``competency_taxonomy``
    columns are left out: ``scope_type`` below is what a client can act on, while the
    others are internal, existing only to enforce the one-profile-per-scope constraint
    (:ref:`openedx-learning-adr-0002` Decision 3).
    """

    scope_type = serializers.SerializerMethodField()

    class Meta:
        model = CompetencyRuleProfile
        fields = ["id", "scope_type", "rule_type", "rule_payload", "archived"]
        # scope_type is absent here because DRF refuses a field that is both declared above and
        # named in read_only_fields; a SerializerMethodField is read-only in any case.
        read_only_fields = ["id", "rule_type", "rule_payload", "archived"]

    def get_scope_type(self, profile: CompetencyRuleProfile) -> str:
        """
        Return which kind of scope ``profile`` applies to.

        All four kinds are recognized from the outset, even though only the system default can
        exist today, so enabling a narrower scope needs no edit here. The scope columns are read
        by their ``_id`` attributes so that no row costs a query, and the system default is
        recognized by those columns being null rather than by matching the internal
        ``scope_code`` string.
        """
        if profile.competency_taxonomy_id is not None:
            return "taxonomy"
        if profile.course_id is not None:
            return "course"
        if profile.organization_id is not None:
            return "organization"
        return "system_default"


class CompetencyCriterionSerializer(serializers.ModelSerializer):
    """
    Doubles as the request-body parser and the response representation for a criterion.

    ``object_id`` and ``logic_operator`` are not CompetencyCriterion fields at all (``object_id``
    isn't stored anywhere on this model; ``logic_operator`` belongs to CompetencyCriteriaGroup),
    so they're declared as plain write_only fields the view reads out of ``validated_data``, not
    model-bound fields. Every other field's JSON name matches its model attribute exactly
    (``group_id``, ``rule_profile_id``, ``object_tag_id``), so none of them need a ``source=``.
    """

    object_id = serializers.CharField(write_only=True)
    group_id = serializers.IntegerField(required=False, allow_null=True)
    # Only ever applies on the derive-or-create path (group_id omitted): it sets the AND/OR
    # operator on the brand-new leaf group this request creates. Rejected alongside an explicit
    # group_id because changing an existing leaf's operator is a future group-update endpoint's
    # job, not this one's -- a caller can't use this field to silently change an existing
    # group's behavior.
    logic_operator = serializers.ChoiceField(
        choices=LogicOperator.choices, write_only=True, required=False, allow_null=True,
    )
    rule_profile_id = serializers.IntegerField(required=False, allow_null=True)
    object_tag_id = serializers.IntegerField(read_only=True)
    archived = serializers.BooleanField(read_only=True)

    class Meta:
        model = CompetencyCriterion
        fields = [
            "id", "object_id", "group_id", "logic_operator",
            "rule_profile_id", "rule_type_override", "rule_payload_override", "object_tag_id", "archived",
        ]
        read_only_fields = ["id", "object_tag_id", "archived"]

    def validate_object_id(self, value: str) -> str:
        """Reject an object_id that isn't a parseable usage key."""
        # Checked before the view's permission check, which denies an unparseable key as a 403 rather than a 400.
        try:
            UsageKey.from_string(value)
        except InvalidKeyError as exc:
            raise serializers.ValidationError(_("object_id is not a valid usage key.")) from exc
        return value


class CompetencyCriterionBulkUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Parse the bulk-update request body, checking only each field's shape and rejecting unknown keys.
    """

    criterion_ids = serializers.ListField(child=serializers.IntegerField())
    rule_profile_id = serializers.IntegerField(required=False, allow_null=True)
    rule_type_override = serializers.CharField(required=False, allow_null=True)
    rule_payload_override = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs: dict) -> dict:
        """
        Reject any top-level key this serializer does not declare.
        """
        unrecognized = sorted(set(self.initial_data) - set(self.fields))
        if unrecognized:
            raise serializers.ValidationError({key: "This field is not recognized." for key in unrecognized})
        return attrs


class CompetencyCriteriaGroupSerializer(serializers.ModelSerializer):
    """
    Read-only representation of a CompetencyCriteriaGroup, for the criteria-tree read endpoint.

    UNSTABLE: the criteria-authoring family is incomplete, so this shape may change without a
    deprecation cycle.

    course_key uses a CharField with a dotted source instead of SlugRelatedField, since
    CourseRun.course_key is an opaque-keys field that the JSON renderer can't serialize directly;
    CharField.to_representation() calls str() on it instead, and returns None cleanly when
    `course` itself is null.
    """

    course_key = serializers.CharField(source="course.course_key", read_only=True, allow_null=True)

    class Meta:
        model = CompetencyCriteriaGroup
        fields = ["id", "parent_id", "tag_id", "course_key", "name", "ordering", "logic_operator"]


class CompetencyCriteriaGroupUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Parse the group-update request body against the stored group, which is this serializer's instance.

    ``logic_operator`` is required and is the only field that can change. Its type is checked here,
    and its value is checked by :func:`update_competency_criteria_group`. Every other field that
    CompetencyCriteriaGroupSerializer emits is accepted too, so a client can send back the group it
    read. Each one must match what is stored, and the error names any that don't. A mismatch is a
    rejected edit, not something to ignore silently.
    """

    logic_operator = serializers.CharField()
    id = serializers.IntegerField(required=False)
    parent_id = serializers.IntegerField(required=False, allow_null=True)
    tag_id = serializers.IntegerField(required=False)
    course_key = serializers.CharField(required=False, allow_null=True)
    # Compared as sent: trimming would turn an exact echo of a padded name into an apparent rename.
    name = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    ordering = serializers.IntegerField(required=False)

    def validate(self, attrs: dict) -> dict:
        """Reject any key this serializer does not declare, and any fixed field that differs from the stored one."""
        unrecognized = sorted(set(self.initial_data) - set(self.fields))
        if unrecognized:
            raise serializers.ValidationError({key: "This field is not recognized." for key in unrecognized})
        stored = CompetencyCriteriaGroupSerializer(self.instance).data
        changed = sorted(key for key, value in attrs.items() if key != "logic_operator" and value != stored[key])
        if changed:
            raise serializers.ValidationError({key: "This field cannot be changed." for key in changed})
        return attrs


class CompetencyCriterionReadSerializer(serializers.ModelSerializer):
    """
    Read-only representation of a CompetencyCriterion, for the criteria-tree read endpoint.

    UNSTABLE: the criteria-authoring family is incomplete, so this shape may change without a
    deprecation cycle.

    A separate serializer from CompetencyCriterionSerializer (#665's create-request serializer),
    not an added field on it: that serializer's `object_id` is write_only, and giving it a dotted
    source to double as this endpoint's output would silently break the create endpoint, since a
    dotted source on a writable field changes where to_internal_value() places the value in
    validated_data.
    """

    object_id = serializers.CharField(source="object_tag.object_id", read_only=True)

    class Meta:
        model = CompetencyCriterion
        fields = [
            "id", "group_id", "object_tag_id", "object_id",
            "rule_profile_id", "rule_type_override", "rule_payload_override",
        ]
