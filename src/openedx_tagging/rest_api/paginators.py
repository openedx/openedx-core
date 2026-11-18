"""
Paginators uses by the REST API
"""


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
        """Returns the current request for UserPermissionsHelper"""
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
    def _model(self) -> type:
        """
        Returns the model that is being paginated.
        """
        return Taxonomy


class TagPermissionsMixin(CanAddPermissionMixin):
    """
    Checks "add_tag" permission using a Tag bound to the current taxonomy, so that taxonomies with
    read_only=True correctly report can_add_tag=False.
    """

    @property
    def _model(self) -> type:
        """
        Returns the model that is being paginated.
        """
        return Tag

    def paginate_queryset(self, queryset, request, view=None):
        self._taxonomy = view.get_taxonomy() if view else None
        return super().paginate_queryset(queryset, request, view=view)

    def get_can_add(self, instance: Tag | None = None) -> bool | None:
        if instance is None and self._taxonomy:
            instance = Tag(taxonomy=self._taxonomy)
        # Check read-only here (as well as via get_can_add()), because the permissions check is skipped for superusers
        # but even they shouldn't be able to modify tags in read-only taxonomies.
        if instance and instance.taxonomy and instance.taxonomy.read_only:
            return False
        return super().get_can_add(instance)


class TagsPagination(TagPermissionsMixin, DefaultPagination):
    """
    Custom pagination configuration for taxonomies
    with a large number of tags. Used on the get tags API view.
    """

    page_size = 10
    max_page_size = 300


class DisabledTagsPagination(TagPermissionsMixin, DefaultPagination):
    """
    Custom pagination configuration for taxonomies
    with a small number of tags. Used on the get tags API view

    This class allows to bring all the tags of the taxonomy.
    It should be used if the number of tags within
    the taxonomy does not exceed `TAGS_THRESHOLD`.
    """

    page_size = MAX_FULL_DEPTH_THRESHOLD
    max_page_size = MAX_FULL_DEPTH_THRESHOLD + 1
