23. How should versioning be handled for CBE competency achievement criteria?
=================================================================

Context
-------
Course Authors and/or Platform Administrators will be entering the competency achievement criteria rules in Studio that learners are required to meet in order to demonstrate competencies. Depending on the institution, these Course Authors or Platform Administrators may have a variety of job titles, including Instructional Designer, Curriculum Designer, Instructor, LMS Administrator, Faculty, or other Staff. 

Typically, only one person would be responsible for entering competency achievement criteria rules in Studio for each course, though this person may change over time. However, entire programs could have many different Course Authors or Platform Administrators with this responsibility. 

Typically, institutions and instructional designers do not change the mastery requirements (competency achievement criteria) for their competencies frequently over time. However, the ability to do historical audit logging of changes within Studio can be a valuable feature to those who have mistakenly made changes and want to revert or those who want to experiment with new approaches.

Currently, Open edX always displays the latest edited version of content in the Studio UI and always shows the latest published version of content in the LMS UI, despite having more robust version tracking on the backend (Publishable Entities). Publishable Entities for Libraries is currently inefficient for large nested structures because all children are copied any time an update is made to a parent.

Authoring data (criteria definitions) and runtime learner data (status) have different governance needs. The former is long-lived and typically non-PII, while the latter is user-specific, can be large (learners x criteria/competencies x time), and may require stricter retention and access controls. These differing lifecycles can make deep coupling of authoring and runtime data harder to manage at scale. Performance is also a consideration as computing or resolving versioned criteria for large courses could add overhead in Studio authoring screens or LMS views.

Decision
--------
The django-simple-history package will be used to track changes to competency achievement criteria definitions in Studio, providing an audit log of changes and the ability to revert to previous versions if needed. This approach allows us to capture the history of changes without the overhead of implementing a full publishable versioning framework for this initial implementation. The LMS will continue to display the latest published version of the criteria, consistent with current behavior, while Studio will show the latest edited version. This decision balances the need for version tracking and auditability with the desire to keep the initial implementation lightweight and performant, while still allowing for future enhancements to versioning if needed based on user feedback and evolving requirements.

Rejected Alternatives
---------------------

1. Defer competency achievement criteria versioning for the initial implementation. Store only the latest authored criteria and expose the latest published state in the LMS, consistent with current Studio/LMS behavior. 
    - Pros: 
        - Keeps the initial implementation lightweight
        - Avoids the publishable framework's known inefficiencies for large nested structures
    - Cons:
        - There is no built-in rollback or audit history
        - Adding versioning later will require data migration and careful choices about draft vs published defaults
2. Each model indicates version, status, and audit fields
    - Pros:   
        - Simple and familiar pattern (version + status + created/updated metadata)  
        - Straightforward queries for the current published state  
        - Can support rollback by marking an earlier version as published  
        - Stable identifiers (original_ids) can anchor versions and ease potential future migrations  
    - Cons:  
        - Requires custom conventions for versioning across related tables and nested groups  
        - Lacks shared draft/publish APIs and immutable version objects that other authoring apps can reuse  
        - Not necessarily consistent with existing patterns in the codebase (though these are already not overly consistent).   
3. Publishable framework in openedx-learning  
    - Pros:  
        - First-class draft/published semantics with immutable historical versions  
        - Consistent APIs and patterns shared across other authoring apps  
    - Cons:  
        - Inefficient for large nested structures because all children are copied for each new parent version  
        - Requires modeling criteria/groups as publishable entities and wiring Studio/LMS workflows to versioning APIs
        - Adds schema and migration complexity for a feature that does not yet require full versioning
4. Append-only audit log table (event history)  
    - Pros:  
        - Lightweight way to capture who changed what and when  
        - Enables basic rollback by replaying or reversing events  
    - Cons:  
        - Requires custom tooling to reconstruct past versions  
        - Does not align with existing publishable versioning patterns
