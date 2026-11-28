"""
Tests of the Asset API
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, assert_type, cast

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import DataError, IntegrityError

from openedx_content.applets.assets import api
from openedx_content.applets.assets.models import Asset, AssetVersion
from openedx_content.applets.components import api as components_api
from openedx_content.applets.media import api as media_api
from openedx_content.applets.media.models import Media
from openedx_content.applets.publishing import api as publishing_api
from openedx_content.applets.publishing.models import LearningPackage
from openedx_django_lib.validators import normalize_unicode

if TYPE_CHECKING:
    from django.contrib.auth.models import User  # pylint: disable=imported-auth-user

    # Test that our model manager has correct typing
    assert_type(Asset.objects.create(), Asset)
    assert_type(Asset.objects.get(), Asset)
    assert_type(AssetVersion.objects.create(), AssetVersion)
    assert_type(AssetVersion.objects.get(), AssetVersion)

pytestmark = pytest.mark.django_db

now = datetime.now(tz=UTC)
later = now + timedelta(seconds=10)

BLACK_PNG_BYTES: bytes = base64.b64decode(  # A valid PNG image, that is a single black pixel
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQAAAAA3bvkkAAAACklEQVR4AWNgAAAAAgABc3UBGAAAAABJRU5ErkJggg=="
)
GREY_PNG_BYTES: bytes = base64.b64decode(  # A valid PNG image that is a single grey pixel
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4AWOwBQAAPwA+Eq7IEAAAAABJRU5ErkJggg=="
)


@pytest.fixture(name="lp")
def _lp() -> LearningPackage:
    """Get a Learning Package."""
    return publishing_api.create_learning_package(package_ref="assets-test-lp", title="Assets Test LP 1")


@pytest.fixture(name="lp2")
def _lp2() -> LearningPackage:
    return publishing_api.create_learning_package(package_ref="assets-test-lp2", title="Assets Test LP 2")


@pytest.fixture(name="user1")
def _user1(django_user_model) -> User:
    return django_user_model.objects.create(username="user1")


@pytest.fixture(name="user2")
def _user2(django_user_model) -> User:
    return django_user_model.objects.create(username="user2")


@pytest.fixture(name="black_png")
def _black_png(lp: LearningPackage) -> Media:
    media_type_id = media_api.get_or_create_media_type("image/png").id
    return media_api.get_or_create_file_media(lp.id, media_type_id, data=BLACK_PNG_BYTES, created=now)


@pytest.fixture(name="grey_png")
def _grey_png(lp: LearningPackage) -> Media:
    media_type_id = media_api.get_or_create_media_type("image/png").id
    return media_api.get_or_create_file_media(lp.id, media_type_id, data=GREY_PNG_BYTES, created=now)


@pytest.fixture(name="black")
def _black(lp: LearningPackage, black_png: Media) -> Asset:
    return api.create_asset_and_version(lp.id, path="black.png", created=now, media=black_png)[0]


@pytest.fixture(name="grey")
def _grey(lp: LearningPackage, grey_png: Media) -> Asset:
    return api.create_asset_and_version(lp.id, path="grey.png", created=now, media=grey_png)[0]


def make_component(lp: LearningPackage, component_code: str, dependencies: list[publishing_api.PublishableEntity.ID]):
    """Helper function to create a Component and version"""
    c, v = components_api.create_component_and_version(
        lp.id,
        component_type=components_api.get_or_create_component_type("xblock.v1", "html"),
        component_code=component_code,
        title=component_code,
        created=now,
        dependencies=dependencies,
    )
    return c, v


# Creating Assets (without versions/data) ######################################


def test_create_asset(lp: LearningPackage, user1: User) -> None:
    """Basic test of ``create_asset()``: Create an asset with no version."""
    path = "instructions.txt"

    asset = api.create_asset(lp.id, path=path, created=now, created_by=user1.id)
    asset.refresh_from_db()  # Ensure we check what was actually saved to database, not just returned

    assert_type(asset, Asset)
    assert asset.id
    assert_type(asset.id, Asset.ID)
    assert asset.publishable_entity.id == asset.id
    assert asset.publishable_entity.learning_package_id == lp.id
    assert asset.learning_package == lp
    assert asset.publishable_entity.can_stand_alone
    assert asset.publishable_entity.created == now
    assert asset.publishable_entity.created_by
    assert asset.publishable_entity.created_by.id == user1.id
    assert asset.path == path
    assert asset.permissions == api.AssetPermissions.PUBLIC.value
    assert not asset.allow_scripts  # Default must be False
    assert asset.versioning.latest is None
    assert asset.versioning.draft is None
    assert asset.versioning.published is None


def test_path_unique_per_lp(lp: LearningPackage, lp2: LearningPackage) -> None:
    """Test that path (filename) must be unique per learning package."""
    path = "instructions.txt"
    other_path = "other.pdf"

    asset1 = api.create_asset(lp.id, path=path, created=now, created_by=None)
    assert asset1.id

    with pytest.raises(api.AssetAlreadyExists, match=f'Asset with path "{path}" already exists.'):
        # Cannot create an asset with the same path (filename) in the same LP:
        api.create_asset(lp.id, path=path, created=now, created_by=None)

    # But creating an asset with the same path in a different LP is fine:
    asset2 = api.create_asset(lp2.id, path=path, created=now, created_by=None)
    assert asset2.id
    assert asset1.id != asset2.id
    assert asset1.path == asset2.path

    # And creating an asset with a different path is fine:
    asset3 = api.create_asset(lp.id, path=other_path, created=now, created_by=None)
    assert asset3.id
    assert asset1.id != asset3.id


def test_path_case_sensitive(lp: LearningPackage) -> None:
    """Test that path (filename) is case sensitive."""
    path1 = "case.txt"
    path2 = "Case.txt"

    asset1 = api.create_asset(lp.id, path=path1, created=now, created_by=None)
    asset2 = api.create_asset(lp.id, path=path2, created=now, created_by=None)
    asset1.refresh_from_db()
    asset2.refresh_from_db()
    assert asset1.id != asset2.id
    assert asset1.path == path1
    assert asset2.path == path2


@pytest.mark.parametrize(
    ("path", "valid"),
    [
        # ✅ Valid:
        ("image.jpg", True),
        ("image (2).jpg", True),
        ("python_lib.zip", True),
        ("course.elmwtqp8.tar.gz", True),
        ("IMG-004.JPEG", True),
        ("folder/example.pdf", True),
        ("textbooks/intro textbook/ch1.pdf", True),
        (".hidden.conf", True),
        ("Αθήνα.md", True),
        ("😎.md", True),
        ("long_path_" * 48, True),  # 480 characters long ('entity_ref' max is 500 including 'openedx-asset:' prefix)
        # The next filename has a narrow no break space before "AM" since that's what MacOS puts in screenshot names
        ("screen shots/Screenshot 2026-10-23 at 11.26.13\u202fAM.png", True),
        # ❌ Invalid:
        ("glob*.png", False),
        (" space_before.jpg ", False),
        ("space_after.jpg ", False),
        ("folder /x.jpg", False),
        ("newline.pdf\n", False),
        ("nul\x00.pdf", False),  # \x7f is the DEL control character
        ("del\x7f.pdf", False),  # \x7f is the DEL control character
        ("/image.jpg", False),
        ("./image.jpg", False),
        ("//image.jpg", False),
        ("../image.jpg", False),
        ("folder/", False),
        ("backslash\\image.jpg", False),
        ("folder/../image.jpg", False),
        ("double//image.jpg", False),
        ("../../../etc/passwd", False),
        ("", False),
        (".", False),
        ("..", False),
        ("long_path_" * 50, False),  # 500 characters long
    ],
)
def test_valid_invalid_paths(path: str, valid: bool, lp: LearningPackage) -> None:
    """Test validation of filenames"""
    assert api.is_valid_asset_path(path) is valid

    def create():
        return api.create_asset(lp.id, path=path, created=now, created_by=None)

    if valid:
        asset = create()
        assert asset.id
        asset.refresh_from_db()
        assert asset.path == path
    else:
        with pytest.raises(ValidationError, match="Path/filename is invalid or contains invalid characters."):
            create()

        # Also test the raw database constraint, bypassing python checks:
        asset = api.create_asset(lp.id, path="temp_path", created=now, created_by=None)
        # DataError is raised by MySQL if the string is to long; everything else is IntegrityError
        with pytest.raises((IntegrityError, DataError)):
            Asset.objects.filter(pk=asset.id).update(path=path)


@pytest.mark.parametrize(
    ("path", "normalized"),
    [
        # ✅ Already Normalized:
        ("caf\u00e9", True),
        ("Αθήνα", True),
        ("😎", True),
        ("\ufb01le.txt", True),  # "ﬁ" ligature: NFC leaves it alone (NFKC would turn it into "fi")
        # ❌ Needs normalization first:
        ("cafe\u0301", False),  # "café" with a combining mark
        ("Mu\u0308nchen/photo.png", False),  # "München" in NFD, as macOS often produces
        ("\u212b.txt", False),  # ANGSTROM SIGN: NFC maps it to "Å" (U+00C5)
    ],
)
def test_path_normalization(path: str, normalized: bool, lp: LearningPackage) -> None:
    """Test Unicode normalization of filenames"""
    assert api.is_valid_asset_path(path) is normalized

    def create():
        return api.create_asset(lp.id, path=path, created=now, created_by=None)

    if normalized:
        asset = create()
        assert asset.id
        asset.refresh_from_db()
        assert asset.path == path
    else:
        with pytest.raises(ValidationError, match="Path/filename is invalid or contains invalid characters."):
            create()

        new_path = normalize_unicode(path)
        test_path_normalization(new_path, True, lp)


@pytest.mark.parametrize(
    ("permissions", "valid"),
    [
        (api.AssetPermissions.PUBLIC, True),
        (api.AssetPermissions.ENROLLED, True),
        (api.AssetPermissions.STAFF, True),
        ("invalid", False),
        ("", False),
    ],
)
def test_valid_invalid_permissions(permissions: api.AssetPermissions, valid: bool, lp: LearningPackage) -> None:
    """Test validation of permissions value"""

    def create():
        return api.create_asset(lp.id, path="example.txt", created=now, created_by=None, permissions=permissions)

    if valid:
        asset = create()
        assert asset.id
        asset.refresh_from_db()
        assert asset.permissions == permissions
    else:
        with pytest.raises(RuntimeError, match="Invalid permissions enum value."):
            create()


def test_create_asset_manually(lp: LearningPackage) -> None:
    """Manually creating an asset is forbidden"""
    pe = publishing_api.create_publishable_entity(
        lp.id, "entity_ref", created=now, created_by=None, can_stand_alone=True
    )
    with pytest.raises(RuntimeError, match=re.escape("Use the ``create_asset...()`` APIs to create an Asset.")):
        Asset.objects.create(
            publishable_entity=pe,
            learning_package_id=lp.id,
            path="example.png",
            permissions=api.AssetPermissions.PUBLIC,
        )


# Creating Assets (with initial version/data) ##################################


def test_create_asset_and_version(lp: LearningPackage, user1: User) -> None:
    """Basic test of ``test_create_asset_and_version()``: Create an asset with initial version."""
    path = "black.png"

    (asset, asset_v1) = api.create_asset_and_version(
        lp.id,
        path=path,
        created=now,
        created_by=user1.id,
        media=BLACK_PNG_BYTES,
    )
    asset.refresh_from_db()  # Ensure we check what was actually saved to database, not just returned
    asset_v1.refresh_from_db()

    assert_type(asset, Asset)
    assert asset.id
    assert_type(asset.id, Asset.ID)
    assert asset.publishable_entity.id == asset.id
    assert asset.publishable_entity.learning_package_id == lp.id
    assert asset.learning_package == lp
    assert asset.publishable_entity.can_stand_alone
    assert asset.publishable_entity.created == now
    assert asset.publishable_entity.created_by
    assert asset.publishable_entity.created_by.id == user1.id
    assert asset.path == path
    assert asset.permissions == api.AssetPermissions.PUBLIC.value
    assert not asset.allow_scripts  # Default must be False
    assert asset.versioning.published is None  # Not published by default
    assert asset.versioning.draft
    assert asset.versioning.draft.pk == asset_v1.pk
    assert asset.versioning.latest
    assert asset.versioning.latest.pk == asset_v1.pk

    assert asset_v1.asset.id == asset.id
    assert_type(asset_v1.asset, Asset)
    assert asset_v1.title == path  # By default, title is set to path if not specified
    assert asset_v1.created == now
    assert asset_v1.created_by
    assert asset_v1.created_by.id == user1.id

    media = asset_v1.media
    assert media
    assert media.learning_package.id == lp.id
    assert str(media.media_type) == "image/png"
    assert media.hash_digest == "8d55c179d5f1516fa5594d7c76f3ef6a971c6c8a"
    assert media.size == len(BLACK_PNG_BYTES)
    assert media.has_file
    assert media.created == now


def test_path_unique_per_lp2(lp: LearningPackage, lp2: LearningPackage, black: Asset, black_png: Media) -> None:
    """
    Test that path (filename) must be unique per learning package.

    Similar to ``test_path_unique_per_lp()`` but this uses
    ``create_asset_and_version()`` instead of ``create_asset()``.
    """
    path = black.path
    other_path = "other.pdf"

    with pytest.raises(api.AssetAlreadyExists, match=f'Asset with path "{path}" already exists.'):
        # Cannot create an asset with the same path (filename) in the same LP:
        api.create_asset_and_version(lp, path=path, created=now, created_by=None, media=black_png)

    # But creating an asset with the same path in a different LP is fine:
    asset2, _ = api.create_asset_and_version(lp2, path=path, created=now, created_by=None, media=BLACK_PNG_BYTES)
    assert asset2.id
    assert black.id != asset2.id
    assert black.path == asset2.path

    # And creating an asset with a different path is fine:
    asset3, _ = api.create_asset_and_version(lp, path=other_path, created=now, created_by=None, media=black_png)
    assert asset3.id
    assert black.id != asset3.id


def test_create_two_assets(lp: LearningPackage, user1: User, black_png: Media) -> None:
    """Create two assets with the same contents"""
    path1 = "black1.png"
    path2 = "black2.png"

    (_asset1, asset1_v1) = api.create_asset_and_version(
        lp.id, path=path1, created=now, created_by=user1.id, media=black_png
    )
    (_asset2, asset2_v1) = api.create_asset_and_version(
        lp.id, path=path2, created=now, created_by=user1.id, media=black_png
    )

    # V1 of both assets uses the exact same Media instance:
    assert asset1_v1.media.id == black_png.id
    assert asset2_v1.media.id == black_png.id


@pytest.mark.parametrize("permissions", [api.AssetPermissions.ENROLLED, api.AssetPermissions.STAFF])
def test_create_asset_and_version_permissions(
    lp: LearningPackage, user1: User, black_png: Media, permissions: api.AssetPermissions
) -> None:
    """Create an asset and version with the specified permissions"""
    path = "black.png"

    (asset, _asset_v1) = api.create_asset_and_version(
        lp.id,
        path=path,
        created=now,
        created_by=user1.id,
        media=black_png,
        permissions=permissions,
        allow_scripts=True,
    )
    assert asset.permissions == permissions.value
    assert asset.allow_scripts


def test_create_asset_and_version_queries(
    lp: LearningPackage, user1: User, black_png: Media, django_assert_num_queries
) -> None:
    """Create an asset and version with the specified permissions"""
    path = "black.png"

    with django_assert_num_queries(25):
        api.create_asset_and_version(
            lp.id,
            path=path,
            created=now,
            created_by=user1.id,
            media=black_png,
        )


# Creating Subsequent Asset Versions ###########################################


def test_create_asset_two_versions(lp: LearningPackage, user1: User, black_png: Media, grey_png: Media) -> None:
    """Create an asset with two versions, v1 black and v2 grey"""
    path = "pixel.png"

    (asset, v1) = api.create_asset_and_version(
        lp.id, path=path, created=now, created_by=user1.id, media=black_png, title="Black Pixel"
    )
    v2 = api.create_next_asset_version(asset.id, media=grey_png, created=later, created_by=user1.id, title="Grey Pixel")

    asset.refresh_from_db()
    assert asset.versioning.draft
    assert asset.versioning.draft.pk == v2.pk

    assert v1.version_num == 1
    assert v1.title == "Black Pixel"

    assert v2.version_num == 2
    assert v2.title == "Grey Pixel"
    assert v2.created == later
    assert v2.media_id == grey_png.id


def test_create_asset_two_versions_manual(lp: LearningPackage, user1: User, black_png: Media, grey_png: Media) -> None:
    """Create an asset with two versions, v1 black and v3 grey, with no v2"""
    path = "pixel.png"

    (asset, v1) = api.create_asset_and_version(lp.id, path=path, created=now, created_by=user1.id, media=black_png)
    v3 = api.create_next_asset_version(
        asset.id, media=grey_png, created=later, created_by=user1.id, force_version_num=3
    )

    asset.refresh_from_db()
    assert asset.versioning.draft
    assert asset.versioning.draft.pk == v3.pk

    assert v1.version_num == 1
    assert v3.created == later
    assert v3.media_id == grey_png.id
    assert v3.version_num == 3


# Getting Assets ###############################################################


def test_get_asset_by_id(black: Asset, black_png: Media, grey: Asset, grey_png: Media):
    """Test retrieving asset by ID."""
    b = api.get_asset(black.id)
    assert b.id == black.id
    assert b.versioning.draft.media.id == black_png.id

    g = api.get_asset(grey.id)
    assert g.id == grey.id
    assert g.versioning.draft.media.id == grey_png.id

    with pytest.raises(Asset.DoesNotExist):
        api.get_asset(cast(Asset.ID, -400))


def test_get_asset_by_filename(  # pylint: disable=too-many-positional-arguments
    lp: LearningPackage, lp2: LearningPackage, black: Asset, black_png: Media, grey: Asset, grey_png: Media
):
    """Test retrieving asset by filename."""
    b = api.get_asset_by_path(lp.id, path="black.png")
    assert b.id == black.id
    assert b.versioning.draft.media.id == black_png.id

    g = api.get_asset_by_path(lp.id, path="grey.png")
    assert g.id == grey.id
    assert g.versioning.draft.media.id == grey_png.id

    with pytest.raises(Asset.DoesNotExist):
        # Different learning package:
        api.get_asset_by_path(lp2.id, path="grey.png")

    with pytest.raises(Asset.DoesNotExist):
        api.get_asset_by_path(lp2.id, path="404.png")


def test_get_assets(lp: LearningPackage, lp2: LearningPackage, black: Asset, grey: Asset):
    """
    Test getting all assets in a learning package, or a subset of assets
    """
    # We start with two assets in 'lp' (one published, one not), and none in 'lp2':
    publishing_api.publish_from_drafts(
        lp.id, publishing_api.get_all_drafts(lp.id).filter(entity=black.publishable_entity)
    )

    assert list(api.get_assets(lp)) == [black, grey]
    assert list(api.get_assets(lp, draft=True)) == [black, grey]
    assert list(api.get_assets(lp, published=True)) == [black]

    assert not list(api.get_assets(lp2))


# Updating Assets ##############################################################


def test_change_permissions(black: Asset):
    """Test changing an assets permissions using ``update_asset``"""
    assert black.permissions == api.AssetPermissions.PUBLIC
    assert not black.allow_scripts

    api.update_asset(black, permissions=api.AssetPermissions.ENROLLED)
    assert black.permissions == api.AssetPermissions.ENROLLED
    black.refresh_from_db()
    assert black.permissions == api.AssetPermissions.ENROLLED
    assert not black.allow_scripts

    api.update_asset(black, permissions=api.AssetPermissions.STAFF)
    assert black.permissions == api.AssetPermissions.STAFF
    black.refresh_from_db()
    assert black.permissions == api.AssetPermissions.STAFF
    assert not black.allow_scripts

    api.update_asset(black, permissions=api.AssetPermissions.PUBLIC, allow_scripts=True)
    assert black.permissions == api.AssetPermissions.PUBLIC
    assert black.allow_scripts
    black.refresh_from_db()
    assert black.permissions == api.AssetPermissions.PUBLIC
    assert black.allow_scripts


def test_update_asset_wont_pull_in_changes(black: Asset):
    """Calling ``update_asset()`` won't have unexpected side effects"""
    black.path = "foo"
    api.update_asset(black, allow_scripts=True)  # Should only change allow_scripts, not save changes to `path`
    black.refresh_from_db()
    assert black.path == "black.png"


