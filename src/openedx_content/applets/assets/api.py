"""
Assets API

These functions are the supported way to create and read Assets and
AssetBundles. As with the other applets, you should never mutate this app's
models directly (there is bookkeeping across multiple models, and related models
you may not know about); read from the models directly only for queries.

Please look at the models.py file for more information about what is stored here.
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.db.transaction import atomic

from ..media import api as media_api
from ..media.models import Media
from ..publishing import api as publishing_api
from ..publishing.models import LearningPackage
from .models import (
    Asset,
    AssetBundle,
    AssetBundleType,
    AssetBundleVersion,
    AssetBundleVersionAsset,
    AssetType,
    AssetVersion,
    AssetVersionMedia,
)

# The public API that will be re-exported by openedx_content.api is listed in
# the __all__ entries below. Internal helper functions that are private to this
# module start with an underscore. If a function does not start with an
# underscore AND it is not in __all__, that function is considered to be callable
# only by other applets in the openedx_content package.
__all__ = [
    # Asset
    "get_or_create_asset_type",
    "create_asset",
    "create_asset_version",
    "create_asset_and_version",
    "create_next_asset_version",
    "get_asset",
    "get_asset_by_code",
    "get_assets",
    "AssetFile",
    "get_asset_version_media",
    # AssetBundle
    "get_or_create_asset_bundle_type",
    "create_asset_bundle",
    "create_asset_bundle_version",
    "create_asset_bundle_and_version",
    "create_next_asset_bundle_version",
    "get_asset_bundle",
    "get_asset_bundle_by_code",
    "get_asset_bundles",
    "AssetBundleMember",
    "get_assets_in_bundle",
]


########################################################################################################################
# Asset Types


def get_or_create_asset_type(code: str) -> AssetType:
    """
    Get the AssetType for ``code``, creating it if it does not yet exist.

    Caching Warning: Be careful about putting any caching decorator around this
    function (e.g. ``lru_cache``). It's possible that incorrect cache values
    could leak out in the event of a rollback -- e.g. new types are introduced in
    a large import transaction which later fails. You can safely cache the
    results that come back from this function with a local dict in your import
    process instead.
    """
    asset_type, _created = AssetType.objects.get_or_create(code=code)
    return asset_type


def get_or_create_asset_bundle_type(code: str) -> AssetBundleType:
    """
    Get the AssetBundleType for ``code``, creating it if missing.

    See the caching warning on ``get_or_create_asset_type``.
    """
    asset_bundle_type, _created = AssetBundleType.objects.get_or_create(code=code)
    return asset_bundle_type


########################################################################################################################
# Assets


def create_asset(
    learning_package_id: LearningPackage.ID,
    /,
    asset_type: AssetType,
    asset_code: str,
    created: datetime,
    created_by: int | None,
    *,
    can_stand_alone: bool = True,
) -> Asset:
    """
    Create a new Asset.

    The ``entity_ref`` is conventionally derived as ``"{asset_type.code}:{asset_code}"``,
    although callers should not assume that this will always be true.
    """
    entity_ref = f"{asset_type.code}:{asset_code}"
    with atomic():
        publishable_entity = publishing_api.create_publishable_entity(
            learning_package_id,
            entity_ref,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        asset = Asset.objects.create(
            publishable_entity=publishable_entity,
            learning_package_id=learning_package_id,
            asset_type=asset_type,
            asset_code=asset_code,
        )
    return asset


def create_asset_version(
    asset_id: Asset.ID,
    /,
    version_num: int,
    title: str,
    created: datetime,
    created_by: int | None,
    *,
    media: dict[str, Media.ID | Media | bytes] | None = None,
) -> AssetVersion:
    """
    Create a new AssetVersion.

    The ``media`` parameter is a dict of *variant* identifiers to Media-like
    things (a ``Media.ID``, ``Media`` object, or raw ``bytes``). This is the set
    of file variants we want to associate with the new AssetVersion -- for
    example ``{"book.epub": ..., "book.pdf": ...}``.

    Media can be specified as ``bytes`` for testing convenience, but you will
    almost always want to create a Media object first in actual app code, because
    that gives you better control over the MIME type and storage specifics (file
    vs. database).
    """
    with atomic():
        publishable_entity_version = publishing_api.create_publishable_entity_version(
            asset_id,
            version_num=version_num,
            title=title,
            created=created,
            created_by=created_by,
        )
        asset_version = AssetVersion.objects.create(
            publishable_entity_version=publishable_entity_version,
            asset_id=asset_id,
        )
        if media:
            _set_asset_version_media(asset_version, media, created=created)

    return asset_version


def create_asset_and_version(  # pylint: disable=too-many-positional-arguments
    learning_package_id: LearningPackage.ID,
    /,
    asset_type: AssetType,
    asset_code: str,
    title: str,
    created: datetime,
    created_by: int | None = None,
    *,
    can_stand_alone: bool = True,
    media: dict[str, Media.ID | Media | bytes] | None = None,
) -> tuple[Asset, AssetVersion]:
    """
    Create an Asset and its first AssetVersion atomically.
    """
    with atomic():
        asset = create_asset(
            learning_package_id,
            asset_type,
            asset_code,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        asset_version = create_asset_version(
            asset.id,
            version_num=1,
            title=title,
            created=created,
            created_by=created_by,
            media=media or {},
        )

    return (asset, asset_version)


def create_next_asset_version(
    asset_id: Asset.ID,
    /,
    media_to_replace: dict[str, Media.ID | Media | bytes | None],
    created: datetime,
    title: str | None = None,
    created_by: int | None = None,
    *,
    force_version_num: int | None = None,
    ignore_previous_media: bool = False,
) -> AssetVersion:
    """
    Create a new AssetVersion based on the most recent version.

    ``media_to_replace`` maps *variant* identifiers to a ``Media.ID``, ``Media``,
    ``bytes`` (a new file), or ``None`` (to delete that variant in the next
    version). Unless ``ignore_previous_media`` is set, the previous version's
    media is carried over and ``media_to_replace`` is applied as a delta on top.
    It is okay to mark variants for deletion that don't exist.

    Use ``force_version_num`` to set a specific version number (e.g. when
    restoring from backup or importing legacy data); otherwise the version number
    is incremented automatically from the latest version.

    This mirrors ``components.api.create_next_component_version``.
    """
    asset = Asset.objects.get(pk=asset_id)
    last_version = asset.versioning.latest
    if last_version is None:
        next_version_num = 1
        title = title or ""
    else:
        next_version_num = last_version.version_num + 1
        if title is None:
            title = last_version.title

    if force_version_num is not None:
        next_version_num = force_version_num

    with atomic():
        publishable_entity_version = publishing_api.create_publishable_entity_version(
            asset_id,
            version_num=next_version_num,
            title=title,
            created=created,
            created_by=created_by,
        )
        asset_version = AssetVersion.objects.create(
            publishable_entity_version=publishable_entity_version,
            asset_id=asset_id,
        )

        if ignore_previous_media or last_version is None:
            variants_to_media = {
                variant: media
                for variant, media in media_to_replace.items()
                if media is not None  # Ignore deletion entries in this case.
            }
        else:
            # Most of the time, we're adding our media changes as a delta on top
            # of the last version's media.
            previous_media = {
                avm.variant: avm.media_id
                for avm in AssetVersionMedia.objects.filter(asset_version=last_version)
            }
            variants_to_media = {
                variant: media
                for variant, media in (previous_media | media_to_replace).items()
                if media is not None  # "media is None" means "delete this"
            }

        _set_asset_version_media(asset_version, variants_to_media, created)

    return asset_version


def _set_asset_version_media(
    version: AssetVersion,
    variants_to_media_values: dict[str, Media.ID | Media | bytes],
    created: datetime,
) -> None:
    """
    Internal helper to set the Media variants for this AssetVersion.

    Only call this when first initializing an AssetVersion. Media can be
    specified as ``bytes`` for testing convenience (the MIME type is guessed from
    the variant name, so a variant like ``"book.epub"`` works best), but you will
    almost always want to create a Media object first in actual app code.

    Mirrors ``components.api._set_component_version_media``.
    """
    @cache  # avoid repeated lookups, e.g. an asset with several variants of one type
    def cached_media_type(media_type_str):
        return media_api.get_or_create_media_type(media_type_str)

    def valid_variant(variant):
        """No absolute paths, surrounding whitespace, or backslashes (Windows separators)."""
        return variant == variant.strip().lstrip("/") and "\\" not in variant

    # Normalize to media_ids for the bulk insert below.
    variants_to_media_ids: dict[str, Media.ID] = {}

    av_learning_package_id = version.asset.learning_package_id

    for variant, media_value in variants_to_media_values.items():
        if not valid_variant(variant):
            raise ValueError(f"{variant!r} is an invalid media variant ({version!r})")

        match media_value:
            case int():  # Media.ID
                media_id = media_value
            case Media():
                media_id = media_value.id
                if media_value.learning_package_id != av_learning_package_id:
                    raise ValueError(
                        f"Media LearningPackage does not match Asset: "
                        f"Tried to create AssetVersion {version!r} "
                        f"(Learning Package ID {av_learning_package_id!r}) "
                        f"with Media {media_value!r} "
                        f"(Learning Package ID {media_value.learning_package_id!r})"
                    )
            case bytes():
                media_type_str, _encoding = mimetypes.guess_type(variant)
                # We use "application/octet-stream" as a generic fallback media
                # type, per RFC 2046.
                media_type_str = media_type_str or "application/octet-stream"
                media_type = cached_media_type(media_type_str)
                media = media_api.get_or_create_file_media(
                    av_learning_package_id,
                    media_type.id,
                    data=media_value,
                    created=created,
                )
                media_id = media.id
            case _:
                raise ValueError(f"Invalid object for media variant: {media_value!r}")

        variants_to_media_ids[variant] = media_id

    AssetVersionMedia.objects.bulk_create(
        [
            AssetVersionMedia(
                asset_version=version,
                variant=variant,
                media_id=media_id,
            )
            for variant, media_id in variants_to_media_ids.items()
        ]
    )


def get_asset(asset_id: Asset.ID, /) -> Asset:
    """
    Get an Asset by its primary key (same as its PublishableEntity's ID).
    """
    return Asset.with_publishing_relations.get(pk=asset_id)


def get_asset_by_code(
    learning_package_id: LearningPackage.ID,
    /,
    asset_type_code: str,
    asset_code: str,
) -> Asset:
    """
    Get an Asset by its unique ``(asset_type, asset_code)`` within a LearningPackage.
    """
    return Asset.with_publishing_relations.get(
        learning_package_id=learning_package_id,
        asset_type__code=asset_type_code,
        asset_code=asset_code,
    )


def get_assets(
    learning_package_id: LearningPackage.ID,
    /,
    draft: bool | None = None,
    published: bool | None = None,
    asset_type_code: str | None = None,
) -> QuerySet[Asset]:
    """
    Fetch a QuerySet of Assets for a LearningPackage, with optional filters.

    Preloads the relations needed to read each Asset's draft and published
    versions.
    """
    qset = Asset.with_publishing_relations.filter(learning_package_id=learning_package_id).order_by("pk")

    if draft is not None:
        qset = qset.filter(publishable_entity__draft__version__isnull=not draft)
    if published is not None:
        qset = qset.filter(publishable_entity__published__version__isnull=not published)
    if asset_type_code is not None:
        qset = qset.filter(asset_type__code=asset_type_code)

    return qset


@dataclass(frozen=True)
class AssetFile:
    """One file variant of an AssetVersion."""

    variant: str
    media: Media


def get_asset_version_media(asset_version: AssetVersion) -> list[AssetFile]:
    """
    Return the list of file variants (variant + Media) for an AssetVersion.
    """
    return [
        AssetFile(variant=avm.variant, media=avm.media)
        for avm in asset_version.assetversionmedia_set.select_related(
            "media", "media__media_type"
        ).order_by("variant")
    ]


########################################################################################################################
# Asset Bundles


def create_asset_bundle(
    learning_package_id: LearningPackage.ID,
    /,
    asset_bundle_type: AssetBundleType,
    bundle_code: str,
    created: datetime,
    created_by: int | None,
    *,
    can_stand_alone: bool = True,
) -> AssetBundle:
    """
    Create a new AssetBundle.

    The ``entity_ref`` is conventionally derived as
    ``"{asset_bundle_type.code}:{bundle_code}"``.
    """
    entity_ref = f"{asset_bundle_type.code}:{bundle_code}"
    with atomic():
        publishable_entity = publishing_api.create_publishable_entity(
            learning_package_id,
            entity_ref,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        asset_bundle = AssetBundle.objects.create(
            publishable_entity=publishable_entity,
            learning_package_id=learning_package_id,
            asset_bundle_type=asset_bundle_type,
            bundle_code=bundle_code,
        )
    return asset_bundle


def create_asset_bundle_version(
    asset_bundle_id: AssetBundle.ID,
    /,
    version_num: int,
    *,
    title: str,
    assets: Iterable[Asset] | None = None,
    created: datetime,
    created_by: int | None,
) -> AssetBundleVersion:
    """
    Create a new AssetBundleVersion with the given (unordered) set of member Assets.

    All member Assets must belong to the same LearningPackage as the bundle. The
    members are registered as publishing *dependencies* of this version, so that
    the publishing system's "unpublished changes" detection accounts for changes
    to the member Assets.
    """
    asset_list = list(assets or [])
    with atomic():
        bundle = AssetBundle.objects.select_related("publishable_entity").get(pk=asset_bundle_id)
        learning_package_id = bundle.publishable_entity.learning_package_id

        # Validate that all members are from the bundle's learning package:
        if asset_list and (
            Asset.objects.filter(pk__in=[asset.id for asset in asset_list])
            .exclude(learning_package_id=learning_package_id)
            .exists()
        ):
            raise ValidationError("AssetBundle members must be from the same learning package.")

        publishable_entity_version = publishing_api.create_publishable_entity_version(
            asset_bundle_id,
            version_num=version_num,
            title=title,
            created=created,
            created_by=created_by,
            # Members are unpinned references, so they are dependencies of this version.
            dependencies=[asset.id for asset in asset_list],
        )
        asset_bundle_version = AssetBundleVersion.objects.create(
            publishable_entity_version=publishable_entity_version,
            asset_bundle_id=asset_bundle_id,
        )
        AssetBundleVersionAsset.objects.bulk_create(
            [
                AssetBundleVersionAsset(asset_bundle_version=asset_bundle_version, asset_id=asset.id)
                for asset in asset_list
            ]
        )

    return asset_bundle_version


def create_asset_bundle_and_version(
    learning_package_id: LearningPackage.ID,
    /,
    asset_bundle_type: AssetBundleType,
    bundle_code: str,
    *,
    title: str,
    assets: Iterable[Asset] | None = None,
    created: datetime,
    created_by: int | None = None,
    can_stand_alone: bool = True,
) -> tuple[AssetBundle, AssetBundleVersion]:
    """
    Create an AssetBundle and its first AssetBundleVersion atomically.
    """
    with atomic():
        asset_bundle = create_asset_bundle(
            learning_package_id,
            asset_bundle_type,
            bundle_code,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        asset_bundle_version = create_asset_bundle_version(
            asset_bundle.id,
            1,
            title=title,
            assets=assets or [],
            created=created,
            created_by=created_by,
        )
    return asset_bundle, asset_bundle_version


def create_next_asset_bundle_version(
    asset_bundle: AssetBundle | AssetBundle.ID,
    /,
    *,
    title: str | None = None,
    assets: Iterable[Asset] | None = None,
    created: datetime,
    created_by: int | None,
    force_version_num: int | None = None,
) -> AssetBundleVersion:
    """
    Create the next version of an AssetBundle.

    If ``assets`` is ``None``, the previous version's membership is carried over
    (use this for metadata-only changes, e.g. a title change). Otherwise the
    membership is *replaced* with the given set. Pass ``title=None`` to keep the
    current title.

    Use ``force_version_num`` to set a specific version number (e.g. when
    restoring from backup or importing legacy data).
    """
    with atomic():
        if isinstance(asset_bundle, int):
            asset_bundle = AssetBundle.objects.select_related("publishable_entity").get(pk=asset_bundle)
        assert isinstance(asset_bundle, AssetBundle)

        last_version = asset_bundle.versioning.latest
        if last_version is None:
            next_version_num = 1
        else:
            next_version_num = last_version.version_num + 1
        if force_version_num is not None:
            next_version_num = force_version_num

        if assets is None:
            # Metadata-only change: keep the same membership as the last version.
            assets = list(last_version.assets.all()) if last_version is not None else []

        if title is None:
            title = last_version.title if last_version is not None else ""

        return create_asset_bundle_version(
            asset_bundle.id,
            next_version_num,
            title=title,
            assets=assets,
            created=created,
            created_by=created_by,
        )


def get_asset_bundle(asset_bundle_id: AssetBundle.ID, /) -> AssetBundle:
    """
    Get an AssetBundle by its primary key (same as its PublishableEntity's ID).
    """
    return AssetBundle.with_publishing_relations.get(pk=asset_bundle_id)


def get_asset_bundle_by_code(
    learning_package_id: LearningPackage.ID,
    /,
    asset_bundle_type_code: str,
    bundle_code: str,
) -> AssetBundle:
    """
    Get an AssetBundle by its unique ``(asset_bundle_type, bundle_code)`` within a LearningPackage.
    """
    return AssetBundle.with_publishing_relations.get(
        learning_package_id=learning_package_id,
        asset_bundle_type__code=asset_bundle_type_code,
        bundle_code=bundle_code,
    )


def get_asset_bundles(
    learning_package_id: LearningPackage.ID,
    /,
    draft: bool | None = None,
    published: bool | None = None,
    asset_bundle_type_code: str | None = None,
) -> QuerySet[AssetBundle]:
    """
    Fetch a QuerySet of AssetBundles for a LearningPackage, with optional filters.
    """
    qset = AssetBundle.with_publishing_relations.filter(
        learning_package_id=learning_package_id
    ).order_by("pk")

    if draft is not None:
        qset = qset.filter(publishable_entity__draft__version__isnull=not draft)
    if published is not None:
        qset = qset.filter(publishable_entity__published__version__isnull=not published)
    if asset_bundle_type_code is not None:
        qset = qset.filter(asset_bundle_type__code=asset_bundle_type_code)

    return qset


@dataclass(frozen=True)
class AssetBundleMember:
    """A single member Asset of an AssetBundle, resolved to a specific AssetVersion."""

    asset_version: AssetVersion

    @property
    def asset(self) -> Asset:
        return self.asset_version.asset


def get_assets_in_bundle(
    asset_bundle: AssetBundle,
    *,
    published: bool,
) -> list[AssetBundleMember]:
    """
    Get the member Assets (resolved to their versions) of the draft or published
    version of the given AssetBundle.

    Members are unpinned, so each member Asset is resolved to its current draft
    or published AssetVersion, as appropriate. Members whose current version is
    ``None`` (e.g. soft-deleted Assets) are skipped.

    Args:
        asset_bundle: The AssetBundle, e.g. returned by ``get_asset_bundle()``.
        published: ``True`` for the published version of the bundle, ``False`` for
            the draft version.
    """
    assert isinstance(asset_bundle, AssetBundle)
    bundle_version = asset_bundle.versioning.published if published else asset_bundle.versioning.draft
    if bundle_version is None:
        # This bundle has not been published yet, or has been deleted.
        raise AssetBundleVersion.DoesNotExist
    assert isinstance(bundle_version, AssetBundleVersion)

    if published:
        version_rel = "asset__publishable_entity__published__version__assetversion"
    else:
        version_rel = "asset__publishable_entity__draft__version__assetversion"

    members: list[AssetBundleMember] = []
    for row in bundle_version.assetbundleversionasset_set.select_related("asset", version_rel):
        asset_version = row.asset.versioning.published if published else row.asset.versioning.draft
        if asset_version is not None:  # Skip soft-deleted members.
            members.append(AssetBundleMember(asset_version=asset_version))
    return members
