"""
Seed the system-default CompetencyRuleProfile: the one row where every scope column is null.

Per ADR-0002 Decision 3, this is the rule every CompetencyCriterion falls back to when nothing
more specific applies, so a deployment that adds no profiles of its own still gets an 80%
threshold.
"""
from django.db import migrations


def seed_default_rule_profile(apps, schema_editor):
    """Create the all-null-scope CompetencyRuleProfile."""
    CompetencyRuleProfile = apps.get_model('openedx_learning', 'CompetencyRuleProfile')
    CompetencyRuleProfile.objects.create(
        rule_type='Grade',
        rule_payload={'op': 'gte', 'value': 0.8, 'scale': 'percent'},
        archived=False,
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
