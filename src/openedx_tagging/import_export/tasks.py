"""
Import and export celery tasks
"""
from __future__ import annotations

from io import BytesIO

from celery import shared_task  # type: ignore[import]

import openedx_tagging.import_export.api as import_export_api

from ..models import Taxonomy
from .import_plan import TagImportPlan, TagImportTask
from .parsers import ParserFormat


@shared_task
def import_tags_task(
    taxonomy: Taxonomy,
    file: BytesIO,
    parser_format: ParserFormat,
    replace=False,
) -> tuple[bool, TagImportTask, TagImportPlan | None]:
    """
    Runs import on a celery task
    """
    return import_export_api.import_tags(
        taxonomy,
        file,
        parser_format,
        replace,
    )


@shared_task
def export_tags_task(
    taxonomy: Taxonomy,
    output_format: ParserFormat,
) -> str:
    """
    Runs export on a celery task
    """
    return import_export_api.export_tags(taxonomy, output_format)
