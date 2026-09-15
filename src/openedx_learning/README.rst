Learning App
============

The ``openedx_learning`` app holds models and APIs for what learners are meant to achieve
and how they get there. Its sibling ``openedx_content`` holds the material itself.

Like ``openedx_content``, it is one Django app split into applets. ``cbe`` covers
Competency-Based Education; ``pathways`` covers Pathways - the versioned definition
of what a learner must complete to earn a larger achievement.

In the layering that ``.importlinter`` enforces, this app sits above ``openedx_catalog``,
``openedx_content``, and ``openedx_tagging``. It may build on any of them; none of them may
import it. ``pathways`` builds on ``openedx_content``'s publishing primitives to version
Pathway definitions and references ``openedx_catalog``'s ``CourseRun``. The catalog's
``CatalogPathway`` in turn points at a Pathway's ``PublishableEntity`` - which it can do
because ``openedx_catalog`` itself sits above ``openedx_content`` - and this app's API is
what sets and resolves that link.
