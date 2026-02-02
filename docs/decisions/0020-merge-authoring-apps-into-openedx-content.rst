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

#. Rename the ``openedx_learning.apps.authoring`` package to be ``openedx_learning.apps.openedx_content``. (Note: We have discussed eventually moving this to a top level ``openedx_content`` app instead of ``openedx_learning.apps.openedx_content``, but that will happen at a later time.)
#. Create bare shells of the existing ``authoring`` apps (``backup_restore``, ``collections``, ``components``, ``contents``, ``publishing``, ``sections``, ``subsections``, ``units``), and move them to the ``openedx_learning.apps.openedx_content.backcompat`` package. These shells will have an ``apps.py`` file, the ``migrations`` package for each existing app, and in some cases a minimal ``models.py`` that will hold the skeletons of a handful of models. This will allow for a smooth schema migration to transition the models from these individual apps to ``openedx_content``.
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
#. We must support people who are installing from scratch, those who are upgrading from the Ulmo release, as well as those who are running off of the master branch of openedx-platform.

Therefore, the migrations will happen in the following order:

#. All pre-existing ``backcompat.*`` apps migrations run as before.
#. New ``backcompat.*`` apps migrations that drop most model state, but leave the database unchanged.
#. The first ``openedx_content`` migration creates logical models without any database changes.
#. The second ``openedx_content`` migration renames the underlying tables.
#. Each the ``openedx-platform`` apps that had foreign keys to the old authoring apps will get a new migration that switches those foreign keys to point to ``openedx_content`` apps instead. These are: ``content_libraries``, ``contentstore``, and ``modulestore_migrator``.
#. The above ``openedx-platform`` apps will also get a squashed migration that sets them up to point to the new ``openedx_content`` models directly.

The tricky part is that all the ``opendx-learning`` migrations will run before any of the ``openedx-platform`` migrations run. We can't force it to do otherwise without making ``openedx-learning`` aware of ``opendx-platform``, and we explicitly want to avoid that. This makes things tricky with respect to the model state dependencies. There are two scenarios we have to worry about:

Migration from Scrach
  The ``openedx-platform`` apps will each run the squashed migration that jumps straight to making foreign keys against the new ``openedx_content`` models, so the fact that the old authoring app models have been removed and the tables have been renamed doesn't matter.

Migration from Ulmo/master
  No actual database operations have to happen here, as the keys were already created earlier. That being said, the migration framework will error out if the state of the old app models that it had foreign keys have been dropped entirely. That's why the bare skeletons of those old models are preserved in the ``backcompat`` app models files, along with their primary key field. Everything else can be dropped from the state point of view—though again, we're not modifying database state in this operation.

  The main downside of this approach is that it may break migrations for developers if they have a months old dev database that is in an in-between release state, e.g. after some ``modulestore_migrations`` referencing the old app mdoels were run, but before the most recent ``modulestore_migrations`` creating foreign keys to those models. No production environment is expected to deploy like this, so the main negative consequence is that the developer would have to drop and recreate their database.

5. Rejected Alternatives
~~~~~~~~~~~~~~~~~~~~~~~~

An earlier attempt at this tried to solve the migration ordering issue by dynamically injecting migration dependencies into the second ``openedx_content`` migration module during the app config ``ready()`` initialization. This was later abandoned because it didn't solve the problem of CMS vs LMS differences in ``INSTALLED_APPS``, so the ordering could still get corrupted unless we added those apps to LMS—which would have introduced more risk.

6. The Bigger Picture
~~~~~~~~~~~~~~~~~~~~~

This overall refactoring means that the ``openedx_content`` Django app corresponds to a Subdomain in Domain Driven Design terminology, with each applet being a Bounded Context. We call these "Applets" instead of "Bounded Contexts" because we don't want it to get confused for Django's notion of Contexts and Context Processors (or Python's notion of Context Managers).
