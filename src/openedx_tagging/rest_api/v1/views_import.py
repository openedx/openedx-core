"""
Taxonomy Import views
"""
from __future__ import annotations

import os

from django.http import FileResponse, Http404
from rest_framework.request import Request
from rest_framework.views import APIView


class TemplateView(APIView):
    """
    View which serves the static Taxonomy Import template files.

    **Example Requests**
        GET /tagging/rest_api/v1/import/template.csv
        GET /tagging/rest_api/v1/import/template.json

    **Query Returns**
        * 200 - Success
        * 404 - Template file not found
        * 405 - Method not allowed
    """
    http_method_names = ['get']

    template_dir = os.path.join(
        os.path.dirname(__file__),
        "../../import_export/",
    )
    allowed_ext_to_content_type = {
        "csv": "text/csv",
        "json": "application/json",
    }

    def get(self, request: Request, file_ext: str, *args, **kwargs) -> FileResponse:
        """
        Downloads the requested file as an attachment,
        or raises 404 if not found.
        """
        content_type = self.allowed_ext_to_content_type.get(file_ext)
        if not content_type:
            raise Http404

        filename = f"template.{file_ext}"
        content_disposition = f'attachment; filename="{filename}"'
        fh = open(os.path.join(self.template_dir, filename), "rb")
        response = FileResponse(fh, content_type=content_type)
        response['Content-Disposition'] = content_disposition
        return response
