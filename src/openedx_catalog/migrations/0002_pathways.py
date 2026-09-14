"""
Create the catalog half of a Pathway: PathwayCategory, CatalogPathway, and PathwayEnrollment.

Every CatalogPathway must have a category. Rather than falling back to the word "Pathway" in code, we ship a database
row with that name, so that the behavior is uniform and operators can rename it or add categories of their own without a
code change (ADR 0007, decision 2). The default row is created right after its table and before CatalogPathway exists.
Because Django unapplies operations in reverse order, the reverse step deletes that row only after the CatalogPathway
table is already gone, so nothing can still reference it.
"""

import re

import django.core.validators
import django.db.models.deletion
import django.db.models.functions.text
import django.db.models.lookups
from django.conf import settings
from django.db import migrations, models

import openedx_catalog.models.pathway_category
import openedx_django_lib.fields
import openedx_django_lib.validators

# These values are duplicated from openedx_catalog.models.pathway_category rather than imported, because a migration
# should represent a point-in-time transformation and must not change if those constants later do.
DEFAULT_CATEGORY_CODE = "pathway"
DEFAULT_CATEGORY_NAME = "Pathway"


def create_default_pathway_category(apps, schema_editor):
    """Create the default category."""
    PathwayCategory = apps.get_model("openedx_catalog", "PathwayCategory")
    PathwayCategory.objects.get_or_create(
        category_code=DEFAULT_CATEGORY_CODE,
        defaults={"name": DEFAULT_CATEGORY_NAME},
    )


