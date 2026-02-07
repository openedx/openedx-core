Tagging App
==============

The ``tagging`` app will enable content authors to tag pieces of content and quickly
filter for ease of re-use.

Motivation
----------

Tagging content is central to enable content re-use, facilitate the implementation
of flexible content structures different from the current implementation and
allow adaptive learning in the Open edX platform.

This service has been implemented as pluggable django app. Since it is necessary for
it to work independently of the content to which it links to.

Setup
---------

**TODO:** We need to wait the discussion of the `Taxonomy discovery <https://docs.google.com/document/d/13zfsGDfomSTCp_G-_ncevQHAb4Y4UW0d_6N8R2PdHlA/edit#heading=h.o6fm1hktwp7b>`_.
to build a proper setup for linking different pieces of content.

The current approach is to save the id of the content through a generic string, 
so that the tag can be linked to any type of content, no matter what type of ID have.
For example, with this approach we can link standalone blocks and library blocks,
both on Denver (v3) and Blockstore Content Libraries (v2).
