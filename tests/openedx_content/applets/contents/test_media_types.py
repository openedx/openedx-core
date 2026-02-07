"""
A few tests to make sure our MediaType lookups are working as expected.
"""
from django.test import TestCase

from openedx_content.applets.contents import api as contents_api


class MediaTypeTest(TestCase):
    """Basic testing of our Media Types for Content"""

    def test_get_or_create_dedupe(self):
        """
        Make sure we're not creating redundant rows for the same media type.
        """
        # The first time, a row is created for "text/html"
        text_media_type_1 = contents_api.get_or_create_media_type("text/plain")

        # This should return the previously created row.
        text_media_type_2 = contents_api.get_or_create_media_type("text/plain")
        assert text_media_type_1 == text_media_type_2

        # This is a different type though...
        svg_media_type = contents_api.get_or_create_media_type("image/svg+xml")
        assert text_media_type_1 != svg_media_type
