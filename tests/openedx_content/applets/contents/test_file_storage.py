"""
Tests for file-backed Content
"""
from datetime import datetime, timezone

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from openedx_content.applets.contents import api as contents_api
from openedx_content.applets.contents.models import get_storage
from openedx_content.applets.publishing import api as publishing_api


class ContentFileStorageTestCase(TestCase):
    """
    Test the storage of files backing Content.
    """
    def setUp(self) -> None:
        """
        It's actually important that we use setUp and not setUpTestData here,
        because at least one of our tests will clear the get_storage cache,
        meaning that subsequent tests will get a new instance of the
        InMemoryStorage backend–and consequently wouldn't see any data loaded
        by setUpTestData.

        Recreating the test data for every test lets individual tests change the
        storage configuration without creating side-effects for other tests.
        """
        super().setUp()
        learning_package = publishing_api.create_learning_package(
            key="ContentFileStorageTestCase-test-key",
            title="Content File Storage Test Case Learning Package",
        )
        self.html_media_type = contents_api.get_or_create_media_type("text/html")
        self.html_content = contents_api.get_or_create_file_content(
            learning_package.id,
            self.html_media_type.id,
            data=b"<html>hello world!</html>",
            created=datetime.now(tz=timezone.utc),
        )

    def test_file_path(self):
        """
        Test that the file path doesn't change.

        If this test breaks, it means that we've changed the convention for
        where we're storing the backing files for Content, which means we'll be
        breaking backwards compatibility for everyone. Please be very careful if
        you're updating this test.
        """
        content = self.html_content
        assert content.path == f"content/{content.learning_package.uuid}/{content.hash_digest}"

        storage_root = settings.OPENEDX_LEARNING['MEDIA']['OPTIONS']['location']
        assert content.os_path() == f"{storage_root}/{content.path}"

    def test_read(self):
        """Make sure we can read the file data back."""
        assert b"<html>hello world!</html>" == self.html_content.read_file().read()

    @override_settings()
    def test_misconfiguration(self):
        """
        Require the OPENEDX_LEARNING setting for file operations.

        The main goal of this is that we don't want to store our files in the
        default MEDIA_ROOT, because that would make them publicly accessible.

        We set our OPENEDX_LEARNING value in test_settings.py. We're going to
        delete this setting in our test and make sure we raise the correct
        exception (The @override_settings decorator will set everything back to
        normal after the test completes.)
        """
        get_storage.cache_clear()
        del settings.OPENEDX_LEARNING
        with self.assertRaises(ImproperlyConfigured):
            self.html_content.read_file()
