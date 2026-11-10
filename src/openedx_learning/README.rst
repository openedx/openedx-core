Learning App
============

The ``openedx_learning`` app holds models and APIs for what learners are meant to achieve
and how they get there. Its sibling ``openedx_content`` holds the material itself.

Like ``openedx_content``, it is one Django app split into applets. Its first applet is
``cbe``, for Competency-Based Education; Learning Pathways are expected to follow.

In the layering that ``.importlinter`` enforces, this app sits above ``openedx_catalog``,
``openedx_content`` and ``openedx_tagging``. It may build on any of them; none may import it.

Setting up a consuming project
------------------------------

A project that uses this app's REST API needs three things in place. Its URLconf includes
``openedx_learning.urls`` under whatever prefix it likes, and the CBE API is then served under
``<prefix>cbe/v1/``. Its ``INSTALLED_APPS`` lists ``rules.apps.AutodiscoverRulesConfig``, not
plain ``rules``, because only the autodiscovering config imports each app's ``rules.py`` and so
registers the permissions declared there. Its ``AUTHENTICATION_BACKENDS`` includes
``rules.permissions.ObjectPermissionBackend``, the backend that answers ``has_perm`` from those
registered permissions. If either ``rules`` setting is missing, the API answers 403 to every
caller who is not a superuser, and nothing in the logs says why. Superusers pass every
``has_perm`` check regardless, so testing as one hides the problem.

.. code-block:: python

    # settings
    INSTALLED_APPS = [
        ...,
        "rules.apps.AutodiscoverRulesConfig",
        "openedx_learning",
    ]
    AUTHENTICATION_BACKENDS = [
        ...,
        "rules.permissions.ObjectPermissionBackend",
    ]

    # urls.py: serves the CBE API under api/cbe/v1/
    urlpatterns = [
        path("api/", include("openedx_learning.urls")),
    ]