def test_no_changing_path(black: Asset) -> None:
    """Test that an Asset's 'path' is immutable."""
    black.path = "foo"
    with pytest.raises(RuntimeError, match=re.escape("Use the ``update_asset()`` API to update an Asset.")):
        black.save()
    black.refresh_from_db()
    assert black.path == "black.png"


def test_no_changing_version(black: Asset, grey_png: Media) -> None:
    """Test modifying an AssetVersion is forbidden."""
    v1: AssetVersion = black.versioning.latest
    v1.media = grey_png
    with pytest.raises(RuntimeError, match="AssetVersion is immutable"):
        v1.save()


# Deleting Assets ##############################################################


def test_delete_assets(lp: LearningPackage, black: Asset, grey: Asset):
    """
    Test deleting an asset
    """
    # We start with two assets in 'lp' (one published, one not), and none in 'lp2':
    publishing_api.publish_from_drafts(
        lp.id, publishing_api.get_all_drafts(lp.id).filter(entity=black.publishable_entity)
    )
    assert list(api.get_assets(lp)) == [black, grey]
    # Then we delete 'black':
    publishing_api.soft_delete_draft(black.id)

    # It's still in the Learning Package, and still published, but its draft is gone:
    assert list(api.get_assets(lp)) == [black, grey]
    assert list(api.get_assets(lp, draft=True)) == [grey]  # black is gone
    assert list(api.get_assets(lp, published=True)) == [black]
    assert api.get_asset_by_path(lp, "black.png").versioning.draft is None

    # Publish the deletion:
    publishing_api.publish_all_drafts(lp.id)
    assert list(api.get_assets(lp)) == [black, grey]
    assert list(api.get_assets(lp, draft=True)) == [grey]  # black is gone
    assert list(api.get_assets(lp, published=True)) == [grey]  # black is gone
    assert api.get_asset_by_path(lp, "black.png").versioning.published is None


