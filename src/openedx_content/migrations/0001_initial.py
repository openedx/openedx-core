"""
This migration effectively moves the models from various authoring apps in the
backcompat package to the new consolidated openedx_content app. It does *not* do
any actual database operations.

See the ADR 0020-merge-authoring-apps-into-openedx-content.rst for details.
"""
import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.migrations.operations.special import SeparateDatabaseAndState

import openedx_django_lib.fields
import openedx_django_lib.validators


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('oel_collections', '0006_remove_all_field_state_for_move_to_applet'),
        ('oel_components', '0005_remove_all_field_state_for_move_to_applet'),
        ('oel_contents', '0002_remove_all_field_state_for_move_to_applet'),
        ('oel_publishing', '0011_remove_all_field_state_for_move_to_applet'),
        ('oel_sections', '0002_remove_all_field_state_for_move_to_applet'),
        ('oel_subsections', '0002_remove_all_field_state_for_move_to_applet'),
        ('oel_units', '0002_remove_all_field_state_for_move_to_applet'),
    ]

    operations = [
        SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='PublishableEntity',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID')),
                        ('key', openedx_django_lib.fields.MultiCollationCharField(db_collations={'mysql': 'utf8mb4_bin', 'sqlite': 'BINARY'}, db_column='_key', max_length=500)),
                        ('created', models.DateTimeField(validators=[openedx_django_lib.validators.validate_utc_datetime])),
                        ('can_stand_alone', models.BooleanField(default=True, help_text='Set to True when created independently, False when created as part of a container.')),
                        ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'verbose_name': 'Publishable Entity',
                        'verbose_name_plural': 'Publishable Entities',
                        'db_table': 'oel_publishing_publishableentity',
                    },
                ),
                migrations.CreateModel(
                    name='ComponentType',
                    fields=[
                        ('id', models.AutoField(primary_key=True, serialize=False)),
                        ('namespace', openedx_django_lib.fields.MultiCollationCharField(db_collations={'mysql': 'utf8mb4_bin', 'sqlite': 'BINARY'}, max_length=100)),
                        ('name', openedx_django_lib.fields.MultiCollationCharField(blank=True, db_collations={'mysql': 'utf8mb4_bin', 'sqlite': 'BINARY'}, max_length=100)),
                    ],
                    options={
                        'db_table': 'oel_components_componenttype',
                    },
                ),
                migrations.CreateModel(
                    name='PublishableEntityVersion',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID')),
                        ('title', openedx_django_lib.fields.MultiCollationCharField(blank=True, db_collations={'mysql': 'utf8mb4_unicode_ci', 'sqlite': 'NOCASE'}, default='', max_length=500)),
                        ('version_num', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                        ('created', models.DateTimeField(validators=[openedx_django_lib.validators.validate_utc_datetime])),
                        ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                        ('entity', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='openedx_content.publishableentity')),
                    ],
                    options={
                        'verbose_name': 'Publishable Entity Version',
                        'verbose_name_plural': 'Publishable Entity Versions',
                        'db_table': 'oel_publishing_publishableentityversion',
                    },
                ),
                migrations.CreateModel(
                    name='Content',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('size', models.PositiveBigIntegerField(validators=[django.core.validators.MaxValueValidator(50000000)])),
                        ('hash_digest', models.CharField(editable=False, max_length=40)),
                        ('has_file', models.BooleanField()),
                        ('text', openedx_django_lib.fields.MultiCollationTextField(blank=True, db_collations={'mysql': 'utf8mb4_unicode_ci', 'sqlite': 'NOCASE'}, max_length=50000, null=True)),
                        ('created', models.DateTimeField(validators=[openedx_django_lib.validators.validate_utc_datetime])),
                    ],
                    options={
                        'verbose_name': 'Content',
                        'verbose_name_plural': 'Contents',
                        'db_table': 'oel_contents_content',
                    },
                ),
                migrations.CreateModel(
                    name='EntityList',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                    ],
                    options={
                        'db_table': 'oel_publishing_entitylist',
                    },
                ),
                migrations.CreateModel(
                    name='LearningPackage',
                    fields=[
                        ('id', models.AutoField(primary_key=True, serialize=False)),
                        ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID')),
                        ('key', openedx_django_lib.fields.MultiCollationCharField(db_collations={'mysql': 'utf8mb4_bin', 'sqlite': 'BINARY'}, db_column='_key', max_length=500)),
                        ('title', openedx_django_lib.fields.MultiCollationCharField(db_collations={'mysql': 'utf8mb4_unicode_ci', 'sqlite': 'NOCASE'}, max_length=500)),
                        ('description', openedx_django_lib.fields.MultiCollationTextField(blank=True, db_collations={'mysql': 'utf8mb4_unicode_ci', 'sqlite': 'NOCASE'}, default='', max_length=10000)),
                        ('created', models.DateTimeField(validators=[openedx_django_lib.validators.validate_utc_datetime])),
                        ('updated', models.DateTimeField(validators=[openedx_django_lib.validators.validate_utc_datetime])),
                    ],
                    options={
                        'verbose_name': 'Learning Package',
                        'verbose_name_plural': 'Learning Packages',
                        'db_table': 'oel_publishing_learningpackage',
                    },
                ),
                migrations.CreateModel(
                    name='MediaType',
                    fields=[
                        ('id', models.AutoField(primary_key=True, serialize=False)),
                        ('type', openedx_django_lib.fields.MultiCollationCharField(db_collations={'mysql': 'utf8mb4_unicode_ci', 'sqlite': 'NOCASE'}, max_length=127)),
                        ('sub_type', openedx_django_lib.fields.MultiCollationCharField(db_collations={'mysql': 'utf8mb4_unicode_ci', 'sqlite': 'NOCASE'}, max_length=127)),
                        ('suffix', openedx_django_lib.fields.MultiCollationCharField(blank=True, db_collations={'mysql': 'utf8mb4_unicode_ci', 'sqlite': 'NOCASE'}, max_length=127)),
                    ],
                    options={
                        'db_table': "oel_contents_mediatype",
                    },
                ),
                migrations.CreateModel(
                    name='PublishableEntityVersionDependency',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                    ],
                    options={
                        'db_table': 'oel_publishing_publishableentityversiondependency',
                    },
                ),
                migrations.CreateModel(
                    name='PublishLog',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID')),
                        ('message', openedx_django_lib.fields.MultiCollationCharField(blank=True, db_collations={'mysql': 'utf8mb4_unicode_ci', 'sqlite': 'NOCASE'}, default='', max_length=500)),
                        ('published_at', models.DateTimeField(validators=[openedx_django_lib.validators.validate_utc_datetime])),
                    ],
                    options={
                        'verbose_name': 'Publish Log',
                        'verbose_name_plural': 'Publish Logs',
                        'db_table': 'oel_publishing_publishlog',
                    },
                ),
                migrations.CreateModel(
                    name='PublishLogRecord',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('dependencies_hash_digest', models.CharField(blank=True, default='', editable=False, max_length=8)),
                    ],
                    options={
                        'verbose_name': 'Publish Log Record',
                        'verbose_name_plural': 'Publish Log Records',
                        'db_table': 'oel_publishing_publishlogrecord',
                    },
                ),
                migrations.CreateModel(
                    name='PublishSideEffect',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                    ],
                    options={
                        'verbose_name': 'Publish Side Effect',
                        'verbose_name_plural': 'Publish Side Effects',
                        'db_table': 'oel_publishing_publishsideeffect',
                    },
                ),
                migrations.CreateModel(
                    name='Collection',
                    fields=[
                        ('id', models.AutoField(primary_key=True, serialize=False)),
                        ('key', openedx_django_lib.fields.MultiCollationCharField(db_collations={'mysql': 'utf8mb4_bin', 'sqlite': 'BINARY'}, db_column='_key', max_length=500)),
                        ('title', openedx_django_lib.fields.MultiCollationCharField(db_collations={'mysql': 'utf8mb4_unicode_ci', 'sqlite': 'NOCASE'}, help_text='The title of the collection.', max_length=500)),
                        ('description', openedx_django_lib.fields.MultiCollationTextField(blank=True, db_collations={'mysql': 'utf8mb4_unicode_ci', 'sqlite': 'NOCASE'}, default='', help_text='Provides extra information for the user about this collection.', max_length=10000)),
                        ('enabled', models.BooleanField(default=True, help_text='Disabled collections are "soft deleted", and should be re-enabled before use, or be deleted.')),
                        ('created', models.DateTimeField(auto_now_add=True, validators=[openedx_django_lib.validators.validate_utc_datetime])),
                        ('modified', models.DateTimeField(auto_now=True, validators=[openedx_django_lib.validators.validate_utc_datetime])),
                        ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'verbose_name_plural': 'Collections',
                        'db_table': 'oel_collections_collection',
                    },
                ),
                migrations.CreateModel(
                    name='Component',
                    fields=[
                        ('publishable_entity', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='openedx_content.publishableentity')),
                        ('local_key', openedx_django_lib.fields.MultiCollationCharField(db_collations={'mysql': 'utf8mb4_bin', 'sqlite': 'BINARY'}, max_length=500)),
                    ],
                    options={
                        'verbose_name': 'Component',
                        'verbose_name_plural': 'Components',
                        'db_table': 'oel_components_component',
                    },
                ),
                migrations.CreateModel(
                    name='Container',
                    fields=[
                        ('publishable_entity', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='openedx_content.publishableentity')),
                    ],
                    options={
                        'db_table': 'oel_publishing_container',
                    },
                ),
                migrations.CreateModel(
                    name='Draft',
                    fields=[
                        ('entity', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='openedx_content.publishableentity')),
                    ],
                    options={
                        'db_table': 'oel_publishing_draft',
                    },
                ),
                migrations.CreateModel(
                    name='Published',
                    fields=[
                        ('entity', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='openedx_content.publishableentity')),
                    ],
                    options={
                        'verbose_name': 'Published Entity',
                        'verbose_name_plural': 'Published Entities',
                        'db_table': 'oel_publishing_published',
                    },
                ),
                migrations.CreateModel(
                    name='CollectionPublishableEntity',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created', models.DateTimeField(auto_now_add=True, validators=[openedx_django_lib.validators.validate_utc_datetime])),
                        ('collection', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='openedx_content.collection')),
                        ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                        ('entity', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.publishableentity')),
                    ],
                    options={
                        'db_table': 'oel_collections_collectionpublishableentity',
                    },
                ),
                migrations.AddField(
                    model_name='collection',
                    name='entities',
                    field=models.ManyToManyField(related_name='collections', through='openedx_content.CollectionPublishableEntity', to='openedx_content.publishableentity'),
                ),
                migrations.CreateModel(
                    name='ComponentVersion',
                    fields=[
                        ('publishable_entity_version', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='openedx_content.publishableentityversion')),
                    ],
                    options={
                        'verbose_name': 'Component Version',
                        'verbose_name_plural': 'Component Versions',
                        'db_table': 'oel_components_componentversion',
                    },
                ),
                migrations.CreateModel(
                    name='ContainerVersion',
                    fields=[
                        ('publishable_entity_version', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='openedx_content.publishableentityversion')),
                    ],
                    options={
                        'db_table': 'oel_publishing_containerversion',
                    },
                ),
                migrations.CreateModel(
                    name='ComponentVersionContent',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('key', openedx_django_lib.fields.MultiCollationCharField(db_collations={'mysql': 'utf8mb4_bin', 'sqlite': 'BINARY'}, db_column='_key', max_length=500)),
                        ('content', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.content')),
                    ],
                    options={
                        'db_table': 'oel_components_componentversioncontent',
                    },
                ),
                migrations.CreateModel(
                    name='DraftChangeLog',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID')),
                        ('changed_at', models.DateTimeField(validators=[openedx_django_lib.validators.validate_utc_datetime])),
                        ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'verbose_name': 'Draft Change Log',
                        'verbose_name_plural': 'Draft Change Logs',
                        'db_table': 'oel_publishing_draftchangelog',
                    },
                ),
                migrations.CreateModel(
                    name='DraftChangeLogRecord',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('dependencies_hash_digest', models.CharField(blank=True, default='', editable=False, max_length=8)),
                        ('draft_change_log', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='records', to='openedx_content.draftchangelog')),
                        ('entity', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.publishableentity')),
                        ('new_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.publishableentityversion')),
                        ('old_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='openedx_content.publishableentityversion')),
                    ],
                    options={
                        'verbose_name': 'Draft Change Log Record',
                        'verbose_name_plural': 'Draft Change Log Records',
                        'db_table': 'oel_publishing_draftchangelogrecord',
                    },
                ),
                migrations.CreateModel(
                    name='DraftSideEffect',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('cause', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='causes', to='openedx_content.draftchangelogrecord')),
                        ('effect', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='affected_by', to='openedx_content.draftchangelogrecord')),
                    ],
                    options={
                        'verbose_name': 'Draft Side Effect',
                        'verbose_name_plural': 'Draft Side Effects',
                        'db_table': 'oel_publishing_draftsideeffect',
                    },
                ),
                migrations.CreateModel(
                    name='EntityListRow',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('order_num', models.PositiveIntegerField()),
                        ('entity', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.publishableentity')),
                        ('entity_list', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='openedx_content.entitylist')),
                        ('entity_version', models.ForeignKey(null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='openedx_content.publishableentityversion')),
                    ],
                    options={
                        'db_table': 'oel_publishing_entitylistrow',
                        'ordering': ['order_num'],
                    },
                ),
                migrations.AddField(
                    model_name='publishableentity',
                    name='learning_package',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='publishable_entities', to='openedx_content.learningpackage'),
                ),
                migrations.AddConstraint(
                    model_name='learningpackage',
                    constraint=models.UniqueConstraint(fields=('key',), name='oel_publishing_lp_uniq_key'),
                ),
                migrations.AddField(
                    model_name='draftchangelog',
                    name='learning_package',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='openedx_content.learningpackage'),
                ),
                migrations.AddField(
                    model_name='content',
                    name='learning_package',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='openedx_content.learningpackage'),
                ),
                migrations.AddField(
                    model_name='collection',
                    name='learning_package',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='openedx_content.learningpackage'),
                ),
                migrations.AddConstraint(
                    model_name='mediatype',
                    constraint=models.UniqueConstraint(fields=('type', 'sub_type', 'suffix'), name='oel_contents_uniq_t_st_sfx'),
                ),
                migrations.AddField(
                    model_name='content',
                    name='media_type',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='openedx_content.mediatype'),
                ),
                migrations.AddField(
                    model_name='publishableentityversiondependency',
                    name='referenced_entity',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.publishableentity'),
                ),
                migrations.AddField(
                    model_name='publishableentityversiondependency',
                    name='referring_version',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='openedx_content.publishableentityversion'),
                ),
                migrations.AddField(
                    model_name='publishableentityversion',
                    name='dependencies',
                    field=models.ManyToManyField(related_name='affects', through='openedx_content.PublishableEntityVersionDependency', to='openedx_content.publishableentity'),
                ),
                migrations.AddField(
                    model_name='publishlog',
                    name='learning_package',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='openedx_content.learningpackage'),
                ),
                migrations.AddField(
                    model_name='publishlog',
                    name='published_by',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
                ),
                migrations.AddField(
                    model_name='publishlogrecord',
                    name='entity',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.publishableentity'),
                ),
                migrations.AddField(
                    model_name='publishlogrecord',
                    name='new_version',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.publishableentityversion'),
                ),
                migrations.AddField(
                    model_name='publishlogrecord',
                    name='old_version',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='openedx_content.publishableentityversion'),
                ),
                migrations.AddField(
                    model_name='publishlogrecord',
                    name='publish_log',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='records', to='openedx_content.publishlog'),
                ),
                migrations.AddField(
                    model_name='publishsideeffect',
                    name='cause',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='causes', to='openedx_content.publishlogrecord'),
                ),
                migrations.AddField(
                    model_name='publishsideeffect',
                    name='effect',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='affected_by', to='openedx_content.publishlogrecord'),
                ),
                migrations.AddField(
                    model_name='component',
                    name='component_type',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='openedx_content.componenttype'),
                ),
                migrations.AddField(
                    model_name='component',
                    name='learning_package',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='openedx_content.learningpackage'),
                ),
                migrations.CreateModel(
                    name='Section',
                    fields=[
                        ('container', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='openedx_content.container')),
                    ],
                    options={
                        'abstract': False,
                        'db_table': "oel_sections_section",
                    },
                    bases=('openedx_content.container',),
                ),
                migrations.CreateModel(
                    name='Subsection',
                    fields=[
                        ('container', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='openedx_content.container')),
                    ],
                    options={
                        'abstract': False,
                        'db_table': "oel_subsections_subsection",
                    },
                    bases=('openedx_content.container',),
                ),
                migrations.CreateModel(
                    name='Unit',
                    fields=[
                        ('container', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='openedx_content.container')),
                    ],
                    options={
                        'abstract': False,
                        'db_table': "oel_units_unit",
                    },
                    bases=('openedx_content.container',),
                ),
                migrations.AddField(
                    model_name='draft',
                    name='draft_log_record',
                    field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='openedx_content.draftchangelogrecord'),
                ),
                migrations.AddField(
                    model_name='draft',
                    name='version',
                    field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.publishableentityversion'),
                ),
                migrations.AddField(
                    model_name='published',
                    name='publish_log_record',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.publishlogrecord'),
                ),
                migrations.AddField(
                    model_name='published',
                    name='version',
                    field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.RESTRICT, to='openedx_content.publishableentityversion'),
                ),
                migrations.AddConstraint(
                    model_name='collectionpublishableentity',
                    constraint=models.UniqueConstraint(fields=('collection', 'entity'), name='oel_collections_cpe_uniq_col_ent'),
                ),
                migrations.AddField(
                    model_name='componentversioncontent',
                    name='component_version',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='openedx_content.componentversion'),
                ),
                migrations.AddField(
                    model_name='componentversion',
                    name='component',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='openedx_content.component'),
                ),
                migrations.AddField(
                    model_name='componentversion',
                    name='contents',
                    field=models.ManyToManyField(related_name='component_versions', through='openedx_content.ComponentVersionContent', to='openedx_content.content'),
                ),
                migrations.CreateModel(
                    name='SectionVersion',
                    fields=[
                        ('container_version', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='openedx_content.containerversion')),
                    ],
                    options={
                        'abstract': False,
                        'db_table': 'oel_sections_sectionversion',
                    },
                    bases=('openedx_content.containerversion',),
                ),
                migrations.CreateModel(
                    name='SubsectionVersion',
                    fields=[
                        ('container_version', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='openedx_content.containerversion')),
                    ],
                    options={
                        'abstract': False,
                        'db_table': 'oel_subsections_subsectionversion',
                    },
                    bases=('openedx_content.containerversion',),
                ),
                migrations.CreateModel(
                    name='UnitVersion',
                    fields=[
                        ('container_version', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='openedx_content.containerversion')),
                    ],
                    options={
                        'abstract': False,
                        'db_table': 'oel_units_unitversion',
                    },
                    bases=('openedx_content.containerversion',),
                ),
                migrations.AddField(
                    model_name='containerversion',
                    name='container',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='openedx_content.container'),
                ),
                migrations.AddField(
                    model_name='containerversion',
                    name='entity_list',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='container_versions', to='openedx_content.entitylist'),
                ),
                migrations.AddIndex(
                    model_name='draftchangelogrecord',
                    index=models.Index(fields=['entity', '-draft_change_log'], name='oel_dlr_idx_entity_rdcl'),
                ),
                migrations.AddConstraint(
                    model_name='draftchangelogrecord',
                    constraint=models.UniqueConstraint(fields=('draft_change_log', 'entity'), name='oel_dlr_uniq_dcl'),
                ),
                migrations.AddConstraint(
                    model_name='draftsideeffect',
                    constraint=models.UniqueConstraint(fields=('cause', 'effect'), name='oel_pub_dse_uniq_c_e'),
                ),
                migrations.AddConstraint(
                    model_name='entitylistrow',
                    constraint=models.UniqueConstraint(fields=('entity_list', 'order_num'), name='oel_publishing_elist_row_order'),
                ),
                migrations.AddIndex(
                    model_name='publishableentity',
                    index=models.Index(fields=['key'], name='oel_pub_ent_idx_key'),
                ),
                migrations.AddIndex(
                    model_name='publishableentity',
                    index=models.Index(fields=['learning_package', '-created'], name='oel_pub_ent_idx_lp_rcreated'),
                ),
                migrations.AddConstraint(
                    model_name='publishableentity',
                    constraint=models.UniqueConstraint(fields=('learning_package', 'key'), name='oel_pub_ent_uniq_lp_key'),
                ),
                migrations.AddIndex(
                    model_name='collection',
                    index=models.Index(fields=['learning_package', 'title'], name='oel_collect_learnin_dfaf89_idx'),
                ),
                migrations.AddConstraint(
                    model_name='collection',
                    constraint=models.UniqueConstraint(fields=('learning_package', 'key'), name='oel_coll_uniq_lp_key'),
                ),
                migrations.AddIndex(
                    model_name='content',
                    index=models.Index(fields=['learning_package', '-size'], name='oel_content_idx_lp_rsize'),
                ),
                migrations.AddConstraint(
                    model_name='content',
                    constraint=models.UniqueConstraint(fields=('learning_package', 'media_type', 'hash_digest'), name='oel_content_uniq_lc_media_type_hash_digest'),
                ),
                migrations.AddConstraint(
                    model_name='publishableentityversiondependency',
                    constraint=models.UniqueConstraint(fields=('referring_version', 'referenced_entity'), name='oel_pevd_uniq_rv_re'),
                ),
                migrations.AddIndex(
                    model_name='publishableentityversion',
                    index=models.Index(fields=['entity', '-created'], name='oel_pv_idx_entity_rcreated'),
                ),
                migrations.AddIndex(
                    model_name='publishableentityversion',
                    index=models.Index(fields=['title'], name='oel_pv_idx_title'),
                ),
                migrations.AddConstraint(
                    model_name='publishableentityversion',
                    constraint=models.UniqueConstraint(fields=('entity', 'version_num'), name='oel_pv_uniq_entity_version_num'),
                ),
                migrations.AddIndex(
                    model_name='publishlogrecord',
                    index=models.Index(fields=['entity', '-publish_log'], name='oel_plr_idx_entity_rplr'),
                ),
                migrations.AddConstraint(
                    model_name='publishlogrecord',
                    constraint=models.UniqueConstraint(fields=('publish_log', 'entity'), name='oel_plr_uniq_pl_publishable'),
                ),
                migrations.AddConstraint(
                    model_name='publishsideeffect',
                    constraint=models.UniqueConstraint(fields=('cause', 'effect'), name='oel_pub_pse_uniq_c_e'),
                ),
                migrations.AddIndex(
                    model_name='component',
                    index=models.Index(fields=['component_type', 'local_key'], name='oel_component_idx_ct_lk'),
                ),
                migrations.AddConstraint(
                    model_name='component',
                    constraint=models.UniqueConstraint(fields=('learning_package', 'component_type', 'local_key'), name='oel_component_uniq_lc_ct_lk'),
                ),
                migrations.AddIndex(
                    model_name='componentversioncontent',
                    index=models.Index(fields=['content', 'component_version'], name='oel_cvcontent_c_cv'),
                ),
                migrations.AddIndex(
                    model_name='componentversioncontent',
                    index=models.Index(fields=['component_version', 'content'], name='oel_cvcontent_cv_d'),
                ),
                migrations.AddConstraint(
                    model_name='componentversioncontent',
                    constraint=models.UniqueConstraint(fields=('component_version', 'key'), name='oel_cvcontent_uniq_cv_key'),
                ),
            ]
        )
    ]
