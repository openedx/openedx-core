"""
Basic tests for the assets API (Assets and AssetBundles).
"""
from datetime import datetime, timezone
from typing import cast

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

import openedx_content.api as content_api
from openedx_content.applets.publishing import api as publishing_api
from openedx_content.applets.publishing.models import LearningPackage, PublishableContentModelRegistry
from openedx_content.models_api import Asset, AssetBundle, AssetBundleVersion, AssetType, AssetVersion


class AssetsTestCase(TestCase):
    """Base class with commonly used test data for the assets applet."""

    learning_package: LearningPackage
    now: datetime
    ebook_type: AssetType
    document_type: AssetType

    @classmethod
    def setUpTestData(cls) -> None:
        cls.learning_package = publishing_api.create_learning_package(
            package_ref="AssetsTestCase-test-key",
            title="Assets Test Case Learning Package",
        )
        cls.now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        cls.ebook_type = content_api.get_or_create_asset_type("ebook")
        cls.document_type = content_api.get_or_create_asset_type("document")
        cls.bundle_type = content_api.get_or_create_asset_bundle_type("video_with_subtitles")

    def create_asset(
        self,
        *,
        asset_code: str = "asset_1",
        title: str = "Test Asset",
        asset_type: AssetType | None = None,
        media: dict | None = None,
    ) -> tuple[Asset, AssetVersion]:
        """Helper to quickly create an Asset and its first version."""
        return content_api.create_asset_and_version(
            self.learning_package.id,
            asset_type=asset_type or self.ebook_type,
            asset_code=asset_code,
            title=title,
            created=self.now,
            created_by=None,
            media=media,
        )


class AssetApiTestCase(AssetsTestCase):
    """Tests for the Asset (leaf) API."""

    def test_create_asset_and_version(self) -> None:
        """A new Asset has a draft v1 and no published version."""
        asset, asset_version = self.create_asset()
        assert isinstance(asset, Asset)
        assert isinstance(asset_version, AssetVersion)
        assert asset_version.version_num == 1
        assert asset_version in asset.versioning.versions.all()
        assert asset.versioning.draft == asset_version
        assert asset.versioning.published is None
        assert asset.versioning.has_unpublished_changes
        assert asset.publishable_entity.can_stand_alone

    def test_asset_with_multiple_media_variants(self) -> None:
        """An AssetVersion can hold multiple file variants (e.g. pdf + txt)."""
        _asset, asset_version = self.create_asset(
            media={"book.pdf": b"%PDF-1.4 fake pdf bytes", "notes.txt": b"some notes"},
        )
        files = content_api.get_asset_version_media(asset_version)
        assert {f.variant for f in files} == {"book.pdf", "notes.txt"}
        by_variant = {f.variant: f for f in files}
        assert str(by_variant["book.pdf"].media.media_type) == "application/pdf"
        assert str(by_variant["notes.txt"].media.media_type) == "text/plain"
        assert by_variant["book.pdf"].media.size == len(b"%PDF-1.4 fake pdf bytes")

    def test_create_next_asset_version(self) -> None:
        """Next version carries media forward as a delta; None deletes a variant."""
        asset, _v1 = self.create_asset(media={"book.pdf": b"v1 pdf"})
        v2 = content_api.create_next_asset_version(
            asset.id,
            media_to_replace={"notes.txt": b"added in v2", "book.pdf": None},
            created=self.now,
        )
        assert v2.version_num == 2
        files = content_api.get_asset_version_media(v2)
        # book.pdf was deleted, notes.txt was added.
        assert {f.variant for f in files} == {"notes.txt"}

    def test_get_asset_and_by_code(self) -> None:
        """get_asset / get_asset_by_code round-trip; missing raises DoesNotExist."""
        asset, _v1 = self.create_asset(asset_code="findme")
        assert content_api.get_asset(asset.id) == asset
        assert content_api.get_asset_by_code(self.learning_package.id, "ebook", "findme") == asset
        with pytest.raises(Asset.DoesNotExist):
            content_api.get_asset(cast(Asset.ID, -500))

    def test_get_assets_filters(self) -> None:
        """get_assets filters by draft/published state and asset type."""
        published_asset, _ = self.create_asset(asset_code="published")
        publishing_api.publish_all_drafts(self.learning_package.id, published_at=self.now)
        draft_only_asset, _ = self.create_asset(asset_code="draft_only", asset_type=self.document_type)

        assert list(content_api.get_assets(self.learning_package.id, published=True)) == [published_asset]
        assert list(content_api.get_assets(self.learning_package.id, published=False)) == [draft_only_asset]
        assert list(content_api.get_assets(self.learning_package.id, asset_type_code="document")) == [
            draft_only_asset
        ]
        assert set(content_api.get_assets(self.learning_package.id)) == {published_asset, draft_only_asset}

    def test_type_idempotency_and_entity_ref(self) -> None:
        """Types are get-or-created idempotently; entity_ref uses the type code."""
        assert content_api.get_or_create_asset_type("ebook") == self.ebook_type
        asset, _v1 = self.create_asset(asset_code="my_book")
        assert asset.asset_type.code == "ebook"
        assert asset.publishable_entity.entity_ref == "ebook:my_book"

    def test_asset_code_unique_per_type(self) -> None:
        """asset_code may repeat across types, but not within a (lp, type)."""
        self.create_asset(asset_code="shared", asset_type=self.ebook_type)
        # Same code, different type -> allowed.
        self.create_asset(asset_code="shared", asset_type=self.document_type)
        # Same code, same type -> rejected.
        with pytest.raises(IntegrityError):
            self.create_asset(asset_code="shared", asset_type=self.ebook_type)

    def test_registered_publishable_models(self) -> None:
        """Asset/AssetVersion are registered as a publishable model pair."""
        assert PublishableContentModelRegistry.get_versioned_model_cls(Asset) is AssetVersion