def test_undelete_assets(lp: LearningPackage, black: Asset, grey_png: Media, user2: User):
    """
    Test creating an asset with the same path (filename) as a deleted asset
    """
    assert black.created == now
    assert black.created_by is None
    # Delete the 'black' asset:
    publishing_api.soft_delete_draft(black.id)

    # Grey is the new black: create a new "black.png" asset with grey contents
    # This should create a new version of the deleted "black.png" asset.
    new_black, v2 = api.create_asset_and_version(
        lp, path=black.path, media=grey_png, created=later, created_by=user2.id
    )
    assert len(new_black.versioning.versions.all()) == 2
    assert new_black.versioning.draft.pk == v2.pk
    assert v2.version_num == 2
    assert v2.media.id == grey_png.id
    # Should the 'created' and 'created_by' be reset when un-deleting, or preserved? We'll say preserved.
    assert new_black.created == now  # Not 'later'
    assert new_black.created_by is None  # Not 'user2'
    new_black.refresh_from_db()
    assert new_black.created == now  # Not 'later'
    assert new_black.created_by is None  # Not 'user2'


def test_undelete_assets_refused(lp: LearningPackage, black: Asset):
    """
    Test creating an asset with the same path (filename) as a deleted asset is
    rejected when we pass ``overwrite_deleted=False``.
    """
    # Delete the 'black' asset:
    publishing_api.soft_delete_draft(black.id)

    with pytest.raises(api.AssetAlreadyExists, match=f'Asset with path "{black.path}" already exists.'):
        api.create_asset_and_version(lp, path=black.path, media=BLACK_PNG_BYTES, overwrite_deleted=False)


