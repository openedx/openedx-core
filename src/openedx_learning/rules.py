"""
This is where the openedx_learning app's rules-based permissions are registered.

``rules.apps.AutodiscoverRulesConfig`` imports ``<app_package>.rules`` and nothing deeper, so
an applet's permissions reach the registry only by being pulled in from here.
"""
# This wildcard import is okay because the applet rules module declares __all__.
# pylint: disable=wildcard-import
from .applets.cbe.rules import *