def delete_default_pathway_category(apps, schema_editor):
    """Remove the default category on reverse."""
    PathwayCategory = apps.get_model("openedx_catalog", "PathwayCategory")
    PathwayCategory.objects.filter(category_code=DEFAULT_CATEGORY_CODE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("openedx_catalog", "0001_initial"),
        ("openedx_content", "0014_typed_media_id"),
        ("organizations", "0004_auto_20230727_2054"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PathwayCategory",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        editable=False,
                        help_text="The internal database ID for this pathway category. Should not be exposed to users nor in APIs.",
                        primary_key=True,
                        serialize=False,
                        verbose_name="Primary Key",
                    ),
                ),
                (
                    "category_code",
                    openedx_django_lib.fields.MultiCollationCharField(
                        db_collations={"mysql": "utf8mb4_bin", "sqlite": "BINARY"},
                        help_text='A stable slug identifying this category, e.g. "masters-degree". Not shown to learners.',
                        max_length=255,
                        validators=[
                            django.core.validators.RegexValidator(
                                re.compile("^[a-zA-Z0-9_.-]+\\Z"),
                                'Enter a valid "code name" consisting of latin letters (A-Z, a-z), numbers, underscores, hyphens, or periods.',
                                "invalid",
                            )
                        ],
                    ),
                ),
                (
                    "name",
                    openedx_django_lib.fields.MultiCollationCharField(
                        db_collations={"mysql": "utf8mb4_unicode_ci", "sqlite": "NOCASE"},
                        help_text='The learner-facing name of this category, e.g. "Master\'s Degree". Operators may change this.',
                        max_length=255,
                    ),
                ),
            ],
            options={
                "verbose_name": "Pathway Category",
                "verbose_name_plural": "Pathway Categories",
                "ordering": ("name",),
                "constraints": [
                    models.UniqueConstraint(
                        django.db.models.functions.text.Lower("category_code"),
                        name="oex_catalog_pathwaycategory_code_uniq_ci",
                    ),
                    models.CheckConstraint(
                        condition=django.db.models.lookups.Regex(models.F("category_code"), "^[a-zA-Z0-9_.-]+\\Z"),
                        name="oex_catalog_pathwaycategory_code_regex",
                        violation_error_message='Enter a valid "code name" consisting of latin letters (A-Z, a-z), numbers, underscores, hyphens, or periods.',
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("name__length__gt", 0)), name="oex_catalog_pathwaycategory_name_not_blank"
                    ),
                ],
            },
        ),
        migrations.RunPython(
            create_default_pathway_category,
            reverse_code=delete_default_pathway_category,
        ),
        migrations.CreateModel(
            name="CatalogPathway",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        editable=False,
                        help_text="The internal database ID for this catalog pathway. Should not be exposed to users nor in APIs.",
                        primary_key=True,
                        serialize=False,
                        verbose_name="Primary Key",
                    ),
                ),
                (
                    "pathway_code",
                    openedx_django_lib.fields.MultiCollationCharField(
                        db_collations={"mysql": "utf8mb4_bin", "sqlite": "BINARY"},
                        help_text='The pathway code/number, e.g. "DataScience2026".',
                        max_length=255,
                        validators=[
                            django.core.validators.RegexValidator(
                                re.compile("^[a-zA-Z0-9_.-]+\\Z"),
                                'Enter a valid "code name" consisting of latin letters (A-Z, a-z), numbers, underscores, hyphens, or periods.',
                                "invalid",
                            )
                        ],
                    ),
                ),
                (
                    "created",
                    models.DateTimeField(
                        auto_now_add=True, validators=[openedx_django_lib.validators.validate_utc_datetime]
                    ),
                ),
                (
                    "modified",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="When the catalog fields of this pathway were last edited. Unrelated to its content.",
                        validators=[openedx_django_lib.validators.validate_utc_datetime],
                    ),
                ),
                (
                    "title",
                    openedx_django_lib.fields.MultiCollationCharField(
                        blank=True,
                        db_collations={"mysql": "utf8mb4_unicode_ci", "sqlite": "NOCASE"},
                        help_text='The full title (display name) of this pathway, e.g. "Data Science Professional Certificate". Leave blank to use the pathway code as the title.',
                        max_length=255,
                    ),
                ),
                (
                    "description",
                    openedx_django_lib.fields.MultiCollationTextField(
                        blank=True,
                        db_collations={"mysql": "utf8mb4_unicode_ci", "sqlite": "NOCASE"},
                        default="",
                        help_text="The description shown to learners browsing the catalog.",
                        max_length=10000,
                    ),
                ),
                (
                    "org",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="catalog_pathways",
                        to="organizations.organization",
                    ),
                ),
                (
                    "category",
                    models.ForeignKey(
                        default=openedx_catalog.models.pathway_category.get_default_pathway_category_id,
                        help_text="The learner-facing kind of pathway this is. Always required; defaults to a category we ship.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pathways",
                        to="openedx_catalog.pathwaycategory",
                    ),
                ),
                (
                    "content_entity",
                    models.OneToOneField(
                        blank=True,
                        help_text="The publishable entity holding this pathway's versioned definition (a Pathway in openedx_learning). Blank until a definition has been created and linked through openedx_learning.api.",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="catalog_pathway",
                        to="openedx_content.publishableentity",
                    ),
                ),
            ],
            options={
                "verbose_name": "Catalog Pathway",
                "verbose_name_plural": "Catalog Pathways",
                "ordering": ("-created",),
            },
        ),
        migrations.CreateModel(
            name="PathwayEnrollment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        editable=False,
                        help_text="The internal database ID for this enrollment. Should not be exposed to users nor in APIs.",
                        primary_key=True,
                        serialize=False,
                        verbose_name="Primary Key",
                    ),
                ),
                (
                    "created",
                    models.DateTimeField(
                        auto_now_add=True, validators=[openedx_django_lib.validators.validate_utc_datetime]
                    ),
                ),
                (
                    "modified",
                    models.DateTimeField(auto_now=True, validators=[openedx_django_lib.validators.validate_utc_datetime]),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="False once the learner has unenrolled. The row is kept so re-enrolling reuses it.",
                    ),
                ),
                (
                    "catalog_pathway",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="enrollments",
                        to="openedx_catalog.catalogpathway",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pathway_enrollments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Pathway Enrollment",
                "verbose_name_plural": "Pathway Enrollments",
                "ordering": ("-created",),
            },
        ),
        migrations.AddIndex(
            model_name="catalogpathway",
            index=models.Index(fields=["org", "pathway_code"], name="openedx_cat_org_id_037ff8_idx"),
        ),
        migrations.AddConstraint(
            model_name="catalogpathway",
            constraint=models.UniqueConstraint(
                models.F("org"),
                django.db.models.functions.text.Lower("pathway_code"),
                name="oex_catalog_catalogpathway_org_code_uniq_ci",
            ),
        ),
        migrations.AddConstraint(
            model_name="catalogpathway",
            constraint=models.CheckConstraint(
                condition=django.db.models.lookups.Regex(models.F("pathway_code"), "^[a-zA-Z0-9_.-]+\\Z"),
                name="oex_catalog_catalogpathway_pathway_code_regex",
                violation_error_message='Enter a valid "code name" consisting of latin letters (A-Z, a-z), numbers, underscores, hyphens, or periods.',
            ),
        ),
        migrations.AddConstraint(
            model_name="catalogpathway",
            constraint=models.CheckConstraint(
                condition=models.Q(("title__length__gt", 0)), name="oex_catalog_catalogpathway_title_not_blank"
            ),
        ),
        migrations.AddConstraint(
            model_name="pathwayenrollment",
            constraint=models.UniqueConstraint(
                fields=("user", "catalog_pathway"), name="oex_catalog_pathwayenrollment_uniq_user_pathway"
            ),
        ),
    ]
