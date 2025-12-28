"""
This module aggregates all applet API modules.

Question: Should this replace openedx_learning.api.authoring?
"""

# pylint: disable=wildcard-import

from .applets.backup_restore.api import *
from .applets.collections.api import *
from .applets.components.api import *
from .applets.contents.api import *
from .applets.publishing.api import *
from .applets.sections.api import *
from .applets.subsections.api import *
from .applets.units.api import *
