20. openedx_content as an Umbrella App of Smaller Applets
=========================================================

Context
-------

Up to this point, Learning Core has used many small apps with a narrow focus (e.g. ``components``, ``collections``, etc.) in order to make each individual app simpler to reason about. This has been useful overall, but it has made refactoring more cumbersome. For instance:

#. Moving models between apps is tricky, requiring the use of Django's ``SeparateDatabaseAndState`` functionality to fake a deletion in one app and a creation in another without actually altering the database. It also requires doctoring the migration files for models in other repos that might have foreign key relations to the model being moved, so that they're pointing to the new ``app_label``.  This will be an issue when we try to extract container-related models and logic out of publishing and into a new ``containers`` app.
#. Renaming an app is also cumbersome, because the process requires creating a new app and transitioning the models over. This came up when trying to rename the ``contents`` app to ``media``.

There have also been minor inconveniences, like having a long list of ``INSTALLED_APPS`` to maintain in edx-platform over time, or not having these tables easily grouped together in the Django admin interface.

Decisions
---------

1. Single openedx_content App
~~~~~~~~~~~~~~~~~~~~~~~

All existing authoring apps will be merged into one Django app (``openedx_learning.app.openedx_content``). Some consequences of this decision:

- The tables will be renamed to have the ``openedx_content`` label prefix.
- All management commands will be moved to the ``openedx_content`` app.

2. Logical Separation via Applets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We will continue to keep internal API boundaries between individual applets, and use the ``api.py`` modules. This is both to insulate applets from implementation changes in other applets, as well as to provide a set of APIs that third-party plugins can utilize. As before, we will use Import Linter to enforce dependency ordering.

3. Restructuring Specifics
~~~~~~~~~~~~~~~~~~~~~~~~~~

In one pull request, we are going to:

#. Rename the ``openedx_learning.apps.authoring`` package to be ``openedx_learning.apps.openedx_content``.
#. Create bare shells of the existing ``authoring`` apps (``backup_restore``, ``collections``, ``components``, ``contents``, ``publishing``, ``sections``, ``subsections``, ``units``), and move them to the ``openedx_learning.apps.openedx_content.backcompat`` package. These shells will have an ``apps.py`` file and the ``migrations`` package for each existing app. This will allow for a smooth schema migration to transition the models from these individual apps to ``openedx_content``.
#. Move the actual models files and API logic for our existing authoring apps to the ``openedx_learning.apps.openedx_content.applets`` package.
#. Convert the top level ``openedx_learning.apps.openedx_content`` package to be a Django app. The top level ``admin.py``, ``api.py``, and ``models.py`` modules will do wildcard imports from the corresponding modules across all applet packages.

In terms of model migrations, all existing apps will have a final migration that uses ``SeparateDatabaseAndState`` to remove all model state, but make no actual database changes. The initial ``openedx_content`` app migration will then also use ``SeparateDatabaseAndState`` to create the model state without doing any actual database operations. The next ``openedx_content`` app migration will rename all existing database tables to use the ``openedx_content`` prefix, for uniformity.

The ordering of these migrations is important, and existing edx-platform migrations should remain unchanged. This is important to make sure that we do not introduce ordering inconsistencies for existing installations that are upgrading.

Therefore, the migrations will happen in the following order:

#. All ``backcompat.*`` apps migrations except for the final ones that delete model state. This takes us up to where migrations would already be before we make any changes.
#. The ``openedx_content`` app's ``0001_intial`` migration that adds model state without changing the database. At this point, model state exists for the same models in all the old ``backcompat.*`` apps as well as the new ``openedx_content`` app.
#. edx-platform apps that had foreign keys to old ``backcompat.*`` apps models will need to be switched to point to the new ``openedx_content`` app models. This will likewise be done without a database change, because they're still pointing to the same tables and columns.
#. Now that edx-platform references have been updated, we can delete the model state from the old ``backcompat.*`` apps and rename the underlying tables (in either order).

The tricky part is to make sure that the old ``backcompat.*`` apps models still exist when the edx-platform migrations to move over the references runs. This is problematic because the edx-platform migrations can only specify that they run *after the new openedx_content models are created*. They cannot specify that they run *before the old backcompat models are dropped*.

So in order to enforce this ordering, we do the following:

* The ``openedx_content`` migration ``0001_initial`` requires that all ``backcompat.*`` migrations except the last ones removing model state are run.
* The ``openedx_content`` migration ``0002_rename_tables_to_openedx_content`` migration requires that the edx-platform migrations changing refrences over run. This is important anyway, because we want to make sure those reference changes happen before we change any table names.
* The final ``backcompat.*`` migrations that remove model field state will list ``openedx_content`` app's ``0002_rename_tables_to_openedx_content`` as a dependency.

A further complication is that ``openedx_learning`` will often run its migrations without edx-platform present (e.g. for CI or standalone dev purposes), so we can't force ``0002_rename_tables_to_openedx_content`` in the ``openedx_content`` app to have references to edx-platform migrations. To get around this, we dynamically inject those migration dependencies only if we detect those edx-platform apps exist in the currently loaded Django project. This injection happens in the ``apps.py`` initialization for the ``openedx_content`` app.

The final complication is that we want these migration dependencies to be the same regardless of whether you're running edx-platform migrations with the LMS or CMS (Studio) settings, or we run the risk of getting into an inconsistent state and dropping the old models before all the edx-platform apps can run their migrations to move their references. To do this, we have to make sure that the edx-platform apps that reference Learning Core models are present in the ``INSTALLED_APPS`` for both configurations.

4. The Bigger Picture
~~~~~~~~~~~~~~~~~~~~~

This practice means that the ``openedx_content`` Django app corresponds to a Subdomain in Domain Driven Design terminology, with each applet being a Bounded Context. We call these "Applets" instead of "Bounded Contexts" because we don't want it to get confused for Django's notion of Contexts and Context Processors (or Python's notion of Context Managers).
