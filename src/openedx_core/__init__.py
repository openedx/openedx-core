"""
Metadata for the openedx-core repository and its PyPI package.

There is currently no public API for openedx_core--that's intentional!
The public APIs belong to the specific apps (openedx_content, openedx_tagging, etc.).
"""
from importlib.metadata import version as get_version

# The version for the entire repository
__version__ = get_version("openedx-core")
