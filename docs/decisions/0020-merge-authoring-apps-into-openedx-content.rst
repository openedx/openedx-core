20. Merge authoring apps into openedx_content (using Applets)
=============================================================

Context
-------

Up to this point, Learning Core has used many small apps with a narrow focus (e.g. ``components``, ``collections``, etc.) in order to make each individual app simpler to reason about. This has been useful overall, but it has made refactoring more cumbersome. For instance:

#. Moving models between apps is tricky, requiring the use of Django's ``SeparateDatabaseAndState`` functionality to fake a deletion in one app and a creation in another without actually altering the database. Moving models also introduces tricky dependencies with respect to migration ordering (described in more detail later in this document). We encountered this when considering how to extract container functionality out of the ``publishing`` app.
#. Renaming an app is also cumbersome, because the process requires creating a new app and transitioning the models over. This came up when trying to rename the ``contents`` app to ``media``.

There have also been minor inconveniences, like having a long list of ``INSTALLED_APPS`` to maintain in openedx-platform over time, or not having these tables easily grouped together in the Django admin interface.

Decisions
---------

1. Single openedx_content App
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

All existing authoring apps will be merged into one Django app named ``openedx_content``. Some consequences of this decision:

- The tables will be renamed to have the ``openedx_content`` label prefix.
- All management commands will be moved to the ``openedx_content`` app.

2. Logical Separation via Applets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We will continue to keep internal API boundaries by using a new "Applets" convention. A Django applet is made to look like a miniature Django app, with its own ``models.py``, ``api.py``, and potentially other modules. The modules for the old authoring apps will be copied into various subpackages of ``openedx_content.applets``, such as ``openedx_content.applets.publishing``. Applets should respect each others' API boundaries, and never directly query models across applets. As before, we will use Import Linter to enforce dependency ordering.

3. Package Restructuring
~~~~~~~~~~~~~~~~~~~~~~~~

In one pull request, we are going to:

#. Rename the ``openedx_learning.apps.authoring`` package to be ``openedx_learning.apps.openedx_content``. (Note: We have discussed eventually moving this to a top level app, i.e. ``openedx_content`` instead of ``openedx_learning.apps.openedx_content``, but that will happen at a later time.)
#. Create bare shells of the existing ``authoring`` apps (``backup_restore``, ``collections``, ``components``, ``contents``, ``publishing``, ``sections``, ``subsections``, ``units``), and move them to the ``openedx_learning.apps.openedx_content.backcompat`` package. These shells will have an ``apps.py`` file, the ``migrations`` package for each existing app, and in some cases a minimal ``models.py`` that will hold the bare skeletons of a handful of models. This will allow for a smooth schema migration to transition the models from these individual apps to ``openedx_content``.
#. Move the actual models files and API logic for our existing authoring apps to the ``openedx_learning.apps.openedx_content.applets`` package.
#. Convert the top level ``openedx_learning.apps.openedx_content`` package to be a Django app. The top level ``admin.py``, ``api.py``, and ``models.py`` modules will do wildcard imports from the corresponding modules across all applet packages.
#. Test packages will also be updated to follow the new structure.

4. Database Migration Specifics
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When Django runs migrations, it calculates both an internal model state, as well as running database operations. We are going to take advantage of the fact that these two can be separated using the ``SeparateDatabaseAndState`` operation. We will use this to remove the model state from the old authoring apps and create the model state in the new ``openedx_content`` app without having to run database operations.

There are a few high level constraints that we have to consider:

#. Existing openedx-platform migrations should not be modified. Existing openedx-platform migrations should remain unchanged. This is important to make sure that we do not introduce ordering inconsistencies for sites that have already run migrations for the old apps and are upgrading to a new release (e.g. Verawood).
#. The openedx-learning repo should not have any dependencies on openedx-platform migrations, because our dependencies strictly go in the other direction: openedx-platform calls openedx-learning, not the other way around. Furthermore, openedx-learning will often be run without openedx-platform, such as for local development or during CI.
#. Two of the openedx-platform apps that have foreign keys to openedx-learning models are only in Studio's INSTALLED_APPS (``contentstore`` and ``modulestore_migrator``), while ``content_libraries`` is installed in both Studio and LMS. Migrations may be run for LMS or Studio first, depending on the user and environment. Tutor runs LMS first, but we can't assume that will always be true.
#. We must support people who are installing from scratch, as well as those who are upgrading from the Ulmo release.





Therefore, the migrations will happen in the following order:

#. All ``backcompat.*`` apps migrations
#. The first ``openedx_content`` migration creates logical models without any database changes.
#. The second ``openedx_content`` migration renames the underlying tables.
#. Each the openedx-platform apps that had foreign keys to



Rejected

Dynamically


.
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
