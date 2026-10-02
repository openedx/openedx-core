"""
Serializers for the CBE REST API, v1.
"""
from __future__ import annotations

from rest_framework import serializers

from ...models import CompetencyCriterion, CompetencyRuleProfile, LogicOperator


class CompetencyRuleProfileSerializer(serializers.ModelSerializer):
    """
    Read-only representation of a CompetencyRuleProfile.

    UNSTABLE: the rule profile family is incomplete, so the create, update, and archive
    endpoints still to come may change this shape without a deprecation cycle.

    ``rule_payload`` is emitted verbatim as stored. For ``Grade``, the only rule type
    supported in this phase, that shape is ``{"op": ..., "value": ..., "scale": ...}``:
    ``op`` is one of ``gte``, ``lte``, or ``eq``, and ``value`` is a fraction between 0.0
    and 1.0 inclusive. ``GradePayload`` and ``validate_rule_payload`` in the
    ``rule_payloads`` module own that shape, so it is not renormalized here. ``scale``
    confirms the fraction is a percentage, ruling out any other scale.

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

    class Meta:
        model = CompetencyCriterion
        fields = [
            "id", "object_id", "group_id", "logic_operator",
            "rule_profile_id", "rule_type_override", "rule_payload_override", "object_tag_id",
        ]
        read_only_fields = ["id", "object_tag_id"]
