Publishing App
==============

The ``publishing`` app holds the core data models that allow different apps will use to track the creation and publishing of new versions of content data. It is intended to have no external dependencies, though it may execute third-party code through plugin mechanisms at runtime.

Motivation
----------

Content publishing is no longer a simple operation where one system processes a set of changes. The act of publishing in Open edX requires many systems to update their data, often through asynchronous tasks. Because each system is doing this independently, we can get into weird states where some systems have updated their data, others will do so in the following minutes, and some systems have failed entirely–for instance, course outlines might not match course contents, or search indexing.

Intended Use Cases
------------------

* Create a new version of content for draft viewing purposes.
* Publish a new version of content.

The idea is that a new LearningPackageVersion is created, and an app builds all the data it needs for that version. Once all apps have built the necessary data, pointing the latest "published" version to be that version is a fast, atomic operation.


Architecture Guidelines
-----------------------

Things to remember:

* apps may come and go
* third party plugins to those apps may come and go
* historical content data from old versions does not have to be preserved.
* content **metadata** should have their full history preserved.
