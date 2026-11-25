"""
Models for digital asset management.

This applet introduces two new ``PublishableEntity`` types:

* An **Asset** is logically one thing, even though it may exist as several file
  *variants*. For example, an ebook Asset might be available as epub, pdf, and
  mobi files simultaneously. Those files are modeled as a M:M relation between
  ``AssetVersion`` and ``Media`` (via ``AssetVersionMedia``), mirroring the way
  ``ComponentVersion`` relates to ``Media``. Because the relation is on the
  *version*, changing an Asset's files creates a new ``AssetVersion`` that flows
  through the publishing (draft/publish) system.

* An **AssetBundle** is a group of related Assets that logically go together --
  for instance, a video file together with its VTT subtitles. Membership is a
  M:M relation between ``AssetBundleVersion`` and ``Asset`` (via
  ``AssetBundleVersionAsset``). It is an *unordered* set: Assets have no
  intrinsic ordering within a bundle. Unlike Containers, AssetBundles do **not**
  use ``EntityList``/``EntityListRow`` and are not ``Container`` subclasses.

Both Asset and AssetBundle are classified by a normalized type
(``AssetType`` / ``AssetBundleType``), in the same spirit as ``ComponentType``
and ``ContainerType``. The type's ``code`` is also used to build the underlying
``PublishableEntity``'s ``entity_ref`` (see this applet's ``api.py``).

This applet stays deliberately generic: it does not define specialized
subclasses (e.g. ``ImageAsset``, ``VideoAssetBundle``). Those will come from
more granular applets later, via multi-table inheritance from ``Asset`` /
``AssetBundle``.

Note: elsewhere in this codebase the word "asset" is sometimes used to mean "a
static file served to a browser" (see ``ComponentVersionMedia`` and the
``*_component_asset`` helpers in the ``components`` applet). The ``Asset`` and
``AssetBundle`` models here are higher-level PublishableEntities and are
distinct from that lower-level usage.
"""
from __future__ import annotations

from typing import ClassVar, NewType, cast

from django.db import models
from typing_extensions import deprecated

from openedx_django_lib.fields import case_sensitive_char_field, code_field, code_field_check, ref_field
from openedx_django_lib.managers import WithRelationsManager

from ..media.models import Media
from ..publishing.models import (
    LearningPackage,
    PublishableEntity,
    PublishableEntityMixin,
    PublishableEntityVersionMixin,
)

__all__ = [
    "AssetType",
    "AssetBundleType",
    "Asset",
    "AssetVersion",
    "AssetVersionMedia",
    "AssetBundle",
    "AssetBundleVersion",
    "AssetBundleVersionAsset",
]


class AssetType(models.Model):
    """
    Normalized representation of the type of an Asset.

    This is a lightweight classification (e.g. "ebook", "document"), in the same
    spirit as ``ComponentType`` and ``ContainerType``. It does *not* introduce
    specialized behavior; that will come later from Asset subclasses in more
    granular applets.

    Plugins/apps that add their own AssetTypes should prefix the code, e.g.
    "myapp_custom_ebook" instead of "custom_ebook", to avoid collisions.
    """

    # We don't need the app default of 8 bytes for this primary key; type tables
    # stay small, just like ComponentType and MediaType.
    id = models.AutoField(primary_key=True)

    # code uniquely identifies the type of asset, e.g. "ebook", "document".
    code = case_sensitive_char_field(max_length=100, blank=False, unique=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                # No whitespace, uppercase, or special characters allowed in "code".
                condition=models.lookups.Regex(models.F("code"), r"^[a-z0-9\-_\.]+$"),
                name="oel_assettype_code_rx",
            ),
        ]

    def __str__(self) -> str:  # pylint: disable=invalid-str-returned
        return self.code


class AssetBundleType(models.Model):
    """
    Normalized representation of the type of an AssetBundle.

    See ``AssetType`` for the rationale. Typical codes might be
    "video_with_subtitles" or similar.
    """

    id = models.AutoField(primary_key=True)

    code = case_sensitive_char_field(max_length=100, blank=False, unique=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.lookups.Regex(models.F("code"), r"^[a-z0-9\-_\.]+$"),
                name="oel_assetbundletype_code_rx",
            ),
        ]

    def __str__(self) -> str:  # pylint: disable=invalid-str-returned
        return self.code


