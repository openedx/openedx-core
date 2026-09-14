Learning App
============

The ``openedx_learning`` app holds models and APIs for what learners are meant to achieve
and how they get there. Its sibling ``openedx_content`` holds the material itself.

Like ``openedx_content``, it is one Django app split into applets. Its first applet is
``cbe``, for Competency-Based Education; Learning Pathways are expected to follow.

In the layering that ``.importlinter`` enforces, this app sits above ``openedx_catalog``,
``openedx_content`` and ``openedx_tagging``. It may build on any of them; none may import it.
