21. Where in the codebase should CBE assessment criteria go?
============================================================

Context
-------
Competency Based Education (CBE) requires that the LMS have the ability to track learners' mastery of competencies through the means of assessment criteria. For example, in order to demonstrate that I have mastered the Multiplication competency, I need to have earned 75% or higher on Assignment 1 or Assignment 2\. The association of the competency, the threshold, the assignments, and the logical OR operator together make up the assessment criteria for the competency. Course Authors and Platform Administrators need a way to set up these associations in Studio so that their outcomes can be calculated as learners complete their materials. This is an important prerequisite for being able to display competency progress dashboards to learners and staff to make Open edX the platform of choice for those using the CBE model.

Decisions
---------
CBE Assessment Criteria, Student Assessment Criteria Status, and Student Competency Status values should go in the openedx-learning repository as there are broader architectural goals to refactor as much code as possible out of the edx-platform repository into the openedx-learning repository such that it can be designed in a way that is easy for plugin developers to utilize. Additionally, we intend to treat CBE features as core features of Open edX rather than optional plugins, and as a result, CBE assessment criteria and learner status should live in the learning core rather than in a separate new repo.

Given the current refactor of openedx-learning (see `0020-merge-authoring-apps-into-openedx-content.rst <0020-merge-authoring-apps-into-openedx-content.rst>`_), we will place CBE code inside the top-level ``openedx_learning`` app as an applet, alongside Learning Pathways. The intended layout is:

::

    src/
         openedx_catalog
         openedx_content
         openedx_learning
         openedx_tagging
    src/openedx_learning/applets/learning_pathways
    src/openedx_learning/applets/cbe

The umbrella ``openedx_learning`` app will import from those applets in the same way that ``openedx_content`` does today.

This placement also keeps CBE close to shared learning-domain concepts that are already part of ``openedx_learning``, minimizing duplication and cross-package import issues. A separate repo would have added packaging/CI/release/migration coordination overhead for a tightly coupled core feature. Finally, applets provide a clean migration path: we can evolve or split responsibilities within ``openedx_learning`` later without the extra cost of deprecating a standalone package.

Rejected Alternatives
---------------------
1. Put all CBE assessment criteria and learner status in a single ``openedx_learning`` app under ``openedx_learning/apps/assessment_criteria``  
    - Pros:  
        - Keeps a single cohesive Django app for authoring criteria and storing learner status, reducing cross-app dependencies and simplifying migrations and APIs.  
        - Keeps Open edX-specific models (users, course identifiers, LMS/Studio workflows) out of the standalone ``openedx_tagging`` package and avoids forcing an authoring-only app to depend on learner runtime data.  
    - Cons:  
        - Does not align with the applet-based top-level structure being created in openedx-learning (``openedx_learning/applets/...``).  
2. edx-platform repository  
    - Pros: This is where all data currently associated with students is stored, so it would match the existing pattern and reduce integration work for the LMS.  
    - Cons: The intention is to move core learning concepts out of edx-platform (see `0001-purpose-of-this-repo.rst <0001-purpose-of-this-repo.rst>`_), and keeping it there makes reuse and pluggability harder.  
3. All code related to adding Assessment Criteria to Open edX goes in openedx-learning/openedx\_learning/apps/authoring/assessment\_criteria  
    - Pros:   
        - Tagging and assessment criteria are part of content authoring workflows as is all of the other code in this directory.  
        - All other elements using the Publishable Framework are in this directory.  
    - Cons:   
        - We want each package of code to be independent, and this would separate assessment criteria from the tags that they are dependent on.  
        - Assessment criteria also includes learner status and runtime evaluation, which do not fit cleanly in the authoring app.  
        - The learner status models in this feature would have a ForeignKey to settings.AUTH_USER_MODEL, which is a runtime/learner concern. If those models lived under the authoring app, then the authoring app would have to import and depend on the user model, forcing an authoring-only package to carry learner/runtime dependencies. This may create unwanted coupling.  
4. New Assessment Criteria Content tables will go in openedx-learning/openedx_learning/openedx_tagging/core/assessment_criteria. New Student Status tables will go in openedx-learning/student_status.  
    - Pros:  
        - Keeps assessment criteria in the same package as the tags that they are dependent on.  
    - Cons:   
        - `openedx_tagging` is intended to be a standalone library without Open edX-specific dependencies (see `0007-tagging-app.rst <0007-tagging-app.rst>`_) assessment criteria would violate that boundary.  
        - Splitting Assessment Criteria and Student Statuses into two apps would require cross-app foreign keys (e.g., status rows pointing at criteria/tag rows in another app), migration ordering and dependency declarations to ensure tables exist in the right order, and shared business logic or APIs for computing/updating status that now must live in one app but reference models in the other.  
5. Split assessment criteria and learner statuses into two apps inside openedx-learning/openedx\_learning/apps (e.g., assessment\_criteria and learner\_status)  
    - Pros:  
        - Clear separation between authoring configuration and computed learner state.  
        - Could allow different storage or scaling strategies for status data.  
    - Cons:  
        - Still introduces cross-app dependency and coordination for a single feature set.  
        - May be premature for the POC; adds overhead without proven need.
6. Store learner status in a separate service  
    - Pros:  
        - Scales independently and avoids write-heavy tables in the core app database.  
        - Could potentially reuse existing infrastructure for grades.  
    - Cons:  
        - Introduces eventual consistency and more integration complexity for LMS/Studio views.  
        - Requires additional infrastructure and operational ownership.  
7. Split authoring and runtime into separate repos/packages  
    - Pros:  
        - Clear ownership boundaries and independent release cycles.  
    - Cons:  
        - Adds packaging and versioning overhead for a tightly coupled domain.  
        - Increases coordination cost for migrations and API changes.
8. Migrate grading signals to openedx-events now and have openedx-learning consume events directly  
    - Pros:  
        - Aligns with the long-term direction of moving events out of edx-platform.  
        - Avoids a shim app in edx-platform and reduces tech debt.  
    - Cons:  
        - Requires cross-repo coordination and work beyond the current scope.  
        - Depends on changes to openedx-events that are not yet scheduled or ready.
