"""
Tests relating to serving static assets.
"""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from django.test import TestCase

from openedx_content.applets.components import api as components_api
from openedx_content.applets.components.api import AssetError
from openedx_content.applets.contents import api as contents_api
from openedx_content.applets.publishing import api as publishing_api
from openedx_content.applets.publishing.models import LearningPackage


class AssetTestCase(TestCase):
    """
    Test serving static assets (Content files, via Component lookup).
    """
    python_source_media_type: contents_api.MediaType
    problem_block_media_type: contents_api.MediaType
    html_media_type: contents_api.MediaType

    problem_type: components_api.ComponentType
    component: components_api.Component
    component_version: components_api.ComponentVersion

    problem_content: contents_api.Content
    python_source_asset: contents_api.Content
    html_asset_content: contents_api.Content

    learning_package: LearningPackage
    now: datetime

    @classmethod
    def setUpTestData(cls) -> None:
        """
        Create all the Content and Components we need.

        The individual tests are read-only.
        """
        cls.now = datetime(2024, 8, 24, tzinfo=timezone.utc)
        cls.problem_type = components_api.get_or_create_component_type(
            "xblock.v1", "problem"
        )
        cls.python_source_media_type = contents_api.get_or_create_media_type(
            "text/x-python",
        )
        cls.problem_block_media_type = contents_api.get_or_create_media_type(
            "application/vnd.openedx.xblock.v1.problem+xml",
        )
        cls.html_media_type = contents_api.get_or_create_media_type("text/html")

        cls.learning_package = publishing_api.create_learning_package(
            key="ComponentTestCase-test-key",
            title="Components Test Case Learning Package",
        )
        cls.component, cls.component_version = components_api.create_component_and_version(
            cls.learning_package.id,
            component_type=cls.problem_type,
            local_key="my_problem",
            title="My Problem",
            created=cls.now,
            created_by=None,
        )

        # ProblemBlock content that is stored as text Content, not a file.
        cls.problem_content = contents_api.get_or_create_text_content(
            cls.learning_package.id,
            cls.problem_block_media_type.id,
            text="<problem>(pretend problem OLX is here)</problem>",
            created=cls.now,
        )
        components_api.create_component_version_content(
            cls.component_version.pk,
            cls.problem_content.id,
            key="block.xml",
        )

        # Python source file, stored as a file. This is hypothetical, as we
        # don't actually support bundling grader files like this today.
        cls.python_source_asset = contents_api.get_or_create_file_content(
            cls.learning_package.id,
            cls.python_source_media_type.id,
            data=b"print('hello world!')",
            created=cls.now,
        )
        components_api.create_component_version_content(
            cls.component_version.pk,
            cls.python_source_asset.id,
            key="src/grader.py",
        )

        # An HTML file that is student downloadable
        cls.html_asset_content = contents_api.get_or_create_file_content(
            cls.learning_package.id,
            cls.html_media_type.id,
            data=b"<html>hello world!</html>",
            created=cls.now,
        )
        components_api.create_component_version_content(
            cls.component_version.pk,
            cls.html_asset_content.id,
            key="static/hello.html",
        )

    def test_no_component_version(self):
        """No ComponentVersion matching the UUID exists."""
        nonexistent_uuid = uuid4()
        response = components_api.get_redirect_response_for_component_asset(
            nonexistent_uuid,
            Path("static/foo.png"),
        )
        assert response.status_code == 404

        # None of the Open edX Core headers should be set...
        for header_name in response.headers:
            assert not header_name.startswith("X-Open-edX")

    def _assert_has_component_version_headers(self, headers):
        """
        Helper to verify common headers expected of successful requests.

        Note: The request header values in an HttpResponse will all have been
        serialized to strings.
        """
        assert headers["X-Open-edX-Component-Key"] == self.component.key
        assert headers["X-Open-edX-Component-Uuid"] == str(self.component.uuid)
        assert headers["X-Open-edX-Component-Version-Uuid"] == str(self.component_version.uuid)
        assert headers["X-Open-edX-Component-Version-Num"] == str(self.component_version.version_num)
        assert headers["X-Open-edX-Learning-Package-Key"] == self.learning_package.key
        assert headers["X-Open-edX-Learning-Package-Uuid"] == str(self.learning_package.uuid)

    def test_404s_with_component_version_info(self):
        """Test 404 errors in various failure scenarios..."""
        # These are all scenarios where the ComponentVersion exists, but the
        # request returns a 404 Not Found error for different reasons:
        paths_to_errors = {
            # Asset doesn't exist for this ComponentVersion at all
            "static/this-doesnt-exist.txt": AssetError.ASSET_PATH_NOT_FOUND_FOR_COMPONENT_VERSION,

            # This is testing that asset paths are case sensitive
            "static/HELLO.html": AssetError.ASSET_PATH_NOT_FOUND_FOR_COMPONENT_VERSION,

            # Text stored in the database directly instead of file storage.
            "block.xml": AssetError.ASSET_HAS_NO_DOWNLOAD_FILE,
        }
        for asset_path, expected_error in paths_to_errors.items():
            response = components_api.get_redirect_response_for_component_asset(
                self.component_version.uuid,
                Path(asset_path),
            )
            self._assert_has_component_version_headers(response.headers)
            assert response.status_code == 404
            assert response.headers["X-Open-edX-Error"] == str(expected_error)

    def _assert_html_content_headers(self, response):
        """Assert expected HttpResponse headers for a downloadable HTML file."""
        self._assert_has_component_version_headers(response.headers)
        assert response.status_code == 200
        assert response.headers["Etag"] == self.html_asset_content.hash_digest
        assert response.headers["Content-Type"] == "text/html"
        assert response.headers["X-Accel-Redirect"] == self.html_asset_content.path
        assert "X-Open-edX-Error" not in response.headers

    def test_public_asset_response(self):
        """Test an asset intended to be publicly available without auth."""
        response = components_api.get_redirect_response_for_component_asset(
            self.component_version.uuid,
            Path("static/hello.html"),
            public=True,
        )
        self._assert_html_content_headers(response)
        assert "must-revalidate" in response.headers["Cache-Control"]

    def test_private_asset_response(self):
        """Test an asset intended to require auth checks."""
        response = components_api.get_redirect_response_for_component_asset(
            self.component_version.uuid,
            Path("static/hello.html"),
            public=False,
        )
        self._assert_html_content_headers(response)
        assert "private" in response.headers["Cache-Control"]