def test_undelete_default_title(lp: LearningPackage, black: Asset, grey_png: Media):
    """
    Test creating an asset with the same path (filename) as a deleted asset does
    not restore a deleted asset's custom title
    """
    # Set the 'black' asset's title to something custom, then delete it:
    api.create_next_asset_version(
        black.id, title="OLD Title of deleted asset", created=now, created_by=None, media=None
    )
    publishing_api.soft_delete_draft(black.id)

    # Grey is the new black: create a new "black.png" asset with grey contents:
    _, v2 = api.create_asset_and_version(lp, path=black.path, media=grey_png, created=later, created_by=None)
    # The title has been reset to the default title, the path/filename:
    assert v2.title == black.path


def test_undelete_custom_title(lp: LearningPackage, black: Asset, grey_png: Media):
    """
    Test creating an asset with the same path (filename) as a deleted asset can
    specify a custom title for the new asset:
    """
    publishing_api.soft_delete_draft(black.id)

    new_title = "✅ new black"
    # Grey is the new black: create a new "black.png" asset with grey contents:
    _, v2 = api.create_asset_and_version(
        lp, path=black.path, media=grey_png, created=later, created_by=None, title=new_title
    )
    assert v2.title == new_title


def test_undelete_permissions(lp: LearningPackage, black: Asset, grey_png: Media):
    """
    Creating an asset with the same path (filename) as a deleted asset should
    not change its permissions.
    """
    # Mark the 'black' asset as private:
    api.update_asset(black, permissions=api.AssetPermissions.STAFF)
    # Publish the 'black' asset:
    publishing_api.publish_all_drafts(lp.id)
    v1 = black.versioning.published
    # Delete the 'black' asset:
    publishing_api.soft_delete_draft(black.id)

    # Grey is the new black: create a new "black.png" asset with grey contents
    # This should create a new version of the deleted "black.png" asset.
    _, v2 = api.create_asset_and_version(lp, path=black.path, media=grey_png)

    black.refresh_from_db()
    assert black.versioning.latest == v2
    assert black.versioning.published == v1

    # It's important that creating a new draft asset doesn't change the
    # permissions of the asset unless the user specifically said to, because
    # otherwise this published "staff only" document would now be accessible
    # with PUBLIC perms. (Permissions are per-Asset not per AssetVersion.)
    assert black.permissions == api.AssetPermissions.STAFF