class Asset(PublishableEntityMixin):
    """
    A single digital asset that may exist as multiple file variants.

    An Asset is logically one thing (e.g. "Chapter 1 ebook"), even though it may
    be available in several formats. The actual files live on ``AssetVersion``
    via the M:M to ``Media``.

    An Asset is 1:1 with ``PublishableEntity`` and shares its primary key, just
    like ``Component``. Make a foreign key to this model when you need a stable
    reference for as long as the LearningPackage exists.
    """

    AssetID = NewType("AssetID", PublishableEntity.ID)
    type ID = AssetID

    @property
    def id(self) -> ID:
        return cast(Asset.ID, self.publishable_entity_id)

    @property
    @deprecated("Use .id instead")
    def pk(self):
        """Mark the .pk attribute as deprecated (use .id); see Component.pk."""
        return self.id

    # Default manager preloads the (frequently accessed) asset_type lookup.
    objects: ClassVar[WithRelationsManager[Asset]] = WithRelationsManager(  # type: ignore[assignment]
        "asset_type"
    )

    with_publishing_relations = WithRelationsManager(
        "asset_type",
        "publishable_entity",
        "publishable_entity__draft__version",
        "publishable_entity__draft__version__assetversion",
        "publishable_entity__published__version",
        "publishable_entity__published__version__assetversion",
    )

    # Redundant with the publishable_entity relation, but having the FK directly
    # lets us build efficient single-table indexes (see Component.learning_package).
    learning_package = models.ForeignKey(LearningPackage, on_delete=models.CASCADE)

    # What kind of Asset this is. Used (along with asset_code) to derive the
    # publishable_entity.entity_ref.
    asset_type = models.ForeignKey(AssetType, on_delete=models.PROTECT)

    # asset_code is an identifier local to the (learning_package, asset_type).
    asset_code = code_field(unicode=True)

    class Meta:
        constraints = [
            # (asset_type, asset_code) is unique within a LearningPackage. Two
            # Assets in the same LearningPackage may share an asset_code if their
            # asset_types differ (same convention as Component).
            models.UniqueConstraint(
                fields=["learning_package", "asset_type", "asset_code"],
                name="oel_asset_uniq_lp_at_code",
            ),
            code_field_check("asset_code", name="oel_asset_code_regex", unicode=True),
        ]
        indexes = [
            # Search by Asset fields across all LearningPackages (e.g. a
            # support-oriented Django Admin tool).
            models.Index(
                fields=["asset_type", "asset_code"],
                name="oel_asset_idx_at_code",
            ),
        ]
        verbose_name = "Asset"
        verbose_name_plural = "Assets"

    def __str__(self) -> str:
        return f"{self.asset_type.code}:{self.asset_code}"


class AssetVersion(PublishableEntityVersionMixin):
    """
    A particular version of an Asset.

    This holds the actual files via a M:M relationship with ``Media`` through
    ``AssetVersionMedia``. Each row is one file *variant* of this version (e.g.
    the epub vs. pdf vs. mobi of an ebook).
    """

    # Technically redundant (reachable via publishable_entity_version), but
    # convenient.
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="versions")

    media: models.ManyToManyField[Media, AssetVersionMedia] = models.ManyToManyField(
        Media,
        through="AssetVersionMedia",
        related_name="asset_versions",
    )

    class Meta:
        verbose_name = "Asset Version"
        verbose_name_plural = "Asset Versions"


class AssetVersionMedia(models.Model):
    """
    Associates a piece of ``Media`` (a file) with an ``AssetVersion``.

    An AssetVersion may be associated with multiple pieces of Media -- the
    different variants/formats of the Asset. Each association has a ``variant``
    identifier that is unique within the AssetVersion (e.g. "epub", "book.pdf").
    This is analogous to ``ComponentVersionMedia.path``.

    Media is immutable and shareable across multiple AssetVersions.
    """

    asset_version = models.ForeignKey(AssetVersion, on_delete=models.CASCADE)
    media = models.ForeignKey(Media, on_delete=models.RESTRICT)

    # variant is a local identifier for the file within an AssetVersion, e.g.
    # "epub" or "book.pdf".
    variant = ref_field()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["asset_version", "variant"],
                name="oel_avmedia_uniq_av_variant",
            ),
        ]
        indexes = [
            models.Index(fields=["media", "asset_version"], name="oel_avmedia_m_av"),
            models.Index(fields=["asset_version", "media"], name="oel_avmedia_av_m"),
        ]


