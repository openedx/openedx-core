"""
Seed the system-default CompetencyRuleProfile: the one row where every scope column is null.

Per ADR-0002 Decision 3, this is the rule every CompetencyCriterion falls back to when nothing
more specific applies, so a deployment that adds no profiles of its own still gets an 80%
threshold.
"""
from django.db import migrations

# Fixed rather than uuid.uuid4(), so this shared system-default row has the same external
# identifier in every deployment, not a fresh random one each time this migration runs.
_DEFAULT_RULE_PROFILE_UUID = "5b3e8f5c-3b0e-4b1a-9b1e-6b6b6b6b6b6b"


def seed_default_rule_profile(apps, schema_editor):
    """Create the all-null-scope CompetencyRuleProfile."""
    CompetencyRuleProfile = apps.get_model('openedx_learning', 'CompetencyRuleProfile')
    CompetencyRuleProfile.objects.create(
        uuid=_DEFAULT_RULE_PROFILE_UUID,
        rule_type='Grade',
        rule_payload={'op': 'gte', 'value': 0.8, 'scale': 'percent'},
        archived=False,
        # apps.get_model() returns a historical model reconstructed from migration state, which
        # does not carry CompetencyRuleProfile's custom save() (and so never computes this).
        # organization_id/course_id/competency_taxonomy_id are all null for this row, so every
        # segment of the "org:X,course:Y,taxonomy:Z" format is blank.
        scope_code='org:,course:,taxonomy:',
    )


def remove_default_rule_profile(apps, schema_editor):
    """Delete the all-null-scope CompetencyRuleProfile, reversing seed_default_rule_profile."""
    CompetencyRuleProfile = apps.get_model('openedx_learning', 'CompetencyRuleProfile')
    CompetencyRuleProfile.objects.filter(
        organization__isnull=True,
        course__isnull=True,
        competency_taxonomy__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('openedx_learning', '0002_competency_criteria'),
    ]

    operations = [
        migrations.RunPython(seed_default_rule_profile, remove_default_rule_profile),
    ]
