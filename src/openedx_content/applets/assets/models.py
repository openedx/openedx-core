"""
Models for Assets, which are files shared by multiple other entities in a
learning package.

The model hierarchy is :class:`Asset` → :class:`AssetVersion` → :class:`Media`.
"""

from __future__ import annotations

import re
from typing import NewType, cast

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.lookups import Regex
from typing_extensions import Sentinel, deprecated

from openedx_django_lib.fields import case_sensitive_char_field, path_field, path_field_checks
from openedx_django_lib.managers import WithRelationsManager
from openedx_django_lib.validators import PATH_MAX_LENGTH

from ..media.models import Media
from ..publishing.models import (
    LearningPackage,
    PublishableEntity,
    PublishableEntityMixin,
    PublishableEntityVersion,
    PublishableEntityVersionMixin,
)

__all__ = [
    "Asset",
    "AssetVersion",
]


# Sentinel value used so only internal APIs can call `Asset.save()`
INTERNAL_API = Sentinel("INTERNAL_API")

# The characters that opaque-keys allows in the name of an AssetKey (``AssetLocator.ALLOWED_ID_RE``). Python's ``\w``
# is Unicode-aware, e.g. "é" matches but a combining accent does not.
_LEGACY_FLAT_PATH_RE = re.compile(r"[\w\-~.:%]+")

# A looser version of the above, for a DB CheckConstraint. ``\w`` means different things in the regex engines of
# Python, MySQL (ICU) and PostgreSQL (ARE), so this only rules out characters that are never allowed, mainly to make
# sure that nobody stores a real path (with folders or spaces) here by mistake. Python validation does the rest.
# Like ``PATH_RE``, it uses a lookahead to rule out control characters, because ``$`` can match before a final "\n".
_LEGACY_FLAT_PATH_DB_RE = r"^(?![^\x00-\x1f\x7f]*[\x00-\x1f\x7f])[^/\\*\s]+$"

INVALID_LEGACY_FLAT_PATH_MSG = "Legacy path is invalid or contains invalid characters."


def is_valid_legacy_flat_path(value: str) -> bool:
    """
    Check if ``value`` is a valid ``Asset.legacy_flat_path``, i.e. a valid
    ``AssetKey`` name like ``images_photo_1.jpg``.
    """
    # Legacy flat paths are derived from paths without ever getting longer, so they fit in the same length.
    return len(value) <= PATH_MAX_LENGTH and _LEGACY_FLAT_PATH_RE.fullmatch(value) is not None


def validate_legacy_flat_path(value: str) -> None:
    """Validate an ``Asset.legacy_flat_path`` value, raising ``ValidationError`` if invalid."""
    if not is_valid_legacy_flat_path(value):
        raise ValidationError(INVALID_LEGACY_FLAT_PATH_MSG)


class AssetPermissions(models.TextChoices):
    """Access permissions for an ``Asset``"""

    # No authentication required. Default (for performance reasons, not product reasons).
    # a.k.a. "Unlocked" in some Studio UIs
    PUBLIC = "public"
    # Restricted to enrolled users only
    # a.k.a. "Locked" in some Studio UIs
    ENROLLED = "enrolled"
    # Restricted to staff only
    STAFF = "staff"