class AssetBundle(PublishableEntityMixin):
    """
    A group of related Assets that logically go together.

    For example, a video file together with its VTT subtitle files. Membership
    is held by ``AssetBundleVersion`` (via ``AssetBundleVersionAsset``) so that
    changing the membership creates a new version that flows through publishing.

    An AssetBundle is 1:1 with ``PublishableEntity`` and shares its primary key,
    just like ``Asset``. It is *not* a ``Container`` subclass and does not use
    ``EntityList``/``EntityListRow``.
    """

    AssetBundleID = NewType("AssetBundleID", PublishableEntity.ID)
    type ID = AssetBundleID

    @property
    def id(self) -> ID:
        return cast(AssetBundle.ID, self.publishable_entity_id)

    @property
    @deprecated("Use .id instead")
    def pk(self):
        """Mark the .pk attribute as deprecated (use .id); see Component.pk."""
        return self.id

    objects: ClassVar[WithRelationsManager[AssetBundle]] = WithRelationsManager(  # type: ignore[assignment]
        "asset_bundle_type"
    )

    with_publishing_relations = WithRelationsManager(
        "asset_bundle_type",
        "publishable_entity",
        "publishable_entity__draft__version",
        "publishable_entity__draft__version__assetbundleversion",
        "publishable_entity__published__version",
        "publishable_entity__published__version__assetbundleversion",
    )

    learning_package = models.ForeignKey(LearningPackage, on_delete=models.CASCADE)

    asset_bundle_type = models.ForeignKey(AssetBundleType, on_delete=models.PROTECT)

    # bundle_code is an identifier local to the (learning_package, asset_bundle_type).
    bundle_code = code_field(unicode=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["learning_package", "asset_bundle_type", "bundle_code"],
                name="oel_assetbundle_uniq_lp_abt_code",
            ),
            code_field_check("bundle_code", name="oel_assetbundle_code_regex", unicode=True),
        ]
        indexes = [
            models.Index(
                fields=["asset_bundle_type", "bundle_code"],
                name="oel_assetbundle_idx_abt_code",
            ),
        ]
        verbose_name = "Asset Bundle"
        verbose_name_plural = "Asset Bundles"

    def __str__(self) -> str:
        return f"{self.asset_bundle_type.code}:{self.bundle_code}"


class AssetBundleVersion(PublishableEntityVersionMixin):
    """
    A particular version of an AssetBundle.

    The set of member Assets for this version is defined via the M:M to
    ``Asset`` through ``AssetBundleVersionAsset``.
    """

    asset_bundle = models.ForeignKey(AssetBundle, on_delete=models.CASCADE, related_name="versions")

    assets: models.ManyToManyField[Asset, AssetBundleVersionAsset] = models.ManyToManyField(
        Asset,
        through="AssetBundleVersionAsset",
        related_name="bundle_versions",
    )

    class Meta:
        verbose_name = "Asset Bundle Version"
        verbose_name_plural = "Asset Bundle Versions"


class AssetBundleVersionAsset(models.Model):
    """
    Membership row linking an ``AssetBundleVersion`` to one of its member Assets.

    This is an *unordered* set (no ``order_num``). Members reference the
    ``Asset`` itself (not a specific ``AssetVersion``); when read, the Asset
    resolves to its current draft or published version, as appropriate. Because
    the foreign key points specifically at ``Asset``, only Assets can be members
    of a bundle (no runtime type-check is needed).
    """

    asset_bundle_version = models.ForeignKey(AssetBundleVersion, on_delete=models.CASCADE)
    asset = models.ForeignKey(Asset, on_delete=models.RESTRICT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["asset_bundle_version", "asset"],
                name="oel_abva_uniq_abv_asset",
            ),
        ]
        indexes = [
            # Reverse lookup: "which bundle versions contain this Asset?"
            models.Index(fields=["asset", "asset_bundle_version"], name="oel_abva_asset_abv"),
        ]