def test_undelete_allow_scripts(lp: LearningPackage):
    """
    Creating an asset with the same path (filename) as a deleted asset should
    not create a new asset with `allow_scripts=True` just because the deleted
    asset had that, unless the user very explicitly opts sets that.
    """
    # Create an HTML asset with allow_scripts=True
    path = "scripts.html"
    html_media_type = media_api.get_or_create_media_type("text/html").id
    html_media = media_api.get_or_create_file_media(lp.id, html_media_type, data=b"<html><script/></html>", created=now)
    orig_html_asset, _ = api.create_asset_and_version(lp.id, path, created=now, media=html_media, allow_scripts=True)
    # Delete the 'orig_html_asset' asset:
    publishing_api.soft_delete_draft(orig_html_asset.id)

    # Create a new HTML asset unrelated to the deleted one, but don't specify 'allow_scripts' one way or the other:
    new_html_media = media_api.get_or_create_file_media(lp.id, html_media_type, data=b"<html />", created=now)
    new_html_asset, _ = api.create_asset_and_version(lp.id, path, created=now, media=new_html_media)
    # It's important that this new HTML doesn't "accidentally" inherit allow_scripts=True from the deleted asset:
    assert not new_html_asset.allow_scripts


# Placeholder Assets ###########################################################


def test_upload_to_placeholder(lp: LearningPackage, black_png: Media, user1: User):
    """
    Uploading a file with the same path (filename) as a placeholder asset (an
    Asset with no versions) creates the first version of the placeholder, rather
    than a new Asset.
    """
    placeholder = api.create_asset(lp.id, path="later.png", created=now, created_by=None)
    component, _ = make_component(lp, "component1", dependencies=[placeholder.id])

    asset, v1 = api.create_asset_and_version(
        lp.id, path="later.png", media=black_png, created=later, created_by=user1.id
    )

    assert asset.id == placeholder.id
    assert v1.version_num == 1
    assert v1.title == "later.png"
    assert v1.media.id == black_png.id
    asset = api.get_asset(asset.id)
    assert asset.versioning.draft == v1
    assert asset.versioning.published is None
    # Uploading the file preserves the existing dependency on the placeholder:
    assert [v.entity_id for v in asset.publishable_entity.affects.all()] == [component.id]


# Legacy flat paths ############################################################


def test_legacy_flat_path_default(black: Asset) -> None:
    """Assets have no legacy_flat_path unless one is specified."""
    black.refresh_from_db()
    assert black.legacy_flat_path is None


@pytest.mark.parametrize("use_create_asset", [True, False])
def test_create_with_legacy_flat_path(use_create_asset: bool, lp: LearningPackage, lp2: LearningPackage) -> None:
    """
    Test creating an asset with a ``legacy_flat_path`` that differs from its
    ``path``, as for a file imported from a course export with a folder
    structure, and looking it up.
    """
    path = "images/photo 1.jpg"
    flat = "images_photo_1.jpg"
    if use_create_asset:
        asset = api.create_asset(lp.id, path=path, created=now, created_by=None, legacy_flat_path=flat)
    else:
        asset, _ = api.create_asset_and_version(lp.id, path=path, media=BLACK_PNG_BYTES, legacy_flat_path=flat)
    asset.refresh_from_db()
    assert asset.path == path
    assert asset.legacy_flat_path == flat

    assert api.get_asset_by_legacy_flat_path(lp.id, flat).id == asset.id
    assert api.get_asset_by_legacy_flat_path(lp, flat).id == asset.id
    # The flat path is not a path:
    with pytest.raises(Asset.DoesNotExist):
        api.get_asset_by_path(lp.id, flat)
    # And the path is not a flat path:
    with pytest.raises(Asset.DoesNotExist):
        api.get_asset_by_legacy_flat_path(lp.id, path)
    # Different learning package:
    with pytest.raises(Asset.DoesNotExist):
        api.get_asset_by_legacy_flat_path(lp2.id, flat)


