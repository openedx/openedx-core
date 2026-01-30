Open edX Core: Foundational Packages for a Teaching & Learning Platform
=======================================================================

|pypi-badge| |ci-badge| |codecov-badge| |doc-badge| |pyversions-badge|
|license-badge|

Overview
--------

*Formerly known as "Learning Core" or "openedx-learning".*

The ``openedx-core`` project holds Django apps which represent core teaching & learning platform concepts.

Each app exposes stable, public API of Python functions and Django models. Some apps additionally provides REST APIs. These APIs are suitable for use in ``openedx-platform`` as well as in community-developed Open edX plugins.

Motivation
----------

The short term goal of this project is to create a small, extensible core that is easier to reason about and write extensions for than ``openedx-platform``. The longer term goal is to create a more nimble core learning platform, enabling rapid experimentation and drastic changes to the learner experience that are difficult to implement with Open edX today.

Replacing ``openedx-platform`` is explicitly *not* a goal of this project, as only a small fraction of the concepts in openedx-platform make sense to carry over here. When these core concepts are extracted and the data migrated, openedx-platform will import apps from this repo and make use of their public in-process APIs.

Architecture
------------

Open edX Core Package Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Open edX Core code should never import from ``openedx-platform``.

We want to be very strict about dependency management internally as well. Please read the `.importlinter config file <.importlinter>`_ file and the `Python API Conventions ADR <docs/decisions/0016-python-public-api-conventions>`_ for more details.


Model Conventions
~~~~~~~~~~~~~~~~~

We have a few different identifier types in the schema, and we try to avoid ``_id`` for this because Django uses that convention to reference IDs in other models/tables. So instead we have:

* ``id`` is the auto-generated, internal row ID and primary key. This never changes. Data models should make foreign keys to this field, as per Django convention.
* ``uuid`` is a randomly generated UUID4. This is the stable way to refer to a row/resource from an external service. This never changes. This is separate from ``id`` mostly because there are performance penalties when using UUIDs as primary keys with MySQL.
* ``key`` is intended to be a case-sensitive, alphanumeric key, which holds some meaning to library clients. This is usually stable, but can be changed, depending on the business logic of the client. The apps in this repo should make no assumptions about it being stable. It can be used as a suffix. Since ``key`` is a reserved name on certain database systems, the database field is ``_key``.
* ``num`` is like ``key``, but for use when it's strictly numeric. It can also be used as a suffix.

See Also
~~~~~~~~

The structure of this repo follows [OEP-0049](https://open-edx-proposals.readthedocs.io/en/latest/architectural-decisions/oep-0049-django-app-patterns.html) where possible, and also borrows inspiration from:

* [Scaling Django to 500 apps](https://2021.djangocon.us/talks/scaling-django-to-500-apps/) (Dan Palmer, DjangoCon US 2021)
* [Django structure for scale and longevity](https://www.youtube.com/watch?v=yG3ZdxBb1oo) (Radoslav Georgiev, EuroPython 2018)

Code Overview
-------------

* ``./src/``: All published code. Packages are importable relative to this directory (e.g., ``import openedx_content``). See ``readme.rst`` in each sub-folder.
* ``./tests/``: Unit tests (not published).
* ``./test_utils/``: Internal helpers for unit tests (not published).
* ``./olx_importer/``: Internal utility for importing data for development (not published).

License
-------

The code in this repository is licensed under the AGPL 3.0 unless otherwise noted.

Please see `LICENSE.txt <LICENSE.txt>`_ for details.

How To Contribute
-----------------

This repo is in a very experimental state. Discussion using GitHub Issues is welcome, but you probably don't want to make contributions as everything can shift around drastically with little notice.

Reporting Security Issues
-------------------------

Please do not report security issues in public. Please email security@openedx.org.

Help
----

If you're having trouble, we have discussion forums at https://discuss.openedx.org where you can connect with others in the community.

Our real-time conversations are on Slack. You can request a `Slack invitation`_, then join our `community Slack workspace`_.

For more information about these options, see the `Getting Help`_ page.

.. _Slack invitation: https://openedx.org/slack
.. _community Slack workspace: https://openedx.slack.com/
.. _Getting Help: https://openedx.org/getting-help

.. |pypi-badge| image:: https://img.shields.io/pypi/v/openedx-core.svg
    :target: https://pypi.python.org/pypi/openedx-core/
    :alt: PyPI

.. |ci-badge| image:: https://github.com/openedx/openedx-core/workflows/Python%20CI/badge.svg?branch=master
    :target: https://github.com/openedx/openedx-core/actions
    :alt: CI

.. |codecov-badge| image:: https://codecov.io/github/edx/openedx-core/coverage.svg?branch=master
    :target: https://codecov.io/github/edx/openedx-core?branch=master
    :alt: Codecov

.. |doc-badge| image:: https://readthedocs.org/projects/openedx-core/badge/?version=latest
    :target: https://openedx-core.readthedocs.io/en/latest/
    :alt: Documentation

.. |pyversions-badge| image:: https://img.shields.io/pypi/pyversions/openedx-core.svg
    :target: https://pypi.python.org/pypi/openedx-core/
    :alt: Supported Python versions

.. |license-badge| image:: https://img.shields.io/github/license/edx/openedx-core.svg
    :target: https://github.com/openedx/openedx-core/blob/master/LICENSE.txt
    :alt: License