class AssetBundleApiTestCase(AssetsTestCase):
    """Tests for the AssetBundle API and membership."""

    def setUp(self) -> None:
        super().setUp()
        self.asset_1, _ = self.create_asset(asset_code="asset_1", title="Asset 1")
        self.asset_2, _ = self.create_asset(asset_code="asset_2", title="Asset 2")

    def create_bundle(self, assets=None, *, bundle_code="bundle_1", title="Bundle"):
        """Helper to create an AssetBundle and its first version."""
        return content_api.create_asset_bundle_and_version(
            self.learning_package.id,
            asset_bundle_type=self.bundle_type,
            bundle_code=bundle_code,
            title=title,
            assets=assets,
            created=self.now,
            created_by=None,
        )

    def test_create_empty_bundle_and_version(self) -> None:
        """An empty bundle has a draft v1 and no published version."""
        bundle, bundle_version = self.create_bundle()
        assert isinstance(bundle, AssetBundle)
        assert isinstance(bundle_version, AssetBundleVersion)
        assert bundle_version.version_num == 1
        assert bundle.versioning.draft == bundle_version
        assert bundle.versioning.published is None
        assert not content_api.get_assets_in_bundle(bundle, published=False)
        assert bundle.publishable_entity.entity_ref == "video_with_subtitles:bundle_1"

    def test_bundle_with_members_draft(self) -> None:
        """Draft membership resolves each Asset to its current draft version."""
        bundle, _bv = self.create_bundle(assets=[self.asset_1, self.asset_2])
        members = content_api.get_assets_in_bundle(bundle, published=False)
        assert {m.asset.id for m in members} == {self.asset_1.id, self.asset_2.id}
        # Members are resolved (unpinned) to each Asset's draft AssetVersion.
        assert {m.asset_version for m in members} == {
            self.asset_1.versioning.draft,
            self.asset_2.versioning.draft,
        }
        # Nothing is published yet.
        with pytest.raises(AssetBundleVersion.DoesNotExist):
            content_api.get_assets_in_bundle(bundle, published=True)

    def test_publish_and_read_published_members(self) -> None:
        """After publishing, the published membership is readable."""
        bundle, _bv = self.create_bundle(assets=[self.asset_1, self.asset_2])
        publishing_api.publish_all_drafts(self.learning_package.id, published_at=self.now)

        bundle = content_api.get_asset_bundle(bundle.id)  # re-fetch to clear versioning cache
        members = content_api.get_assets_in_bundle(bundle, published=True)
        assert {m.asset.id for m in members} == {self.asset_1.id, self.asset_2.id}

    def test_create_next_bundle_version(self) -> None:
        """Replacing membership bumps the version; assets=None keeps membership."""
        bundle, _bv = self.create_bundle(assets=[self.asset_1])

        v2 = content_api.create_next_asset_bundle_version(
            bundle.id,
            assets=[self.asset_1, self.asset_2],
            created=self.now,
            created_by=None,
        )
        assert v2.version_num == 2

        # Metadata-only change (assets=None) keeps the same membership.
        v3 = content_api.create_next_asset_bundle_version(
            bundle.id,
            title="Renamed Bundle",
            created=self.now,
            created_by=None,
        )
        assert v3.version_num == 3
        assert v3.title == "Renamed Bundle"

        bundle = content_api.get_asset_bundle(bundle.id)
        members = content_api.get_assets_in_bundle(bundle, published=False)
        assert {m.asset.id for m in members} == {self.asset_1.id, self.asset_2.id}

    def test_duplicate_member_rejected(self) -> None:
        """The same Asset cannot appear twice in a bundle version."""
        with pytest.raises(IntegrityError):
            self.create_bundle(assets=[self.asset_1, self.asset_1])

    def test_member_must_be_same_learning_package(self) -> None:
        """An Asset from another learning package cannot be a member."""
        other_lp = publishing_api.create_learning_package(
            package_ref="AssetsTestCase-other-lp",
            title="Other Learning Package",
        )
        other_asset = content_api.create_asset(
            other_lp.id,
            asset_type=self.ebook_type,
            asset_code="foreign",
            created=self.now,
            created_by=None,
        )
        with pytest.raises(ValidationError):
            self.create_bundle(assets=[other_asset], bundle_code="bad_bundle")

    def test_registered_publishable_models(self) -> None:
        """AssetBundle/AssetBundleVersion are registered as a publishable model pair."""
        assert PublishableContentModelRegistry.get_versioned_model_cls(AssetBundle) is AssetBundleVersion
