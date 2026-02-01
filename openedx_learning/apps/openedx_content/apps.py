"""
App Config for our umbrella openedx_content app.
"""
# pylint: disable=import-outside-toplevel
#
# Local imports in AppConfig.ready() are common and expected in Django, since
# Django needs to run initialization before before we can query for things like
# models, settings, and app config.

from importlib import import_module

from django.apps import AppConfig


class ContentConfig(AppConfig):
    """
    Initialization for all applets must happen in here.
    """

    name = "openedx_learning.apps.openedx_content"
    verbose_name = "Learning Core > Content"
    default_auto_field = "django.db.models.BigAutoField"
    label = "openedx_content"

    def patch_migration_dependencies(self):
        """
        Hacky initialization to preserve migration order.

        We consolidated a number of smaller apps into a single openedx_content
        app. In the process of doing so, we transitioned the models those apps
        by using SeparateDatabaseAndState to remove models in the small apps and
        add them to the new openedx_content app without actually doing any
        database operations.

        One unfortunate consequence of this is that we need to make sure that
        the openedx-platform migrations that switch their references from the
        old authoring models to the new ones in openedx_content run *after* the
        openedx_content 0001_initial migration, but *before* the backcompat
        migrations that drop the model state for the old authoring apps. To do
        this, we make it so that our 0002_rename_tables_to_openedx_content has
        these openedx-platform migrations as dependencies, and then make it so
        that the "000x_remove_all_field_state_for_move_to_applet" migrations all
        list 0002_rename_tables_to_openedx_content as a dependency.

        Thus we force this ordering:

        1. New model state created in openedx_content.
        2. openedx-platform apps switch model foreign key references from old
           authoring apps models to openedx_content models.
        3. We rename the openedx_content tables to be properly prefixed with our
           app label.
        4. Only at this point can the backcompat migrations dropping model field
           state run on the old authoring apps.

        The only problem is that our migrations only sometimes run in the
        openedx-platform project. When we're running in CI or other places,
        having references to openedx-platform migrations would break. So that's
        why we do this really sketchy looking migration dependency injection on
        app initialization.

        For more details, see docs/decisions/0020-merge-authoring-apps-into-openedx-content.rst.
        """
        from django.apps import apps

        # We can't directly import Python modules that start with a number using
        # an import statement, so we have to use import_module to bring in the
        # migration module.
        migration_module = import_module(
            ".migrations.0002_rename_tables_to_openedx_content",
            package=__package__,
        )
        deps_to_inject = [
#            ('content_libraries', '0012_alter_contentlibrary_learning_package'),
#            ('contentstore', '0015_alter_componentlink_upstream_block_and_more'),
#            ('modulestore_migrator', '0007_alter_modulestoreblockmigration_change_log_record_and_more'),
        ]
        our_deps = migration_module.Migration.dependencies
        for edx_platform_app, edx_platform_migration in deps_to_inject:
            if edx_platform_app in apps.app_configs.keys():
                our_deps.append((edx_platform_app, edx_platform_migration))

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
        Currently used to register publishable models and patch migrations.

        May later be used to register signal handlers as well.
        """
        self.patch_migration_dependencies()
        self.register_publishable_models()
