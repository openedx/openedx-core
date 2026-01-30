"""
Core models for Collections

TLDR Guidelines:
1. DO NOT modify these models to store full version snapshots.
2. DO NOT use these models to try to reconstruct historical versions of
   Collections for fast querying.

If you're trying to do either of these things, you probably want a new model or
app. For more details, read on.

The goal of these models is to provide a lightweight method of organizing
PublishableEntities. The first use case for this is modeling the structure of a
v1 Content Library within a LearningPackage. This is what we'll use the
Collection model for.

An important thing to note here is that Collections are *NOT* publishable
entities themselves. They have no "Draft" or "Published" versions. Collections
are never "published", though the things inside of them are.

When a LibraryContentBlock makes use of a Content Library, it copies all of
the items it will use into the Course itself. It will also store a version
on the LibraryContentBlock -- this is a MongoDB ObjectID in v1 and an integer in
v2 Libraries. Later on, the LibraryContentBlock will want to check back to see
if any updates have been made, using its version as a key. If a new version
exists, the course team has the option of re-copying data from the Library.

ModuleStore-based v1 Libraries and OeXCore-based v2 libraries both version
the entire library in a series of snapshots. This makes it difficult to have
very large libraries, which is an explicit goal for Modular Learning. In
Open edX Core, we've moved to tracking the versions of individual Components to
address this issue. But that means we no longer have a single version indicator
for "has anything here changed"?

We *could* have put that version in the ``publishing`` app's PublishLog, but
that would make it too broad. We want the ability to eventually collapse many v1
Libraries into a single OeXCore backed v2 Library. If we tracked the
versioning in only a central location, then we'd have many false positives where
the version was bumped because something else in the Learning Package changed.
So instead, we're creating a new Collection model inside the LearningPackage to
track that concept.

A critical takeaway is that we don't have to store snapshots of every version of
a Collection, because that data has been copied over by the LibraryContentBlock.
We only need to store the current state of the Collection, and increment the
version numbers when changes happen. This will allow the LibraryContentBlock to
check in and re-copy over the latest version if the course team desires.

That's why these models only store the current state of a Collection. Unlike the
``components`` app,  ``collections`` does not store fully materialized snapshots
of past versions. This is done intentionally in order to save space and reduce
the cost of writes. Collections may grow to be very large, and we don't want to
be writing N rows with every version, where N is the number of
PublishableEntities in a Collection.

MVP of these models does not store changesets, but we can add this when there's a
use case for it. The number of rows in these changesets would grow in proportion
to the number of things that are actually changing (instead of copying over
everything on every version). This is could be used to make it easier to figure out
what changed between two given versions of a Collection. A LibraryContentBlock
in a course would have stored the version number of the last time it copied data
from the Collection, and we can eventually surface this data to the user. Note that
while it may be possible to reconstruct past versions of Collections based off of
this changeset data, it's going to be a very slow process to do so, and it is
strongly discouraged.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx_django_lib.fields import MultiCollationTextField, case_insensitive_char_field, key_field
from openedx_django_lib.validators import validate_utc_datetime

from ..publishing.models import LearningPackage, PublishableEntity

__all__ = [
    "Collection",
    "CollectionPublishableEntity",
]


class CollectionManager(models.Manager):
    """
    Custom manager for Collection class.
    """
    def get_by_key(self, learning_package_id: int, key: str):
        """
        Get the Collection for the given Learning Package + key.
        """
        return self.select_related('learning_package') \
                   .get(learning_package_id=learning_package_id, key=key)


class Collection(models.Model):
    """
    Represents a collection of library components
    """
    objects: CollectionManager[Collection] = CollectionManager()

    id = models.AutoField(primary_key=True)

    # Each collection belongs to a learning package
    learning_package = models.ForeignKey(LearningPackage, on_delete=models.CASCADE)

    # Every collection is uniquely and permanently identified within its learning package
    # by a 'key' that is set during creation. Both will appear in the
    # collection's opaque key:
    # e.g. "lib-collection:lib:key" is the opaque key for a library collection.
    key = key_field(db_column='_key')

    title = case_insensitive_char_field(
        null=False,
        blank=False,
        max_length=500,
        help_text=_(
            "The title of the collection."
        ),
    )

    description = MultiCollationTextField(
        blank=True,
        null=False,
        default="",
        max_length=10_000,
        help_text=_(
            "Provides extra information for the user about this collection."
        ),
        db_collations={
            "sqlite": "NOCASE",
            "mysql": "utf8mb4_unicode_ci",
        }
    )

    enabled = models.BooleanField(
        default=True,
        help_text=_(
            'Disabled collections are "soft deleted", and should be re-enabled before use, or be deleted.',
        ),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    created = models.DateTimeField(
        auto_now_add=True,
        validators=[
            validate_utc_datetime,
        ],
    )

    modified = models.DateTimeField(
        auto_now=True,
        validators=[
            validate_utc_datetime,
        ],
    )

    entities: models.ManyToManyField[PublishableEntity, "CollectionPublishableEntity"] = models.ManyToManyField(
        PublishableEntity,
        through="CollectionPublishableEntity",
        related_name="collections",
    )

    class Meta:
        verbose_name_plural = "Collections"
        constraints = [
            # Keys are unique within a given LearningPackage.
            models.UniqueConstraint(
                fields=[
                    "learning_package",
                    "key",
                ],
                name="oel_coll_uniq_lp_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["learning_package", "title"],
                name="openedx_content_coll_lp_title",
            ),
        ]

    def __repr__(self) -> str:
        """
        Developer-facing representation of a Collection.
        """
        return str(self)

    def __str__(self) -> str:
        """
        User-facing string representation of a Collection.
        """
        return f"<{self.__class__.__name__}> (lp:{self.learning_package_id} {self.key}:{self.title})"


class CollectionPublishableEntity(models.Model):
    """
    Collection -> PublishableEntity association.
    """
    collection = models.ForeignKey(
        Collection,
        on_delete=models.CASCADE,
    )
    entity = models.ForeignKey(
        PublishableEntity,
        on_delete=models.RESTRICT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created = models.DateTimeField(
        auto_now_add=True,
        validators=[
            validate_utc_datetime,
        ],
    )

    class Meta:
        constraints = [
            # Prevent race conditions from making multiple rows associating the
            # same Collection to the same Entity.
            models.UniqueConstraint(
                fields=[
                    "collection",
                    "entity",
                ],
                name="oel_collections_cpe_uniq_col_ent",
            )
        ]
