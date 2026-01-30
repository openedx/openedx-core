21. Learning Core is now Open edX Core
======================================

Context
-------

When this repo was created, it was intended to only encompass learning concepts. The content models were supposed to be read-optimized LMS-friendly representations of learning content. It was imagined that the authoring models would live elsewhere (e.g. Blockstore). That's why this was called "Learning Core".

Over time, we realized that we actually needed to start by developing write-optimized CMS-friendly models, many of which are ideal to be also be used in LMS. We ended up with an "Authoring API" which powered the Studio-only Content Libraries feature.

Now, we are working on CBE, Pathways, and Catalog-realted features in this repo. There's also still a Tagging API in this repo. So, the scope of Learning Core has increased, although it still makes sense to develop these important foundational, stable packages together in one core repository.

Decision
--------

The Learning Core is now the Open edX Core. The openedx-learning repository is now openedx-core, as is the installable PyPI project. It's a place to put foundational teaching and learning models and stable APIs. It is **not** a place to put code that is periphery, highly feature-specific, or lacking a path towards stability.

The Open edX Core will consist of several Django apps, each implementing a cohesive yet significant platform concern. Each app should expose a Python API (at ``.api``) and a models API (ideally at ``.models_api``), and optionally a REST API (at ``.rest_api``). We'll use importlinter to enforce boundaries between the apps. In the future, if we move to uv, we may manage these as `uv workspaces <https://docs.astral.sh/uv/concepts/projects/workspaces/>`_.

We will initially have two top-level Django applications:

* ``openedx_content``
* ``openedx_tagging``

We expect it to grow a few more top-level Django applications, including:

* ``openedx_learning`` (learner-facing models, similar to the original intent of Learning Core).
* ``openedx_catalog`` (CourseRun, etc.)
* ``openedx_cbe`` (Models related to pathways, credential-based education. Still in discussion).

Long term, we are open to more stable, core APIs moving in, such as:

* ``openedx_events``
* ``openedx_filters``
* ``openedx_authz``
* ``openedx_keys`` (renamed from ``opaque_keys``)

There are dependencies between these apps we'd want to keep in mind. For example, we wouldn't want to move in ``openedx_keys`` without moving in ``openedx_authz`` first, otherwise we'd create a cyclical dependency between ``openex-authz`` and ``openedx-core``.

By keeping each app as a top-level package and by using importlinter, we leave the door open for packages to be removed later. For example, if it becomes cumbersome to maintain ``openedx_catalog`` alongside the other core apps, then it should be possible to extract it into a separate ``openedx-catalog`` repo without breaking any external instances of ``from openedx_catalog import ...``.

Consequences
------------

We'll implement this change immediately as detailed in https://github.com/openedx/openedx-core/issues/470

Rejected alternatives
---------------------

* Separate repos for ``openedx-content``, ``openedx-cbe``, etc. 
  
  * Axim is making a conscious effort to slow the proliferation of new repos, as it has been challenging to maintain consistent standards, tooling, and upgrades across all of them. If there is not a strong reason to separate repos, then we would prefer to start off with a single repo.

* A combined top level Python package with nested apps: ``openedx_core.content.api``, ``openedx_core.cbe.api``, etc.

  * This would make it harder to split apps out into separate repos later, because it would involve either (a) updating all the import statements or (b) having the ``openedx_core`` namespace split across multiple repos. We'd like to remain flexible with code reorganization given the fast-moving and experimental nature of these projects.
