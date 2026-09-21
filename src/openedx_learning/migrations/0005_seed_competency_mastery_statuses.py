from django.db import migrations

# These ids and strings are literals, not references to `MasteryStatus`
# (`src/openedx_learning/applets/cbe/models.py`), and must stay that way: once
# applied, a migration has to keep meaning what it meant at the time it ran, so
# it cannot depend on a constant that a later edit to that enum could change.
# See `MasteryStatus` for the names these ids correspond to.


def forward(apps, schema_editor):
    """
    Seed the three CompetencyMasteryStatus rows, in rank order.
    """
    CompetencyMasteryStatus = apps.get_model("openedx_learning", "CompetencyMasteryStatus")
    CompetencyMasteryStatus.objects.get_or_create(id=1, defaults={"status": "AttemptedNotDemonstrated"})
    CompetencyMasteryStatus.objects.get_or_create(id=2, defaults={"status": "PartiallyAttempted"})
    CompetencyMasteryStatus.objects.get_or_create(id=3, defaults={"status": "Demonstrated"})


def revert(apps, schema_editor):  # pragma: no cover
    """
    Delete the seeded CompetencyMasteryStatus rows.
    """
    CompetencyMasteryStatus = apps.get_model("openedx_learning", "CompetencyMasteryStatus")
    CompetencyMasteryStatus.objects.filter(id__in=(1, 2, 3)).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("openedx_learning", "0004_learner_status_models"),
    ]

    operations = [
        migrations.RunPython(forward, revert),
    ]
