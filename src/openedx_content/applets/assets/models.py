"""
Models for Assets, which are files shared by multiple other entities in a
learning package.

The model hierarchy is :class:`Asset` → :class:`AssetVersion` → :class:`Media`.
"""

from __future__ import annotations

import re
from typing import NewType, cast

from django.db import models
from typing_extensions import Sentinel, deprecated

from openedx_django_lib.fields import case_sensitive_char_field
from openedx_django_lib.managers import WithRelationsManager

from ..media.models import Media
from ..publishing.models import (
    LearningPackage,
    PublishableEntity,
    PublishableEntityMixin,
    PublishableEntityVersionMixin,
)

__all__ = [
    "Asset",
    "AssetVersion",
]

# Validation pattern for Asset's filename/path field. Disallows starting slash,
# '//', '\', './' and '../'.
PATH_RE = re.compile(
    r"\A"  # beginning of string
    r"(?!.*(?:\A|/)\.{1,2}(?:/|\Z))"  # no "." or ".." path segments
    r"[^/\\\x00-\x1f\x7f]+"  # first segment: no slash, backslash, or control chars
    r"(?:/[^/\\\x00-\x1f\x7f]+)*"  # more segments, each after exactly one "/"
    r"\Z"  # end of string, not just end of line
)
INVALID_PATH_MSG = "Asset path is invalid."
# Sentinel value used so only internal APIs can call `Asset.save()`
INTERNAL_API = Sentinel("INTERNAL_API")


class AssetPermissions(models.TextChoices):
    """Access permissions for an ``Asset``"""

    # No authentication required. Default (for performance reasons, not product reasons).
    PUBLIC = "public"
    # Restricted to enrolled users only
    LOCKED = "locked"
    # Restricted to staff only
    STAFF = "staff"


class Asset(PublishableEntityMixin):
    """
    An :class:`Asset` represents a shared asset file in a learning package.

    Each file in a course run's "Files" section is an :class:`Asset`.

    :class:`Asset` is distinct from the media files that are attached to
    individual :class:`Component` instances, although both use :class:`Media` to
    store the actual files. The main different is that the Component-local Media
    are scoped to a single Component, whereas each :class:`Asset` represents a
    file that can be used by many different Components from the
    :class:`LearningPackage`, or used by no Components at all in the case of a
    course-level Asset like a textbook PDF or the course card image used in the
    catalog. The other significant difference is that each :class:`Asset` holds
    only a single file, whereas a :class:`Component` can have many files
    attached.
    """

    AssetID = NewType("AssetID", PublishableEntity.ID)
    type ID = AssetID

    @property
    def id(self) -> ID:
        return cast(Asset.ID, self.publishable_entity_id)

    @property
    @deprecated("Use .id instead")
    def pk(self):
        """Mark the .pk attribute as deprecated"""
        # Note: Django-Stubs forces mypy to identify the `.pk` attribute of this model as having 'Any' type (due to our
        # use of a OneToOneField primary key), and this is impossible for us to override, so we prefer to use
        # `.id` which we can control fully.
        # Since Django uses '.pk' internally, we have to make sure it still works, however. So the best we can do is
        # override this with a deprecated marker, so it shows a warning in developer's IDEs like VS Code.
        return self.id

    with_publishing_relations = WithRelationsManager(
        "publishable_entity",
        "publishable_entity__draft__version",
        "publishable_entity__draft__version__assetversion",
        "publishable_entity__published__version",
        "publishable_entity__published__version__assetversion",
        "publishable_entity__published__publish_log_record",
        "publishable_entity__published__publish_log_record__publish_log",
    )

    learning_package = models.ForeignKey(LearningPackage, on_delete=models.CASCADE)
    """
    This foreign key is technically redundant because we're already locked
    to a single :class:`LearningPackage` through our ``publishable_entity``
    relation. However, having this foreign key directly allows us to make
    indexes that efficiently query by other :class:`Asset` fields
    within a given :class:`LearningPackage`, which is going to be a common
    use case (and we can't make a compound index using columns from
    different tables).
    """

    path = case_sensitive_char_field(
        max_length=500,  # Same as ComponentVersionMedia.path
        blank=False,
        # Validation of path value is below, as a CheckConstraint.
    )
    """
    The file name, such as ``image.png`` or ``images/example.jpg``. If this path
    is e.g. ``image.png``, then it is referenced from OLX as "static/image.png".
    But in general this ``path`` should not include ``static/``.

    The path is not versioned and cannot be changed. To "rename" a file,
    create a copy with a new name and (soft) delete the instance with the old
    name.
    """

    permissions = models.CharField(
        max_length=12,
        blank=False,
        choices=AssetPermissions,
        default=AssetPermissions.PUBLIC.value,
    )

    allow_scripts = models.BooleanField(default=False, null=False)
    """Serve HTML file assets with a reduced sandbox that allows loading scripts."""

    class Meta:
        constraints = [
            # path (filename) is unique within the learning package
            models.UniqueConstraint(
                fields=["learning_package", "path"],
                name="oe_content_asset_uniq_path",
            ),
            # path (filename) has to match our path regex
            models.CheckConstraint(
                condition=models.lookups.Regex(models.F("path"), PATH_RE.pattern),
                name="oe_content_asset_valid_path",
                violation_error_message=INVALID_PATH_MSG,
            ),
        ]

        # These are for the Django Admin UI.
        verbose_name = "Asset"
        verbose_name_plural = "Assets"

    def __str__(self) -> str:
        return f"Asset:{self.path}"

    def save(self, *args, **kwargs):
        """Block saving changes except through authorized APIs"""
        # Note: the manager's `update()` method still works as normal
        if kwargs.pop("saved_from", None) != INTERNAL_API:
            if self._state.adding:
                raise RuntimeError("Use the ``create_asset...()`` APIs to create an Asset.")
            raise RuntimeError("Use the ``update_asset()`` API to update an Asset.")
        return super().save(*args, **kwargs)


class AssetVersion(PublishableEntityVersionMixin):
    """
    A particular version of a :class:`Asset`.
    """

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="versions")
    """
    This is technically redundant, since we can get this through
    ``publishable_entity_version.publishable.asset``, but this is more
    convenient.
    """

    media = models.ForeignKey(Media, null=False, on_delete=models.RESTRICT, related_name="asset_versions")
    """media holds the actual file data associated with this version of the asset."""

    class Meta:
        verbose_name = "Asset Version"
        verbose_name_plural = "Asset Versions"

    def save(self, *args, **kwargs):
        """Block saving changes to existing rows of this model"""
        if not self._state.adding:
            raise RuntimeError("AssetVersion is immutable. Do not use save() to modify it.")
        return super().save(*args, **kwargs)
