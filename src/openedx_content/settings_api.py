"""
Special openedx_content API for use in Django settings modules.

In order to be usable at Django startup, this must not import any models,
which is why it is defined separately from api.py
"""


def openedx_content_backcompat_apps_to_install():
    """
    Return list of Django apps to add to INSTALLED_APPS for backwards compatibility.

    Version 0.31.0 of openedx-core (nee openedx-learning) reorganized its installation profile
    from many Django apps within `openedx_learning.apps.authoring` into a single `openedx_content`
    Django app. But, anything installing the `openedx_content` Django will also need to install
    these backcompat Django apps for the forseeable future. For more details, see
    /docs/decisions/0020-merge-authoring-apps-into-openedx-content.rst

    Example::

        from openedx_content.api import openedx_content_backcompat_apps_to_install
        INSTALLED_APPS = [
            ...,
            "openedx_content",
            *openedx_content_backcompat_apps_to_install(),
            ...,
        ]
    """
    return [
        "openedx_content.backcompat.backup_restore",
        "openedx_content.backcompat.collections",
        "openedx_content.backcompat.components",
        "openedx_content.backcompat.contents",
        "openedx_content.backcompat.publishing",
        "openedx_content.backcompat.sections",
        "openedx_content.backcompat.subsections",
        "openedx_content.backcompat.units",
    ]
