import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import openedx_django_lib.validators


class Migration(migrations.Migration):

    dependencies = [
        ('oel_tagging', '0021_remove_system_defined_add_read_only'),
        ('openedx_learning', '0009_seed_competency_mastery_statuses'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StudentCompetencyStatus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(validators=[openedx_django_lib.validators.validate_utc_datetime])),
                ('modified', models.DateTimeField(validators=[openedx_django_lib.validators.validate_utc_datetime])),
                ('status', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='openedx_learning.competencymasterystatus')),
                ('tag', models.ForeignKey(db_column='oel_tagging_tag_id', on_delete=django.db.models.deletion.PROTECT, related_name='+', to='oel_tagging.tag')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('user', 'tag'), name='oex_learning_studentcompetencystatus_user_tag_uniq'), models.CheckConstraint(condition=models.Q(('status__in', (20, 30))), name='oex_learning_studentcompetencystatus_status_allowed')],
            },
        ),
    ]
