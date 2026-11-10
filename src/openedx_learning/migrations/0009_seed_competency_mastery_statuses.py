from django.db import migrations

# These ids and strings are literals, not references to `MasteryStatus`
# (`src/openedx_learning/applets/cbe/models/learner_status.py`), and must stay that way: once
# applied, a migration has to keep meaning what it meant at the time it ran, so
# it cannot depend on a constant that a later edit to that enum could change.
# See `MasteryStatus` for the names these ids correspond to.


def forward(apps, schema_editor):
    """
    Seed the three CompetencyMasteryStatus rows, in rank order.
    """
    CompetencyMasteryStatus = apps.get_model("openedx_learning", "CompetencyMasteryStatus")
    CompetencyMasteryStatus.objects.get_or_create(id=10, defaults={"status": "AttemptedNotDemonstrated"})
    CompetencyMasteryStatus.objects.get_or_create(id=20, defaults={"status": "PartiallyAttempted"})
    CompetencyMasteryStatus.objects.get_or_create(id=30, defaults={"status": "Demonstrated"})


def revert(apps, schema_editor):
    """
    Delete every CompetencyMasteryStatus row, leaving the table as 0008 created it.
    """
    CompetencyMasteryStatus = apps.get_model("openedx_learning", "CompetencyMasteryStatus")
    CompetencyMasteryStatus.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("openedx_learning", "0008_competency_mastery_status"),
    ]

    operations = [
        migrations.RunPython(forward, revert),
    ]
