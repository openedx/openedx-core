"""
Make Tag.external_id non-nullable.

Backfills a generated external_id (derived from the tag's value) for every
existing tag that doesn't already have one, then enforces NOT NULL on the
column.

See docs/openedx_tagging/decisions/0012-non-nullable-tag-external-id.rst for
the rationale.

Note that we copy the candidate-generation logic here instead of importing
`tag_external_id_candidate` from `openedx_tagging.models.utils`, so that this
migration keeps producing the exact same values it did when it first ran,
regardless of any future changes to that app code (matching the precedent in
`openedx_content/backcompat/publishing/migrations/0010_backfill_dependencies.py`).
"""
from __future__ import annotations

from django.db import migrations
from django.db.models import Q

import openedx_django_lib.fields

# Frozen copy of TAG_EXTERNAL_ID_MAX_LENGTH / tag_external_id_candidate from
# openedx_tagging.models.utils, as of when this migration was written. See the
# module docstring for why this isn't just imported.
_TAG_EXTERNAL_ID_MAX_LENGTH = 255

# Caps both the SQL batch size of each bulk_update() and how many Tag instances
# are held in memory at once before being flushed.
_BATCH_SIZE = 500


def _tag_external_id_candidate(value: str, attempt: int = 1) -> str:
    if attempt == 1:
        return value.strip()[:_TAG_EXTERNAL_ID_MAX_LENGTH].strip()
    suffix = f"-{attempt}"
    return value.strip()[: _TAG_EXTERNAL_ID_MAX_LENGTH - len(suffix)].strip() + suffix


def backfill(apps, _schema_editor):
    """
    Generate and persist an external_id for every tag that doesn't have one.

    "Doesn't have one" covers both NULL and an empty string: the field has always
    been blank=True, and this migration runs unmodified against arbitrary
    downstream data, so a row written by code outside this repo could plausibly
    hold "" rather than NULL for "no external_id".
    """
    Tag = apps.get_model("oel_tagging", "Tag")

    is_missing = Q(external_id__isnull=True) | Q(external_id="")
    has_value = ~is_missing

    taxonomy_ids = Tag.objects.filter(is_missing).values_list("taxonomy_id", flat=True).distinct()
    for taxonomy_id in taxonomy_ids:
        existing = {
            eid.casefold() for eid in
            Tag.objects.filter(has_value, taxonomy_id=taxonomy_id).values_list("external_id", flat=True)
        }
        batch = []
        for tag in Tag.objects.filter(is_missing, taxonomy_id=taxonomy_id).order_by("id").iterator():
            attempt = 1
            candidate = _tag_external_id_candidate(tag.value, attempt)
            while candidate.casefold() in existing:
                attempt += 1
                candidate = _tag_external_id_candidate(tag.value, attempt)
            existing.add(candidate.casefold())
            tag.external_id = candidate
            batch.append(tag)

            if len(batch) >= _BATCH_SIZE:
                Tag.objects.bulk_update(batch, ["external_id"], batch_size=_BATCH_SIZE)
                batch.clear()

        if batch:
            Tag.objects.bulk_update(batch, ["external_id"], batch_size=_BATCH_SIZE)


def reverse_backfill(_apps, _schema_editor):
    """
    No-op: rollback only loosens the NOT NULL constraint. The generated
    external_id values are intentionally left in place.
    """


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("oel_tagging", "0021_remove_system_defined_add_read_only"),
    ]

    operations = [
        migrations.RunPython(backfill, reverse_backfill),
        migrations.AlterField(
            model_name="tag",
            name="external_id",
            field=openedx_django_lib.fields.MultiCollationCharField(
                blank=True,
                db_collations={"mysql": "utf8mb4_unicode_ci", "sqlite": "NOCASE"},
                help_text="Used to link an Open edX Tag with a tag in an externally-defined taxonomy.",
                max_length=255,
            ),
        ),
    ]
