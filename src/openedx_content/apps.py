"""
App Config for our umbrella openedx_content app.
"""
from django.apps import AppConfig

# pylint: disable=import-outside-toplevel
#
# Local imports in AppConfig.ready() are common and expected in Django, since
# Django needs to run initialization before before we can query for things like
# models, settings, and app config.


class ContentConfig(AppConfig):
    """
    Initialization for all applets must happen in here.
    """

    name = "openedx_content"
    verbose_name = "Open edX Core > Content"
    default_auto_field = "django.db.models.BigAutoField"
    label = "openedx_content"

    def register_publishable_models(self):
        """
        Register all Publishable -> Version model pairings in our app.
        """
        from .api import register_publishable_models
        from .models import (
            Component,
            ComponentVersion,
            Container,
            ContainerVersion,
            Section,
            SectionVersion,
            Subsection,
            SubsectionVersion,
            Unit,
            UnitVersion,
        )
        register_publishable_models(Component, ComponentVersion)
        register_publishable_models(Container, ContainerVersion)
        register_publishable_models(Section, SectionVersion)
        register_publishable_models(Subsection, SubsectionVersion)
        register_publishable_models(Unit, UnitVersion)

    def ready(self):
        """
        Currently used to register publishable models.

        May later be used to register signal handlers as well.
        """
        self.register_publishable_models()
