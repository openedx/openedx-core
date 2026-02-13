"""
App Config for the openedx_catalog app.
"""

from django.apps import AppConfig


class CatalogAppConfig(AppConfig):
    """
    Initialize and configure the Catalog app
    """

    name = "openedx_catalog"
    verbose_name = "Open edX Core > Catalog"
    default_auto_field = "django.db.models.BigAutoField"
    label = "openedx_catalog"

    def ready(self) -> None:
        pass
