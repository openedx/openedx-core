"""
Data models used by openedx-tagging
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from django.db.models import QuerySet
from typing_extensions import NotRequired, TypeAlias


class TagData(TypedDict):
    """
    Data about a single tag. Many of the tagging API methods return Django
    QuerySets that resolve to these dictionaries.

    Even though the data will be in this same format, it will not necessarily
    be an instance of this class but rather a plain dictionary. This is more a
    type than a class.
    """
    value: str
    external_id: str | None
    child_count: int
    descendant_count: int
    depth: int
    parent_value: str | None
    # Note: usage_count may or may not be present, depending on the request.
    usage_count: NotRequired[int]
    # Internal database ID, if any. Generally should not be used; prefer 'value' which is unique within each taxonomy.
    _id: int | None


if TYPE_CHECKING:
    from django_stubs_ext import ValuesQuerySet
    TagDataQuerySet: TypeAlias = ValuesQuerySet[Any, TagData]
    # The following works better for pyright (provides proper VS Code autocompletions),
    # but I can't find any way to specify different types for pyright vs mypy :/
    # TagDataQuerySet: TypeAlias = QuerySet[TagData]
else:
    TagDataQuerySet = QuerySet[TagData]
