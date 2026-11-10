"""
Implementation of the Pathway part of the `openedx_catalog` API: categories, catalog pathways and enrollment.

A Pathway is split into a catalog half (these models) and a versioned content half in
`openedx_learning.applets.pathways`. See the openedx_learning ADR 0007. The functions below only touch the catalog half;
creating and versioning the *definition* of a Pathway is done through `openedx_learning.api`.
"""

import logging
from typing import overload

from django.db.models.query import QuerySet

from ..models import CatalogPathway, PathwayCategory, PathwayEnrollment

log = logging.getLogger(__name__)

# These are the public API methods that anyone can use
__all__ = [
    "get_pathway_category",
    "get_pathway_categories",
    "create_pathway_category",
    "update_pathway_category",
    "delete_pathway_category",
    "get_catalog_pathway",
    "get_catalog_pathways",
    "create_catalog_pathway",
    "update_catalog_pathway",
    "delete_catalog_pathway",
    "enroll_in_pathway",
    "unenroll_from_pathway",
    "is_enrolled_in_pathway",
    "get_pathway_enrollments",
]


def get_pathway_category(category_code: str) -> PathwayCategory:
    """
    Get a `PathwayCategory` by its stable code.

    ⚠️ Does not check permissions.
    """
    return PathwayCategory.objects.get(category_code=category_code)


def _pathway_category(category: PathwayCategory | str) -> PathwayCategory:
    """
    Resolve a `PathwayCategory` given either as an instance or by its code.
    """
    if isinstance(category, PathwayCategory):
        return category
    return get_pathway_category(category)


def get_pathway_categories() -> QuerySet[PathwayCategory]:
    """
    List all pathway categories, ordered by title, e.g. for a picker shown when creating a catalog pathway.

    ⚠️ Does not check permissions.
    """
    return PathwayCategory.objects.all()


def create_pathway_category(*, category_code: str, title: str, title_plural: str = "") -> PathwayCategory:
    """
    Create a `PathwayCategory`.

    The ``category_code`` can't be changed later, so pick one that stays accurate if the ``title`` is reworded. Leave
    ``title_plural`` blank unless the title doesn't pluralize by appending "s" (see `PathwayCategory.title_plural`).

    ⚠️ Does not check permissions.
    """
    return PathwayCategory.objects.create(category_code=category_code, title=title, title_plural=title_plural)


def update_pathway_category(
    category: PathwayCategory | str,
    *,
    title: str | None = None,
    title_plural: str | None = None,
) -> None:
    """
    Update a `PathwayCategory`, given as an instance or by its code. Pass `None` for a field to leave it unchanged.

    Pass ``title_plural=""`` to go back to deriving the plural from the title. The ``category_code`` can't be changed,
    because it's what code and imports key off.

    ⚠️ Does not check permissions.
    """
    pc = _pathway_category(category)
    update_fields = []
    for field_name, value in (
        ("title", title),
        ("title_plural", title_plural),
    ):
        if value is not None:
            setattr(pc, field_name, value)
            update_fields.append(field_name)
    if update_fields:
        pc.save(update_fields=update_fields)


def delete_pathway_category(category: PathwayCategory | str) -> None:
    """
    Delete a `PathwayCategory`, given as an instance or by its code.

    This fails with a `ProtectedError` while any catalog pathway uses it; move those pathways to another category first
    with `update_catalog_pathway`.

    ⚠️ Does not check permissions.
    """
    _pathway_category(category).delete()


def _catalog_pathways() -> QuerySet[CatalogPathway]:
    """
    Catalog pathways along with what's needed to show them: their org and category.
    """
    return CatalogPathway.objects.select_related("org", "category")


@overload
def get_catalog_pathway(*, org_code: str, pathway_code: str) -> CatalogPathway: ...
@overload
def get_catalog_pathway(*, key_str: str) -> CatalogPathway: ...
@overload
def get_catalog_pathway(*, pk: CatalogPathway.ID) -> CatalogPathway: ...


def get_catalog_pathway(
    pk: CatalogPathway.ID | None = None,
    key_str: str = "",
    org_code: str = "",
    pathway_code: str = "",
) -> CatalogPathway:
    """
    Get a catalog pathway.

    Its org and category come with it, so showing them costs no further queries.

    ⚠️ Does not check permissions or visibility rules.

    The `CatalogPathway` may not have any content implementing it yet. Because `openedx_catalog` never knows about the
    content side, this function cannot tell you whether it does; ask `openedx_learning.api` instead.
    """
    assert pk or key_str or (org_code and pathway_code)
    if pk:
        assert not org_code
        assert not key_str
        return _catalog_pathways().get(pk=pk)
    if key_str:
        assert key_str.startswith("catalog-pathway:")
        assert not org_code
        assert not pathway_code
        _, org_code, pathway_code = key_str.split(":", 2)
    return _catalog_pathways().get(org__short_name=org_code, pathway_code=pathway_code)


def get_catalog_pathways(
    *,
    org_code: str | None = None,
    category_code: str | None = None,
) -> QuerySet[CatalogPathway]:
    """
    List catalog pathways, most recently created first, optionally narrowed down to an org and/or a category.

    Both filters match exactly; an unknown org or category simply matches nothing. The result is a `QuerySet`, so that
    callers can narrow, search and paginate it further. Each pathway's org and category come with it, so showing them
    costs no further queries.

    ⚠️ Does not check permissions or visibility rules. That suits authoring and administration, but a listing shown to
    learners will need the visibility logic described in the `openedx_catalog.api` docstring, which doesn't exist yet.

    As with `get_catalog_pathway`, this can't tell you which pathways have content implementing them yet; ask
    `openedx_learning.api` instead.
    """
    pathways = _catalog_pathways()
    if org_code is not None:
        pathways = pathways.filter(org__short_name=org_code)
    if category_code is not None:
        pathways = pathways.filter(category__category_code=category_code)
    return pathways


