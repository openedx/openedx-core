"""
App Config for our umbrella openedx_learning app.
"""
from django.apps import AppConfig

# pylint: disable=import-outside-toplevel
#
# Local imports in AppConfig.ready() are common and expected in Django, since
# Django needs to run initialization before we can query for things like models,
# settings, and app config.


class LearningConfig(AppConfig):
    """
    Initialization for all applets must happen in here.
    """

    name = "openedx_learning"
    verbose_name = "Open edX Core > Learning"
    default_auto_field = "django.db.models.BigAutoField"
    label = "openedx_learning"

    def register_publishable_models(self):
        """
        Register all Publishable -> Version model pairings in our app.
        """
        from openedx_content.api import register_publishable_models

        from .models import Pathway, PathwayItem, PathwayItemVersion, PathwayVersion

        register_publishable_models(Pathway, PathwayVersion)
        register_publishable_models(PathwayItem, PathwayItemVersion)

    def ready(self):
        """
        Currently used to register publishable models.

        May later be used to register signal handlers as well.
        """
        self.register_publishable_models()
