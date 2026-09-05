"""
This is the public API for learning-domain features in Open edX Core.
"""
# This wildcard import is okay because the applet api module declares __all__.
# pylint: disable=wildcard-import
from .applets.cbe.api import *
from .applets.cbe.views import CompetencyTaxonomyView  # pylint: disable=unused-import
