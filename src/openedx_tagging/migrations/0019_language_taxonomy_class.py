"""
Data migration to update the _taxonomy_class on the Languages system taxonomy
to reflect the new module path after the package rename.

Old: openedx_tagging.core.tagging.models.system_defined.LanguageTaxonomy
New: openedx_tagging.models.system_defined.LanguageTaxonomy
"""

from django.db import migrations

OLD_CLASS = "openedx_tagging.core.tagging.models.system_defined.LanguageTaxonomy"
NEW_CLASS = "openedx_tagging.models.system_defined.LanguageTaxonomy"


def update_language_taxonomy_class(apps, schema_editor):
    Taxonomy = apps.get_model("oel_tagging", "Taxonomy")
    Taxonomy.objects.filter(_taxonomy_class=OLD_CLASS).update(_taxonomy_class=NEW_CLASS)


def revert_language_taxonomy_class(apps, schema_editor):
    Taxonomy = apps.get_model("oel_tagging", "Taxonomy")
    Taxonomy.objects.filter(_taxonomy_class=NEW_CLASS).update(_taxonomy_class=OLD_CLASS)


class Migration(migrations.Migration):

    dependencies = [
        ("oel_tagging", "0018_objecttag_is_copied"),
    ]

    operations = [
        migrations.RunPython(update_language_taxonomy_class, revert_language_taxonomy_class),
    ]