class Asset(PublishableEntityMixin):
    """
    An :class:`Asset` represents a shared asset file in a learning package.

    Each file in a course run's "Files" section is an :class:`Asset`.

    :class:`Asset` is distinct from the media files that are attached to
    individual :class:`Component` instances, although both use :class:`Media` to
    store the actual files. The main difference is that the Component-local
    Media are scoped to a single Component, whereas each :class:`Asset`
    represents a file that can be used by many different Components from the
    :class:`LearningPackage`, or used by no Components at all in the case of a
    course-level Asset like a textbook PDF or the course card image used in the
    catalog. The other significant difference is that each :class:`Asset` holds
    only a single file, whereas a :class:`Component` can have many files
    attached.

    .. no_pii:
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

    path = path_field(blank=False)
    """
    The file name, such as ``image.png`` or ``images/example.jpg``. If this path
    is e.g. ``image.png``, then it is referenced from OLX as "static/image.png".
    But in general this ``path`` should not include ``static/``.

    The path is not versioned and cannot be changed. To "rename" a file,
    create a copy with a new name and (soft) delete the instance with the old
    name.
    """

    legacy_flat_path = case_sensitive_char_field(
        max_length=PATH_MAX_LENGTH,
        null=True,
        blank=False,
        validators=[validate_legacy_flat_path],
    )
    """
    The name that the legacy (MongoDB) contentstore used to identify this
    asset, if any, e.g. ``images_photo_1.jpg``. ``NULL`` for assets that never
    had one.

    In the legacy contentstore, every file in a course is identified by an
    ``AssetKey`` such as
    ``asset-v1:Org+Course+Run+type@asset+block@images_photo_1.jpg``. The last
    part (which opaque-keys calls ``AssetKey.path``) is a "flat" name with no
    folders, which is usually derived from the file's real path by replacing
    ``/`` and any other character that's not allowed in an ``AssetKey`` with
    ``_``. For example, ``images/photo 1.jpg`` becomes ``images_photo_1.jpg``.
    That conversion is lossy (``images/photo 1.jpg`` and ``images photo
    1.jpg`` have the same flat name), and some flat names were created in other
    ways, so this field always holds the exact name that the contentstore used,
    which can't reliably be re-computed from ``path``. Computing it is the
    responsibility of the caller (the contentstore shim in openedx-platform),
    not of this app.

    Often ``legacy_flat_path`` and ``path`` are equal: files uploaded to the
    legacy "Files" page only kept their flat name, so ``path`` is the flat name
    too. They differ for files that were imported from a course export with a
    real folder structure, where ``path`` is e.g. ``images/photo 1.jpg``.

    Content created with the legacy contentstore can reference such an asset in
    several ways, including:

    - ``/static/images/photo 1.jpg``
    - ``/static/images_photo_1.jpg``
    - ``/asset-v1:Org+Course+Run+type@asset+block@images_photo_1.jpg``
    - ``/c4x/Org/Course/asset/images_photo_1.jpg``
    - and more (e.g. absolute URLs of several forms)

    This field exists for two reasons:

    1. To make sure there are no collisions in this flat namespace: it is
       unique (and case-sensitive) within a Learning Package. Deleted assets
       keep their flat name, so it can't be reused by a different asset.
    2. To provide a fast, indexed lookup by flat name (see
       ``get_asset_by_legacy_flat_path()``), e.g. for resolving legacy
       references or emulating the legacy contentstore API.

    The value must be a valid ``AssetKey`` name: one or more Unicode word
    characters or ``-``, ``~``, ``.``, ``:``, ``%``. Unlike ``path``, it is not
    required to be Unicode normalized, because it must match the existing
    ``AssetKey`` exactly.

    Once set, the value can't be changed or cleared, but it can be set later on
    an asset that doesn't have one yet (``update_asset()``).

    ⚠️ Collisions are only detected between assets that both have a
    ``legacy_flat_path``. If a course has any assets with a
    ``legacy_flat_path``, then all new assets in that course should get one
    too. Otherwise, e.g., a new asset with ``path="images_photo_1.jpg"`` and no
    ``legacy_flat_path`` can be created alongside an existing asset with
    ``path="images/photo 1.jpg"`` and ``legacy_flat_path="images_photo_1.jpg"``,
    which makes ``/static/images_photo_1.jpg`` ambiguous.

    In the future, we hope that new assets will only be referenced in the
    recommended format (``/static/images/photo 1.jpg``) and won't need a
    ``legacy_flat_path``. However, content (and links to it from elsewhere) is
    forever, so we'll need to keep storing and using this field in older
    courses indefinitely, even long after the contentstore API/shim has been
    removed from the platform (see ADR 0014).
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
        default_manager_name = "objects"  # Use the manager from PublishableEntityMixin by default
        constraints = (
            # path (filename) is unique within the learning package
            models.UniqueConstraint(
                fields=["learning_package", "path"],
                name="oe_content_asset_uniq_path",
            ),
            *path_field_checks("path", name="oe_content_asset_valid_path"),
            # legacy_flat_path is unique within the learning package (NULL is exempt)
            models.UniqueConstraint(
                fields=["learning_package", "legacy_flat_path"],
                name="oe_content_asset_uniq_legacy_flat_path",
            ),
            models.CheckConstraint(
                condition=Regex(models.F("legacy_flat_path"), _LEGACY_FLAT_PATH_DB_RE)
                | models.Q(legacy_flat_path__isnull=True),
                name="oe_content_asset_valid_legacy_flat_path",
                violation_error_message=INVALID_LEGACY_FLAT_PATH_MSG,
            ),
            # Make SQLite enforce a maximum length like MySQL does
            models.CheckConstraint(
                condition=models.Q(legacy_flat_path__length__lte=PATH_MAX_LENGTH)
                | models.Q(legacy_flat_path__isnull=True),
                name="oe_content_asset_legacy_flat_path_maxlength",
            ),
            models.CheckConstraint(
                condition=models.Q(permissions__in=AssetPermissions.values), name="oe_content_asset_permissions_enum"
            ),
        )

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

    @property
    def used_by_draft_versions(self) -> list[PublishableEntityVersion]:
        """
        Get a list of all the draft versions of any ``PublishableEntity``
        instances whose current draft version depends on this asset. This only
        works if you load a set of assets via::

            get_assets(..., draft=True, used_by_draft_versions=True)
        """
        try:
            return self.publishable_entity.used_by_draft_versions  # type: ignore[attr-defined]
        except AttributeError:
            raise RuntimeError(
                "used_by_draft_versions is not loaded unless you use get_assets(..., used_by_draft_versions=True)"
            ) from None


class AssetVersion(PublishableEntityVersionMixin):
    """
    A particular version of a :class:`Asset`.

    .. no_pii:
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
