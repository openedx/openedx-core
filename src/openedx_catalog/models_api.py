"""
Core models available for use in other apps. These are mostly meant to be used
as foreign key targets. Each model should be considered read-only and only
mutated using API methods available in `openedx_catalog.api`.

See the `openedx_catalog.api` docstring for much more details.
"""

from .models import CatalogCourse, CourseRun
