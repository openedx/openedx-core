"""
Module for parts of the Learning Core API that exist to make it easier to use in
Django projects.
"""


def openedx_learning_apps_to_install():
    """
    Return all app names for appending to INSTALLED_APPS.

    This function exists to better insulate edx-platform and potential plugins
    over time, as we eventually plan to remove the backcompat apps.
    """
    return [
        "openedx_learning.apps.openedx_content",
        "openedx_learning.apps.openedx_content.backcompat.backup_restore",
        "openedx_learning.apps.openedx_content.backcompat.collections",
        "openedx_learning.apps.openedx_content.backcompat.components",
        "openedx_learning.apps.openedx_content.backcompat.contents",
        "openedx_learning.apps.openedx_content.backcompat.publishing",
        "openedx_learning.apps.openedx_content.backcompat.sections",
        "openedx_learning.apps.openedx_content.backcompat.subsections",
        "openedx_learning.apps.openedx_content.backcompat.units",
    ]
