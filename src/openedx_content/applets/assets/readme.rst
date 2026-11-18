Assets Applet
=============

The ``assets`` applet holds the core data models that store "shared assets" used in courses and content libraries: individual files, such as images or PDF documents, that can be used by many Components in a Learning Package, or by none at all. See ADRs 0013 (Shared Static Assets) and 0014 (Referencing Assets) in ``docs/openedx_content/decisions/``.

Motivation
----------

Course "Files" have historically lived in the MongoDB contentstore, as a course-wide, unversioned namespace of files. We want to move them into ``openedx_content`` so that they get the same versioning, draft/publish and dependency tracking as the rest of the content in a Learning Package.

Intended Use Cases
------------------

* Storing every file in a course run's "Files" page (including files migrated from contentstore) as an ``Asset`` in the run's Learning Package.
* Storing files uploaded directly to a content library, rather than attached to a particular Component.
* Answering "which Components use this file?" and "which referenced files are missing?" with queries over ``PublishableEntityVersionDependency``, instead of parsing content.

Architecture Guidelines
-----------------------

* An ``Asset`` is a ``PublishableEntity`` that holds exactly one file. Each ``AssetVersion`` points to a single ``Media`` row. Related files are grouped by attaching them to one Component, or by putting the Assets in a Collection, not by putting several files in one Asset.
* An Asset is identified by its ``path`` (filename), which is unique and case-sensitive within a Learning Package and can never change, because content refers to Assets by filename. Look Assets up by ``path``; don't parse ``entity_ref``.
* ``permissions`` and ``allow_scripts`` are on the unversioned ``Asset``, so that changing them applies to every version. This applet only stores them. Permission checks and the decision to honor ``allow_scripts`` belong to the platform.
* An Asset with no versions is a *placeholder* for a file that is referenced but hasn't been uploaded yet. Uploading a file with that name (or with the name of a soft-deleted Asset) creates a new version of the existing Asset, so that existing dependencies on it are kept.
* Create and modify Assets only through ``api.py``. ``Asset.save()`` refuses direct saves, and ``AssetVersion`` is immutable.

See ADRs 0013 (Shared Static Assets) and 0014 (Referencing Assets) in ``docs/openedx_content/decisions/`` for more details.
