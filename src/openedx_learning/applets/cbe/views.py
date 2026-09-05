"""
REST API views for Competency-Based Education (CBE).
"""
from __future__ import annotations

from django.core import exceptions
from rest_framework.exceptions import ValidationError

from openedx_tagging.api import TaxonomyType
from openedx_tagging.models import Taxonomy
from openedx_tagging.rest_api.v1.views import TaxonomyView

from .api import create_competency_taxonomy


class CompetencyTaxonomyView(TaxonomyView):
    """
    TaxonomyView that also supports taxonomy_type="competency".
    """

    def perform_create(self, serializer) -> None:
        """
        Create a new taxonomy (competency or tags).
        """
        taxonomy_type = serializer.validated_data.pop("taxonomy_type", TaxonomyType.TAGS.value)
        if taxonomy_type == TaxonomyType.COMPETENCY.value:
            try:
                serializer.instance = create_competency_taxonomy(**serializer.validated_data)
            except exceptions.ValidationError as e:
                raise ValidationError() from e
        else:
            super().perform_create(serializer)

    def _create_taxonomy_for_import(self, validated_data: dict) -> Taxonomy:
        """
        Create a competency taxonomy if requested, otherwise defer to the base implementation.
        """
        taxonomy_type = validated_data.get("taxonomy_type", TaxonomyType.TAGS.value)
        if taxonomy_type == TaxonomyType.COMPETENCY.value:
            return create_competency_taxonomy(
                name=validated_data["taxonomy_name"],
                description=validated_data["taxonomy_description"],
                export_id=validated_data.get("taxonomy_export_id"),
            )
        return super()._create_taxonomy_for_import(validated_data)