def test_legacy_flat_path_same_as_path(lp: LearningPackage) -> None:
    """
    Files uploaded to the legacy "Files" page only kept their flat name, so the
    ``path`` and ``legacy_flat_path`` are often the same.
    """
    asset = api.create_asset(lp.id, path="photo_1.jpg", created=now, created_by=None, legacy_flat_path="photo_1.jpg")
    assert api.get_asset_by_path(lp.id, "photo_1.jpg").id == asset.id
    assert api.get_asset_by_legacy_flat_path(lp.id, "photo_1.jpg").id == asset.id


def test_legacy_flat_path_unique_per_lp(lp: LearningPackage, lp2: LearningPackage) -> None:
    """
    Two different paths can have the same flat name, but only one asset per
    learning package can have it.
    """
    flat = "images_photo_1.jpg"
    asset1 = api.create_asset(lp.id, path="images/photo 1.jpg", created=now, created_by=None, legacy_flat_path=flat)

    with pytest.raises(api.LegacyFlatPathAlreadyExists, match=f'Asset with legacy path "{flat}" already exists.'):
        api.create_asset(lp.id, path="images photo 1.jpg", created=now, created_by=None, legacy_flat_path=flat)
    with pytest.raises(api.LegacyFlatPathAlreadyExists) as exc_info:
        api.create_asset_and_version(lp.id, path="images photo 1.jpg", media=BLACK_PNG_BYTES, legacy_flat_path=flat)
    assert exc_info.value.legacy_flat_path == flat
    # Nothing was created by the failed attempts:
    with pytest.raises(Asset.DoesNotExist):
        api.get_asset_by_path(lp.id, "images photo 1.jpg")

    # But the same flat name in a different learning package is fine:
    asset2 = api.create_asset(lp2.id, path="images photo 1.jpg", created=now, created_by=None, legacy_flat_path=flat)
    assert asset2.id != asset1.id

    # And the database enforces it too:
    asset3 = api.create_asset(lp.id, path="other.jpg", created=now, created_by=None, legacy_flat_path="other.jpg")
    with pytest.raises(IntegrityError):
        Asset.objects.filter(pk=asset3.id).update(legacy_flat_path=flat)


def test_legacy_flat_path_collides_with_flat_path_equal_to_path(lp: LearningPackage) -> None:
    """
    A new asset whose path equals an existing asset's flat name can't take that
    flat name, which prevents ``/static/images_photo.png`` from becoming
    ambiguous...
    """
    api.create_asset(lp.id, path="images/photo.png", created=now, created_by=None, legacy_flat_path="images_photo.png")
    with pytest.raises(api.LegacyFlatPathAlreadyExists):
        api.create_asset(
            lp.id, path="images_photo.png", created=now, created_by=None, legacy_flat_path="images_photo.png"
        )


def test_legacy_flat_path_null_does_not_collide(lp: LearningPackage) -> None:
    """
    ...but only if the caller specifies the new asset's flat name. Assets
    without a ``legacy_flat_path`` never collide with anything in the flat
    namespace, so it's the caller's responsibility to always specify it in
    learning packages that use legacy asset keys.
    """
    a = api.create_asset(
        lp.id, path="images/photo.png", created=now, created_by=None, legacy_flat_path="images_photo.png"
    )
    b = api.create_asset(lp.id, path="images_photo.png", created=now, created_by=None)
    c = api.create_asset(lp.id, path="images_photo2.png", created=now, created_by=None)
    assert b.legacy_flat_path is None
    assert c.legacy_flat_path is None  # Many assets can have NULL
    assert api.get_asset_by_legacy_flat_path(lp.id, "images_photo.png").id == a.id
    assert api.get_asset_by_path(lp.id, "images_photo.png").id == b.id


def test_legacy_flat_path_case_sensitive(lp: LearningPackage) -> None:
    """Legacy flat paths are case-sensitive, like ``AssetKey``s."""
    lower = api.create_asset(lp.id, path="photo.png", created=now, created_by=None, legacy_flat_path="photo.png")
    upper = api.create_asset(lp.id, path="Photo.png", created=now, created_by=None, legacy_flat_path="Photo.png")
    assert api.get_asset_by_legacy_flat_path(lp.id, "photo.png").id == lower.id
    assert api.get_asset_by_legacy_flat_path(lp.id, "Photo.png").id == upper.id
    with pytest.raises(Asset.DoesNotExist):
        api.get_asset_by_legacy_flat_path(lp.id, "PHOTO.png")


