Components App
==============

The ``components`` app holds the versioned data models for the lowest-level pieces of content that can be stored in Open edX: Components (e.g. XBlocks), as well as the individual pieces of raw data content that they reference.

Motivation
----------

We want a small, extensible model for modeling the smallest pieces of content (e.g. individual blocks of XBlock content), that we will build more complex data on top of, like tagging.

Intended Use Cases
------------------



Architecture Guidelines
-----------------------

* We're keeping nearly unlimited history, so per-version metadata (i.e. the space/time cost of making a new version) must be kept low.
* Do not assume that all Components will be XBlocks.
* Encourage other apps to make models that join to (and add their own metadata to) Component, ComponentVersion, Content, etc. But it should be done in such a way that this app is not aware of them.
* Always preserve the most raw version of the data possible, e.g. OLX, even if XBlocks then extend that with more sophisticated data models. At some point those XBlocks will get deprecated/removed, and we will still want to be able to export the raw data.
* Exports should be fast and *not* require the invocation of plugin code.