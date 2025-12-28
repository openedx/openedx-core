"""
Views for the media server application

(serves media files in dev or low-traffic instances).
"""
from pathlib import Path

from django.core.exceptions import ObjectDoesNotExist
from django.http import FileResponse, Http404

from openedx_learning.apps.openedx_content.applets.components.api import look_up_component_version_content


def component_asset(
    request, learning_package_key, component_key, version_num, asset_path
):
    """
    Serve the ComponentVersion asset data.

    This function maps from a logical URL with Component and verison data like:
      media_server/component_asset/course101/finalexam-problem14/1/static/images/fig3.png
    To the actual data file as stored in file/object storage, which looks like:
      media/055499fd-f670-451a-9727-501ea9dfbf5b/a9528d66739a297aa0cd17106b0bc0f7515b8e78

    TODO:
    * ETag support
    * Range queries
    * Serving from a different domain than the rest of the service
    """
    try:
        cvc = look_up_component_version_content(
            learning_package_key, component_key, version_num, asset_path
        )
    except ObjectDoesNotExist:
        raise Http404("File not found")  # pylint: disable=raise-missing-from

    response = FileResponse(cvc.raw_content.file, filename=Path(asset_path).name)
    response["Content-Type"] = cvc.raw_content.mime_type

    return response
