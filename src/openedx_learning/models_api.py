"""
Models that we want callers to extend or make foreign keys to.

This is also the stable import point for the model classes themselves, for callers that need to create competency
taxonomies directly. Pathway models should be created through `openedx_learning.api`, which keeps their versioning
consistent; import them here only to make foreign keys to them.
"""

# pylint: disable=unused-import
from .models import (
    CompetencyTaxonomy,
    Pathway,
    PathwayItem,
    PathwayItemCourseRun,
    PathwayItemVersion,
    PathwayVersion,
    PathwayVersionItem,
)
