Courses Applet
==============

The ``courses`` applet holds the ``CourseContent`` model, which maps each course run (``openedx_catalog.CourseRun``) to the ``LearningPackage`` that holds its content. See ADR 0012 (Course Learning Packages) in ``docs/openedx_content/decisions/``.

Architecture Guidelines
-----------------------

* Each course run has zero or one ``CourseContent`` rows.
* By convention, each course run gets its own ``LearningPackage``, but several ``CourseContent`` rows may point to the same ``LearningPackage``. Code must never assume the relationship is 1:1.
* Code that needs a course run's ``LearningPackage`` must look it up through this applet's API, never by ``package_ref``, and never derive a course key from a ``package_ref``.
* ``CourseContent`` is not a "Course" model and should not be used as a foreign key target. Use ``CourseRun`` instead.
