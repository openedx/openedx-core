Learning Core: Catalog App
==========================

Overview
--------

The ``openedx_catalog`` Django apps provides core models to represent all courses in the Open edX platform. Higher-level apps can build on these models to implement features like enrollment, grading, scheduling, exams, and much more.

Motivation
----------

The existing ``CourseOverview`` model in ``openedx-platform`` is derived from various places, but mostly from the metadata fields of the root ``Course`` object stored in modulestore (MongoDB) for each course. As we slowly transition toward storing course content fully in Learning Core (in ``openedx_content``), we want to move to storing all course data and metadata in these sort of MySQL models. We're creating this new ``CourseRun`` model in ``openedx_catalog`` to support these goals:

1. Provide a core model to represent each course, for foreign key purposes.
2. To allow provisioning placeholder courses before any content even exists.
3. To be much simpler and more performant than ``CourseOverview`` was (far fewer fields generally, fewer legacy fields, integer primary key).
4. Perhaps to provide a transition mechanism, a pointer than can point either to modulestore content or learning core content, as we transition content storage.

Architecture
------------

See `the architecture diagram <./ARCHITECTURE.md>`__. (Because we use RST for all Python READMEs, it cannot be embedded here directly.)
