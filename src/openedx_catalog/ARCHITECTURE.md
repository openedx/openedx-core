# Catalog App Architecture Diagram

Here's a visual overview of how this app relates to other apps.

(_Note: to see the diagram below, view this on GitHub or view in VS Code with [a Markdown-Mermaid extension](https://marketplace.visualstudio.com/items?itemName=bierner.markdown-mermaid) enabled._)

```mermaid
---
config:
  theme: 'forest'
---
flowchart TB
    Catalog["**openedx_catalog** (CourseRun, CatalogCourse plus core metadata models, e.g. CourseSchedule. Other metadata models live in other apps but are 1:1 with CourseRun.)"]
    Content["**openedx_content**<br>The content of the course. (publishing, containers, components, media)"]
    Organizations["**edx-organizations** (Organization)"]
    Enrollments["**platform: enrollments** (CourseEnrollment, CourseEnrollmentAllowed)"]
    Modes["**platform: course_modes** (CourseMode)"]
    Catalog <-. "Direction of this relationship TBD." .-> Content
    Catalog -- References --> Organizations
    Enrollments -- References --> Modes
    Enrollments -- References --> Catalog

    style Enrollments fill:#ccc
    style Modes fill:#ccc
    style Organizations fill:#ccc

    Pathways["<a href='https://openedx.atlassian.net/wiki/spaces/OEPM/pages/5148147732/Brief+Modular+Content+Delivery+-+Platform+Strategy'>**openedx_pathways**</a> (Pathway, PathwaySchedule, PathwayEnrollment, PathwayCertificate, etc.)"]
    Pathways -- References --> Catalog

    style Pathways fill:#c0ffee,stroke-dasharray: 5 5

    FutureCatalog["Future discovery service - learner-oriented, pluggable, browse/search courses and programs"] -- References --> Catalog
    FutureCatalog <-- Plugin API --> Pathways
    style FutureCatalog fill:#ffc0ee,stroke-dasharray: 5 5
```