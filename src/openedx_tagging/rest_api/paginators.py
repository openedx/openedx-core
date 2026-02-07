"""
Paginators uses by the REST API
"""
from typing import Type

from edx_rest_framework_extensions.paginators import DefaultPagination  # type: ignore[import]
from rest_framework.request import Request
from rest_framework.response import Response

from openedx_tagging.models import Tag, Taxonomy

from .utils import UserPermissionsHelper

# From this point, the tags begin to be paginated
MAX_FULL_DEPTH_THRESHOLD = 10_000


class CanAddPermissionMixin(UserPermissionsHelper):  # pylint: disable=abstract-method
    """
    This mixin inserts a boolean "can_add_<model>" field at the top level of the paginated response.

    The value of the field indicates whether request user may create new instances of the current model.
    """
    @property
    def _request(self) -> Request:
        """
        Returns the current request.
        """
        return self.request  # type: ignore[attr-defined]

    def get_paginated_response(self, data) -> Response:
        """
        Injects the user's model-level permissions into the paginated response.
        """
        response_data = super().get_paginated_response(data).data  # type: ignore[misc]
        field_name = f"can_add_{self.model_name}"
        response_data[field_name] = self.get_can_add()
        return Response(response_data)


class TaxonomyPagination(CanAddPermissionMixin, DefaultPagination):
    """
    Inserts permissions data for Taxonomies into the top level of the paginated response.
    """
    page_size = 500
    max_page_size = 500

    @property
    def _model(self) -> Type:
        """
        Returns the model that is being paginated.
        """
        return Taxonomy


class TagsPagination(CanAddPermissionMixin, DefaultPagination):
    """
    Custom pagination configuration for taxonomies
    with a large number of tags. Used on the get tags API view.
    """
    page_size = 10
    max_page_size = 300

    @property
    def _model(self) -> Type:
        """
        Returns the model that is being paginated.
        """
        return Tag


class DisabledTagsPagination(CanAddPermissionMixin, DefaultPagination):
    """
    Custom pagination configuration for taxonomies
    with a small number of tags. Used on the get tags API view

    This class allows to bring all the tags of the taxonomy.
    It should be used if the number of tags within
    the taxonomy does not exceed `TAGS_THRESHOLD`.
    """
    page_size = MAX_FULL_DEPTH_THRESHOLD
    max_page_size = MAX_FULL_DEPTH_THRESHOLD + 1

    @property
    def _model(self) -> Type:
        """
        Returns the model that is being paginated.
        """
        return Tag
