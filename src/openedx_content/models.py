"""
This module aggregates all applet model modules.

I experimented with creating a utility to auto-detect applets and magically
import their modules, but that broke code introspection.
"""

# pylint: disable=wildcard-import

from .applets.backup_restore.models import *
from .applets.collections.models import *
from .applets.components.models import *
from .applets.contents.models import *
from .applets.publishing.models import *
from .applets.sections.models import *
from .applets.subsections.models import *
from .applets.units.models import *