@pytest.mark.parametrize(
    ("flat", "valid", "db_enforced"),
    [
        # ✅ Valid:
        ("images_photo_1.jpg", True, None),
        ("python_lib.zip", True, None),
        ("x__y.png", True, None),  # contentstore doesn't collapse repeated underscores in asset names
        ("50%25_off.pdf", True, None),  # contentstore keeps '%', so this is a literal "%25", not an encoded "%"
        ("a:b~c.png", True, None),  # ':' and '~' are allowed in AssetKeys, though compute_location() replaces them
        ("caf\u00e9.png", True, None),  # café (normalized 'é')
        ("Αθήνα.md", True, None),
        ("Å.txt", True, None),  # Not Unicode normalized (ANGSTROM SIGN), but a valid AssetKey, so we keep it as-is
        (".hidden.conf", True, None),
        (".", True, None),  # Valid AssetKey names, though unlikely in practice
        ("..", True, None),
        ("long_path_" * 48, True, None),  # 480 characters
        # ❌ Invalid, and the database also rejects it:
        ("", False, True),
        ("images/photo.png", False, True),  # a real path, not a flat one
        ("photo 1.jpg", False, True),
        (" photo.jpg", False, True),
        ("tab\t.jpg", False, True),
        ("newline.pdf\n", False, True),
        ("nul\x00.pdf", False, True),
        ("del\x7f.pdf", False, True),
        ("glob*.png", False, True),
        ("backslash\\image.jpg", False, True),
        ("long_path_" * 50, False, True),  # 500 characters
        # ❌ Invalid, but only checked in Python:
        ("a+b.png", False, False),
        ("a@b.png", False, False),
        ("image(2).jpg", False, False),
        ("😎.md", False, False),
        (  # café with a combining accent, which is not a "word" character, so not allowed in AssetKeys:
            "cafe\u0301.png",
            False,
            False,
        ),
    ],
)
def test_valid_invalid_legacy_flat_paths(flat: str, valid: bool, db_enforced: bool | None, lp: LearningPackage) -> None:
    """Test validation of legacy flat paths"""
    assert api.is_valid_legacy_flat_path(flat) is valid

    def create():
        return api.create_asset(lp.id, path="example.jpg", created=now, created_by=None, legacy_flat_path=flat)

    if valid:
        asset = create()
        asset.refresh_from_db()
        assert asset.legacy_flat_path == flat
        assert api.get_asset_by_legacy_flat_path(lp.id, flat).id == asset.id
    else:
        with pytest.raises(ValidationError, match="Legacy path is invalid or contains invalid characters."):
            create()

        # Same for setting it later:
        asset = api.create_asset(lp.id, path="temp_path", created=now, created_by=None)
        with pytest.raises(ValidationError, match="Legacy path is invalid or contains invalid characters."):
            api.update_asset(asset, legacy_flat_path=flat)

        # Also test the raw database constraint, bypassing python checks:
        if db_enforced:
            # DataError is raised by MySQL if the string is to long; everything else is IntegrityError
            with pytest.raises((IntegrityError, DataError)):
                Asset.objects.filter(pk=asset.id).update(legacy_flat_path=flat)


def test_set_legacy_flat_path_later(lp: LearningPackage, black: Asset) -> None:
    """
    ``update_asset`` can set the legacy flat path of an asset that doesn't have
    one, e.g. when the contentstore shim takes over an existing asset, or to
    correct a migration that somehow missed setting this; but it can't change it
    afterwards.
    """
    api.update_asset(black, legacy_flat_path="black.png")
    black.refresh_from_db()
    assert black.legacy_flat_path == "black.png"
    assert api.get_asset_by_legacy_flat_path(lp.id, "black.png").id == black.id

    # Setting the same value again, or passing None, is a no-op:
    api.update_asset(black, legacy_flat_path="black.png")
    api.update_asset(black, legacy_flat_path=None, allow_scripts=True)
    black.refresh_from_db()
    assert black.legacy_flat_path == "black.png"
    assert black.allow_scripts

    # But it can't be changed:
    with pytest.raises(ValidationError, match="Cannot change the legacy path"):
        api.update_asset(black, legacy_flat_path="other.png")
    black.refresh_from_db()
    assert black.legacy_flat_path == "black.png"


def test_set_legacy_flat_path_later_collision(black: Asset, grey: Asset) -> None:
    """``update_asset`` can't give an asset a flat name that another asset has."""
    api.update_asset(black, legacy_flat_path="black.png")
    with pytest.raises(api.LegacyFlatPathAlreadyExists):
        api.update_asset(grey, legacy_flat_path="black.png")
    grey.refresh_from_db()
    assert grey.legacy_flat_path is None


def test_deleted_asset_keeps_legacy_flat_path(lp: LearningPackage) -> None:
    """
    A deleted asset keeps its flat name, so a different asset can't take it
    (it would make legacy references to the deleted file resolve to a different
    file), and it can still be looked up.
    """
    asset, _ = api.create_asset_and_version(
        lp.id, path="images/photo.png", media=BLACK_PNG_BYTES, legacy_flat_path="images_photo.png"
    )
    publishing_api.soft_delete_draft(asset.id)

    assert api.get_asset_by_legacy_flat_path(lp.id, "images_photo.png").id == asset.id
    with pytest.raises(api.LegacyFlatPathAlreadyExists):
        api.create_asset_and_version(
            lp.id, path="images_photo.png", media=BLACK_PNG_BYTES, legacy_flat_path="images_photo.png"
        )


@pytest.mark.parametrize(
    ("old_flat", "new_flat", "expected_flat"),
    [
        ("black.png", "black.png", "black.png"),  # Same value: fine
        ("black.png", None, "black.png"),  # Not specified: keep the existing value
        (None, "black.png", "black.png"),  # Not set yet: set it
        (None, None, None),
    ],
)
def test_undelete_legacy_flat_path(
    old_flat: str | None, new_flat: str | None, expected_flat: str | None, lp: LearningPackage, grey_png: Media
) -> None:
    """
    Re-creating a deleted asset keeps or sets its legacy flat path.
    """
    old, _ = api.create_asset_and_version(lp.id, path="black.png", media=BLACK_PNG_BYTES, legacy_flat_path=old_flat)
    publishing_api.soft_delete_draft(old.id)

    new, v2 = api.create_asset_and_version(lp.id, path="black.png", media=grey_png, legacy_flat_path=new_flat)
    assert new.id == old.id
    assert v2.version_num == 2
    new.refresh_from_db()
    assert new.legacy_flat_path == expected_flat


def test_undelete_legacy_flat_path_mismatch(lp: LearningPackage, grey_png: Media) -> None:
    """
    Re-creating a deleted asset with a different legacy flat path is an error,
    and nothing is changed.
    """
    old, _ = api.create_asset_and_version(lp.id, path="black.png", media=BLACK_PNG_BYTES, legacy_flat_path="black.png")
    publishing_api.soft_delete_draft(old.id)

    with pytest.raises(ValidationError, match="Cannot change the legacy path"):
        api.create_asset_and_version(lp.id, path="black.png", media=grey_png, legacy_flat_path="other.png")
    old.refresh_from_db()
    assert old.legacy_flat_path == "black.png"
    assert old.versioning.latest.version_num == 1
    assert old.versioning.draft is None


