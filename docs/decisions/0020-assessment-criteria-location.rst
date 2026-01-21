20. Where in the codebase should CBE assessment criteria go?
============================================================

Context
-------
Competency Based Education (CBE) requires that the LMS have the ability to track learners' mastery of competencies through the means of assessment criteria. For example, in order to demonstrate that I have mastered the Multiplication competency, I need to have earned 75% or higher on Assignment 1 or Assignment 2\. The association of the competency, the threshold, the assignments, and the logical OR operator together make up the assessment criteria for the competency. Course Authors and Platform Administrators need a way to set up these associations in Studio so that their outcomes can be calculated as learners complete their materials. This is an important prerequisite for being able to display competency progress dashboards to learners and staff to make Open edX the platform of choice for those using the CBE model.

Decisions
---------
CBE Assessment Criteria, Student Assessment Criteria Status, and Student Competency Status values should go in the openedx-learning repository as there are broader architectural goals to refactor as much code as possible out of the edx-platform repository into the openedx-learning repository such that it can be designed in a way that is easy for plugin developers to utilize.

More specifically, all code related to adding Assessment Criteria to Open edX will live in openedx-learning/openedx_learning/apps/assessment_criteria.

This keeps a single cohesive Django app for authoring the criteria and for storing learner status derived from those criteria, which reduces cross-app dependencies and simplifies migrations and APIs. It also keeps Open edX-specific models (users, course identifiers, LMS/Studio workflows) out of the standalone ``openedx_tagging`` package and avoids forcing the authoring app to depend on learner runtime data. The tradeoff is that authoring and runtime concerns live in the same app; if learner status needs to scale differently or be owned separately in the future, a split into a dedicated status app can be revisited. Alternatives that externalize runtime status to analytics/services or split repos introduce operational and coordination overhead that is not justified at this stage.

Rejected Alternatives
---------------------
1. edx-platform repository  
    - Pros: This is where all data currently associated with students is stored, so it would match the existing pattern and reduce integration work for the LMS.  
    - Cons: The intention is to move core learning concepts out of edx-platform (see `0001-purpose-of-this-repo.rst`_), and keeping it there makes reuse and pluggability harder.  
2. All code related to adding Assessment Criteria to Open edX goes in openedx-learning/openedx\_learning/apps/authoring/assessment\_criteria  
    - Pros:   
        - Tagging and assessment criteria are part of content authoring workflows as is all of the other code in this directory.  
        - All other elements using the Publishable Framework are in this directory.  
    - Cons:   
        - We want each package of code to be independent, and this would separate assessment criteria from the tags that they are dependent on.  
        - Assessment criteria also includes learner status and runtime evaluation, which do not fit cleanly in the authoring app.  
        - The learner status models in this feature would have a ForeignKey to settings.AUTH_USER_MODEL, which is a runtime/learner concern. If those models lived under the authoring app, then the authoring app would have to import and depend on the user model, forcing an authoring-only package to carry learner/runtime dependencies. This may create unwanted coupling.  
3. New Assessment Criteria Content tables will go in openedx-learning/openedx_learning/openedx_tagging/core/assessment_criteria. New Student Status tables will go in openedx-learning/student_status.  
    - Pros:  
        - Keeps assessment criteria in the same package as the tags that they are dependent on.  
    - Cons:   
        - `openedx_tagging` is intended to be a standalone library without Open edX-specific dependencies (see `0007-tagging-app.rst`_); assessment criteria would violate that boundary.  
        - Splitting Assessment Criteria and Student Statuses into two apps would require cross-app foreign keys (e.g., status rows pointing at criteria/tag rows in another app), migration ordering and dependency declarations to ensure tables exist in the right order, and shared business logic or APIs for computing/updating status that now must live in one app but reference models in the other.  
4. Split assessment criteria and learner statuses into two apps inside openedx-learning/openedx\_learning/apps (e.g., assessment\_criteria and learner\_status)  
    - Pros:  
        - Clear separation between authoring configuration and computed learner state.  
        - Could allow different storage or scaling strategies for status data.  
    - Cons:  
        - Still introduces cross-app dependency and coordination for a single feature set.  
        - May be premature for the POC; adds overhead without proven need.
5. Store learner status in a separate service  
    - Pros:  
        - Scales independently and avoids write-heavy tables in the core app database.  
        - Could potentially reuse existing infrastructure for grades.  
    - Cons:  
        - Introduces eventual consistency and more integration complexity for LMS/Studio views.  
        - Requires additional infrastructure and operational ownership.  
6. Split authoring and runtime into separate repos/packages  
    - Pros:  
        - Clear ownership boundaries and independent release cycles.  
    - Cons:  
        - Adds packaging and versioning overhead for a tightly coupled domain.  
        - Increases coordination cost for migrations and API changes.
