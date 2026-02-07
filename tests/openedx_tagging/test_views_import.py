"""
Tests import REST API views.
"""
from __future__ import annotations

import ddt  # type: ignore[import]
from rest_framework import status
from rest_framework.test import APITestCase

TAXONOMY_TEMPLATE_URL = "/tagging/rest_api/v1/import/{filename}"


@ddt.ddt
class TestTemplateView(APITestCase):
    """
    Tests the taxonomy template downloads.
    """
    @ddt.data(
        ("template.csv", "text/csv"),
        ("template.json", "application/json"),
    )
    @ddt.unpack
    def test_download(self, filename, content_type):
        url = TAXONOMY_TEMPLATE_URL.format(filename=filename)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.headers['Content-Type'] == content_type
        assert response.headers['Content-Disposition'] == f'attachment; filename="{filename}"'
        assert int(response.headers['Content-Length']) > 0

    def test_download_not_found(self):
        url = TAXONOMY_TEMPLATE_URL.format(filename="template.txt")
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_download_method_not_allowed(self):
        url = TAXONOMY_TEMPLATE_URL.format(filename="template.txt")
        response = self.client.post(url)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
