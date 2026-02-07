Collections App
==============

The ``collections`` app will enable content authors to group content together for
organizing and re-using content. Components can be part of several different collections.

Motivation
----------

With the Legacy Libraries ("v1"), people didn't have any way to organize content in libraries, so they
had to create many small libraries.

For the Libraries Relaunch ("v2"), we want to encourage people to create large libraries with lots of content,
and organize that content using tags and collections.

Architecture Guidelines
-----------------------

Things to remember:

* Collections may grow very large.
* Collections are not publishable in and of themselves.
* Collections link to PublishableEntity records, **not** PublishableEntityVersion records.