def create_catalog_pathway(
    *,
    org_code: str,
    pathway_code: str,
    category: PathwayCategory,
    title: str = "",
    description: str = "",
) -> CatalogPathway:
    """
    Create a `CatalogPathway`.

    The `Organization` identified by `org_code` must already exist. The category is always required: there is no
    default, so a UI that wants to preselect one decides which itself.

    This creates only the catalog half of a Pathway. Use `openedx_learning.api` to create the versioned content that
    implements it.

    ⚠️ Does not check permissions.
    """
    pathway = CatalogPathway(pathway_code=pathway_code, title=title, category=category, description=description)
    pathway.org_code = org_code  # Resolves the Organization by short_name; raises Organization.DoesNotExist.
    pathway.save()
    return pathway


def update_catalog_pathway(
    catalog_pathway: CatalogPathway | CatalogPathway.ID,
    *,
    title: str | None = None,
    category: PathwayCategory | None = None,
    description: str | None = None,
) -> None:
    """
    Update a `CatalogPathway`. Pass `None` for a field to leave it unchanged.

    None of these edits create a new content version: catalog copy and the Pathway definition change at different rates
    and are edited by different people, which is the whole point of the split.

    ⚠️ Does not check permissions.
    """
    if isinstance(catalog_pathway, CatalogPathway):
        cp = catalog_pathway
    else:
        cp = CatalogPathway.objects.get(pk=catalog_pathway)

    update_fields = []
    for field_name, value in (
        ("title", title),
        ("category", category),
        ("description", description),
    ):
        if value is not None:
            setattr(cp, field_name, value)
            update_fields.append(field_name)
    if update_fields:
        cp.save(update_fields=update_fields + ["modified"])


def delete_catalog_pathway(catalog_pathway: CatalogPathway | CatalogPathway.ID) -> None:
    """
    Delete a `CatalogPathway`.

    This fails with a `ProtectedError` while anyone is or ever was enrolled in it, including learners who have since
    unenrolled, so that deleting the wrong pathway can't wipe out enrollment history; delete those enrollments first if
    that's really intended. It also fails while any Pathway content still implements it, because that link is a
    `PROTECT` foreign key on the content side.

    ⚠️ Does not check permissions.
    """
    if isinstance(catalog_pathway, CatalogPathway):
        cp = catalog_pathway
    else:
        cp = CatalogPathway.objects.get(pk=catalog_pathway)
    cp.delete()


def enroll_in_pathway(user_id: int, catalog_pathway: CatalogPathway | CatalogPathway.ID) -> PathwayEnrollment:
    """
    Enroll a learner in a `CatalogPathway`, or return their existing active enrollment.

    If the learner had previously unenrolled, their existing row is reactivated rather than replaced, so the original
    enrollment date is kept.

    Enrollment does not pin a content version: progress is always evaluated against whatever is published at the time,
    so that authoring changes reach learners who are already enrolled.

    Raises `CatalogPathway.DoesNotExist` if given the ID of a pathway that doesn't exist.

    ⚠️ Does not check permissions.
    """
    if not isinstance(catalog_pathway, CatalogPathway):
        catalog_pathway = CatalogPathway.objects.get(pk=catalog_pathway)
    enrollment, created = PathwayEnrollment.objects.get_or_create(user_id=user_id, catalog_pathway=catalog_pathway)
    if not created and not enrollment.is_active:
        enrollment.is_active = True
        enrollment.save(update_fields=["is_active", "modified"])
    return enrollment


def unenroll_from_pathway(user_id: int, catalog_pathway: CatalogPathway | CatalogPathway.ID) -> None:
    """
    Unenroll a learner from a `CatalogPathway`. A no-op if they aren't enrolled.

    The enrollment row is deactivated, not deleted. It is saved rather than bulk-updated so that `post_save` fires, as
    it does on enrollment, and listeners such as analytics sinks see the change.

    ⚠️ Does not check permissions.
    """
    pathway_id = catalog_pathway.id if isinstance(catalog_pathway, CatalogPathway) else catalog_pathway
    enrollment = PathwayEnrollment.objects.filter(
        user_id=user_id, catalog_pathway_id=pathway_id, is_active=True
    ).first()
    if enrollment:
        enrollment.is_active = False
        enrollment.save(update_fields=["is_active", "modified"])


def is_enrolled_in_pathway(user_id: int, catalog_pathway: CatalogPathway | CatalogPathway.ID) -> bool:
    """
    Check whether this learner is actively enrolled in this `CatalogPathway`.

    ⚠️ Does not check permissions.
    """
    pathway_id = catalog_pathway.id if isinstance(catalog_pathway, CatalogPathway) else catalog_pathway
    return PathwayEnrollment.objects.filter(user_id=user_id, catalog_pathway_id=pathway_id, is_active=True).exists()


def get_pathway_enrollments(user_id: int, *, include_inactive: bool = False) -> QuerySet[PathwayEnrollment]:
    """
    Get a learner's pathway enrollments, most recent first.

    Only active enrollments are returned unless ``include_inactive`` is set. Each enrollment's catalog pathway comes
    with it, along with the pathway's org and category, so a dashboard can show them without further queries.

    ⚠️ Does not check permissions or visibility rules.
    """
    enrollments = PathwayEnrollment.objects.filter(user_id=user_id).select_related(
        "catalog_pathway__org", "catalog_pathway__category"
    )
    if not include_inactive:
        enrollments = enrollments.filter(is_active=True)
    return enrollments
