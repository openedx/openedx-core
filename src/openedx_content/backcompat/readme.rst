Backwards Compatibility App Package
===================================

The apps in this package should not be modified. They are a byproduct of our migration from having a bunch of little authoring apps to having one unified app. They exist to provide backwards compatibilty for database migrations (see `<0020-authoring-as-one-app.rst>`_).

At some point in the future, we will remove this package and modify the initial migration for the ``authoring`` app to actually create the models for real, instead of using ``SeparateDatabaseAndState`` to fake the database side of the migration. For anyone who has already run the ``openedx_content`` migrations, the modified initial migration won't run anyway. Anyone setting things up for the first time would get the ``openedx_content`` models created without the intermediate steps of creating all the smaller app models first and renaming them. We should not do this before the Willow release, but there's no real downside to doing it later.
