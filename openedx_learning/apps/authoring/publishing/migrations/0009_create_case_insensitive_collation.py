# Generated manually for PostgreSQL collation support
from django.contrib.postgres.operations import CreateCollation
from django.db import migrations


class Migration(migrations.Migration):
    run_before = [
        ("oel_publishing", "0001_initial"),
    ]
    operations = [
        # Create a custom case-insensitive collation for PostgreSQL.
        # This collation is used by case_insensitive_char_field() to provide
        # case-insensitive comparisons and unique constraints on PostgreSQL,
        # matching the behavior of MySQL's utf8mb4_unicode_ci collation.
        #
        # Note: CreateCollation is a PostgreSQL-specific operation from
        # django.contrib.postgres.operations. Django automatically skips
        # PostgreSQL-specific operations when running migrations on other
        # database backends (MySQL, SQLite, etc.). The operation checks
        # schema_editor.connection.vendor and only executes when vendor=='postgresql'.
        #
        # Requirements:
        # - PostgreSQL 12+ (for non-deterministic collations)
        # - PostgreSQL compiled with ICU support (standard in most distributions)
        #
        # This works regardless of the database's locale_provider setting
        # (whether it's 'libc', 'icu', or 'c').
        CreateCollation(
            "case_insensitive",
            provider="icu",
            locale="und-u-ks-level2",
            deterministic=False,
        ),
    ]
