"""
Paginators for the CBE REST API.

These sit outside the versioned package, as openedx_tagging's do, because a page size is
version independent.
"""
from edx_rest_framework_extensions.paginators import DefaultPagination  # type: ignore[import]


class CompetencyRuleProfilePagination(DefaultPagination):
    """
    Page size for the competency rule profile collection.

    Pinned here rather than inherited from the consuming project's DEFAULT_PAGINATION_CLASS, the
    same way openedx_tagging pins TaxonomyPagination: this is a published library, so the page
    size is part of its REST contract instead of varying by deployment.
    """

    page_size = 100
    max_page_size = 500
