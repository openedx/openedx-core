from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('openedx_learning', '0007_alter_criterion_override_help_text'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompetencyMasteryStatus',
            fields=[
                ('id', models.SmallAutoField(primary_key=True, serialize=False)),
                ('status', models.CharField(max_length=64, unique=True)),
            ],
        ),
    ]
