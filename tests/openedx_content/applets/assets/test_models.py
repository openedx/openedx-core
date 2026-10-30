"""
Tests of the Asset models

Pretty minimal, as most tests are in ``test_api.py``
"""

from typing import TYPE_CHECKING, assert_type

from openedx_content.applets.assets.models import Asset, AssetVersion

if TYPE_CHECKING:
    # Test that our model manager has correct typing
    assert_type(Asset.objects.create(), Asset)
    assert_type(Asset.objects.get(), Asset)
    assert_type(AssetVersion.objects.create(), AssetVersion)
    assert_type(AssetVersion.objects.get(), AssetVersion)