def test_undelete_legacy_flat_path_taken(lp: LearningPackage, grey_png: Media) -> None:
    """
    Re-creating a deleted asset (with no legacy flat path) using a legacy flat
    path that another asset has is an error, and nothing is changed.
    """
    old, _ = api.create_asset_and_version(lp.id, path="images_photo.png", media=BLACK_PNG_BYTES)
    publishing_api.soft_delete_draft(old.id)
    api.create_asset(lp.id, path="images/photo.png", created=now, created_by=None, legacy_flat_path="images_photo.png")

    with pytest.raises(api.LegacyFlatPathAlreadyExists):
        api.create_asset_and_version(
            lp.id, path="images_photo.png", media=grey_png, legacy_flat_path="images_photo.png"
        )
    old.refresh_from_db()
    assert old.legacy_flat_path is None
    assert old.versioning.latest.version_num == 1
    assert old.versioning.draft is None


def test_upload_to_placeholder_legacy_flat_path(lp: LearningPackage, black_png: Media) -> None:
    """
    Uploading a file to a placeholder asset (with no versions) sets its legacy
    flat path.
    """
    placeholder = api.create_asset(lp.id, path="images/later.png", created=now, created_by=None)
    asset, _ = api.create_asset_and_version(
        lp.id, path="images/later.png", media=black_png, legacy_flat_path="images_later.png"
    )
    assert asset.id == placeholder.id
    assert api.get_asset_by_legacy_flat_path(lp.id, "images_later.png").id == placeholder.id


# Detecting asset usage ########################################################


def test_get_asset_usage(lp: LearningPackage, black: Asset, grey: Asset, django_assert_num_queries):
    """
    Test that, e.g. for the purposes of a course's "Files" page, we can
    efficiently get a list of assets and where each is used.
    """
    # Create some components that reference our assets:
    comp_blk_gry, _ = make_component(lp, "comp_blk_gry", dependencies=[black.id, grey.id])
    comp_black, _ = make_component(lp, "comp_black", dependencies=[black.id])
    comp_grey, _ = make_component(lp, "comp_grey", dependencies=[grey.id])

    with django_assert_num_queries(2):
        assets_with_usage = list(api.get_assets(lp, draft=True, used_by_draft_versions=True))

    with django_assert_num_queries(0):
        assert [a.id for a in assets_with_usage] == [black.id, grey.id]
        # The first asset, 'black.png', is used in two components:
        assert {c.entity_id for c in assets_with_usage[0].used_by_draft_versions} == {
            comp_blk_gry.id,
            comp_black.id,
        }
        # The second asset, 'grey.png', is also used in two components:
        assert {c.entity_id for c in assets_with_usage[1].used_by_draft_versions} == {
            comp_blk_gry.id,
            comp_grey.id,
        }


# Detecting broken references to assets ########################################


def test_broken_references(lp: LearningPackage, black: Asset, grey: Asset):
    """
    Test finding broken references to Assets:
    """
    # Create two components that reference static assets:
    make_component(lp, "component1", dependencies=[black.id])  # References 'black.png'
    make_component(lp, "component2", dependencies=[black.id, grey.id])  # References 'black.png' & 'grey.png'

    # At first, nothing is missing:
    assert api.get_missing_asset_dependencies(lp).count() == 0

    # Now delete those assets:
    publishing_api.soft_delete_draft(black.id)
    publishing_api.soft_delete_draft(grey.id)

    assert list(api.get_missing_asset_dependencies(lp)) == [
        black,
        grey,
    ]


def test_broken_references2(lp: LearningPackage, black: Asset, grey: Asset):
    """
    Test finding broken references to Assets (more complex)
    """
    # Create two components that reference static assets:
    c1, _c1v1 = make_component(lp, "component1", dependencies=[black.id])  # Initially references 'black.png'
    c2, _c2v1 = make_component(lp, "component2", dependencies=[black.id, grey.id])  # References 'black.png', 'grey.png'
    components_api.create_next_component_version(c2.id, media_to_replace={}, created=later, dependencies=[grey.id])
    publishing_api.publish_from_drafts(lp.id, publishing_api.get_all_drafts(lp.id).filter(entity=c1.publishable_entity))
    publishing_api.soft_delete_draft(c1.id)

    # Now: component1 has a published version that references 'black.png' and no draft version
    c1.refresh_from_db()
    assert list(c1.versioning.published.publishable_entity_version.dependencies.all()) == [black.publishable_entity]
    assert not c1.versioning.draft
    # Now: component2's newest draft version references only 'grey.png'.
    # It has no published version, and its previous version referenced both 'black.png' and 'grey.png'
    c2.refresh_from_db()
    assert list(c2.versioning.draft.publishable_entity_version.dependencies.all()) == [grey.publishable_entity]
    assert not c2.versioning.published

    # At first, nothing is missing:
    assert api.get_missing_asset_dependencies(lp).count() == 0

    # Now delete those assets:
    publishing_api.soft_delete_draft(black.id)
    publishing_api.soft_delete_draft(grey.id)

    # Only 'grey' is a broken reference from any draft component version:
    assert list(api.get_missing_asset_dependencies(lp)) == [grey]


def test_broken_references_placeholders(lp: LearningPackage, black: Asset, black_png: Media):
    """
    Test that placeholder assets (referenced but never uploaded) are reported as
    broken references, alongside deleted assets, until a file is uploaded.
    """
    placeholder = api.create_asset(lp.id, path="missing.png", created=now, created_by=None)
    unused_placeholder = api.create_asset(lp.id, path="unused.png", created=now, created_by=None)
    make_component(lp, "component1", dependencies=[black.id, placeholder.id])
    publishing_api.soft_delete_draft(black.id)

    # Both the deleted asset and the placeholder are missing. A placeholder that nothing depends on isn't listed:
    missing = list(api.get_missing_asset_dependencies(lp))
    assert missing == [black, placeholder]
    assert unused_placeholder not in missing

    # Once the file is uploaded, the placeholder is no longer missing:
    api.create_asset_and_version(lp.id, path="missing.png", media=black_png)
    assert list(api.get_missing_asset_dependencies(lp)) == [black]
