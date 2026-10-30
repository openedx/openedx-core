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

from openedx_content.applets.assets import api
from openedx_content.applets.assets.models import Asset, AssetPermissions, AssetVersion
from openedx_content.applets.components import api as components_api
from openedx_content.applets.media import api as media_api
from openedx_content.applets.media.models import Media
from openedx_content.applets.publishing import api as publishing_api
from openedx_content.applets.publishing.models import LearningPackage

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
    assert asset.permissions == AssetPermissions.PUBLIC.value
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
    "path, valid",
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
        # The next filename has a narrow no break space before "AM" since that's what MacOS puts in screenshot names
        ("screen shots/Screenshot 2026-10-23 at 11.26.13\u202fAM.png", True),
        # ❌ Invalid:
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
    ],
)
def test_valid_invalid_paths(path: str, valid: bool, lp: LearningPackage) -> None:
    """Test validation of filenames"""

    def create():
        return api.create_asset(lp.id, path=path, created=now, created_by=None)

    if valid:
        asset = create()
        assert asset.id
        asset.refresh_from_db()
        assert asset.path == path
    else:
        with pytest.raises(ValidationError, match="Asset path is invalid."):
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
            permissions=AssetPermissions.PUBLIC,
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
    assert asset.permissions == AssetPermissions.PUBLIC.value
    assert not asset.allow_scripts  # Default must be False
    assert asset.versioning.published is None  # Not published by default
    assert asset.versioning.draft
    assert asset.versioning.draft.pk is asset_v1.pk
    assert asset.versioning.latest
    assert asset.versioning.latest.pk is asset_v1.pk

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


@pytest.mark.parametrize("permissions", [AssetPermissions.LOCKED, AssetPermissions.STAFF])
def test_create_asset_and_version_permissions(
    lp: LearningPackage, user1: User, black_png: Media, permissions: AssetPermissions
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
    assert black.permissions == AssetPermissions.PUBLIC
    assert not black.allow_scripts

    api.update_asset(black, permissions=AssetPermissions.LOCKED)
    assert black.permissions == AssetPermissions.LOCKED
    black.refresh_from_db()
    assert black.permissions == AssetPermissions.LOCKED
    assert not black.allow_scripts

    api.update_asset(black, permissions=AssetPermissions.STAFF)
    assert black.permissions == AssetPermissions.STAFF
    black.refresh_from_db()
    assert black.permissions == AssetPermissions.STAFF
    assert not black.allow_scripts

    api.update_asset(black, permissions=AssetPermissions.PUBLIC, allow_scripts=True)
    assert black.permissions == AssetPermissions.PUBLIC
    assert black.allow_scripts
    black.refresh_from_db()
    assert black.permissions == AssetPermissions.PUBLIC
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
    reject when we pass ``overwrite_deleted=False``.
    """
    # Delete the 'black' asset:
    publishing_api.soft_delete_draft(black.id)

    with pytest.raises(api.AssetAlreadyExists, match=f'Asset with path "{black.path}" already exists.'):
        api.create_asset_and_version(lp, path=black.path, media=BLACK_PNG_BYTES, overwrite_deleted=False)


def test_undelete_permissions(lp: LearningPackage, black: Asset, grey_png: Media):
    """
    Creating an asset with the same path (filename) as a deleted asset should
    not change its permissions.
    """
    # Mark the 'black' asset as private:
    api.update_asset(black, permissions=AssetPermissions.STAFF)
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
    assert black.permissions == AssetPermissions.STAFF


# Detecting broken references to assets ########################################


def make_component(lp: LearningPackage, component_code: str):
    return components_api.create_component_and_version(
        lp.id,
        component_type=components_api.get_or_create_component_type("xblock.v1", "html"),
        component_code=component_code,
        title=component_code,
        created=now,
    )


def test_broken_references(lp: LearningPackage, black: Asset, grey: Asset):
    """
    Test finding broken references to Assets:
    """
    # Create two components that reference static assets:
    _c1, c1v1 = make_component(lp, "component1")  # References 'black.png'
    _c2, c2v1 = make_component(lp, "component2")  # References 'black.png' and 'grey.png'
    # TODO: where should dependencies between Component and Asset get created?
    publishing_api.set_version_dependencies(c1v1.pk, dependencies=[black.id])
    publishing_api.set_version_dependencies(c2v1.pk, dependencies=[black.id, grey.id])

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
    c1, c1v1 = make_component(lp, "component1")  # Initially references 'black.png'
    c2, c2v1 = make_component(lp, "component2")  # Initially references 'black.png' and 'grey.png'
    # TODO: where should dependencies between Component and Asset get created?
    publishing_api.set_version_dependencies(c1v1.pk, dependencies=[black.id])
    publishing_api.set_version_dependencies(c2v1.pk, dependencies=[black.id, grey.id])
    c2v2 = components_api.create_next_component_version(c2.id, media_to_replace={}, created=later)
    publishing_api.set_version_dependencies(c2v2.pk, dependencies=[grey.id])
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
