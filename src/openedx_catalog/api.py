"""
Open edX Core Catalog API

Note: this is currently a very minimal API. At this point, the openedx_catalog
app mainly exists to provide core models that represent "catalog courses" and
"course runs" for use by foreign keys across the system.

If a course "exists" in the system, you can trust that it will exist as a
CatalogCourse and CourseRun row in this openedx_catalog app, and use those as
needed when creating foreign keys in various apps. This should be much more
efficient than storing the full course key as a string or creating a foreign key
to the (large) CourseOverview table.

Note that the opposite does not hold. Admins can now create CourseRuns and/or
CatalogCourses that don't yet have any content attached. So you may find entries
in this openedx_catalog app that don't correspond to courses in modulestore.

In addition, we currently do not account for which courses should be visible to
which users. So this API does not yet provide any "list courses" methods. In the
future, the catalog API will be extended to implement course listing along with
pluggable logic for managing multiple catalogs of courses that can account for
instance-specific logic (e.g. enterprise, subscriptions, white labelling) when
determining which courses are visible to which users.
"""

# Import only the public API methods denoted with __all__
# pylint: disable=wildcard-import
from .api_impl import *

# You'll also want the models from .models_api
