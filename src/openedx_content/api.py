"""
This is the public API for content authoring in the Open edX Core.

This is the single ``api`` module that code outside of the
``openedx_content.*`` package should import from. It will
re-export the public functions from all api.py modules of its applets.
It may also implement its own convenience APIs that wrap calls to multiple app
APIs.
"""

# These wildcard imports are okay because these api modules declare __all__.
# pylint: disable=wildcard-import,unused-import

# Signals are kept in a separate namespace, for two reasons:
# (1) so they can easily be imported/used as `api.signals` (e.g. `from openedx_content import api`, use `api.signals.x`)
# (2) to avoid confusion between event data structures and other API symbols with similar names (e.g.
#     `DraftChangeLogEventData` vs `DraftChangeLogRecord` is clearer if the former is `signals.DraftChangeLogEventData`)
from . import signals
# The rest of the public API (other than models):
from .applets.assets.api import *
from .applets.backup_restore.api import *
from .applets.collections.api import *
from .applets.components.api import *
from .applets.containers.api import *
from .applets.media.api import *
from .applets.publishing.api import *
from .applets.sections.api import *
from .applets.subsections.api import *
from .applets.units.api import *
