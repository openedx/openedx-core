"""
Custom Django ORM Managers.
"""
from __future__ import annotations

from typing import TypeVar

from django.db import models
from django.db.models.query import QuerySet

M = TypeVar('M', bound=models.Model)


class WithRelationsManager(models.Manager[M]):
    """
    Custom Manager that adds select_related to the default queryset.

    This is useful if people using a model will very frequently want to call
    into some of its relations and you want to avoid unnecessary extra database
    calls.

    You can override the default ``objects`` manager with this one if you have
    a model that should basically always called with a ``select_related``. For
    example, if you have a small lookup type-model that is frequently accessed.

    For more complex joins, use this class to create a distinctly named manager
    on your model class, instead of overwriting ``objects``. So for example::

      class Component(models.Model):
          with_publishing_relations = WithRelationsManager(
              'publishable_entity',
              'publishable_entity__draft__version',
              'publishable_entity__draft__version__componentversion',
              'publishable_entity__published__version',
              'publishable_entity__published__version__componentversion',
          )
    """
    def __init__(self, *relations):
        """
        Init with a list of relations that you would use in select_related.
        """
        self._relations = relations
        super().__init__()

    def get_queryset(self) -> QuerySet:
        return super().get_queryset().select_related(
            *self._relations
        )
