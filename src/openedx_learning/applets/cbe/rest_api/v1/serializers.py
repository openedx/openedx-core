"""
Serializers for the CBE REST API, v1.
"""
from __future__ import annotations

from rest_framework import serializers

from ...models import CompetencyRuleProfile


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
