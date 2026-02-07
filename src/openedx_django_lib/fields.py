"""
Convenience functions to make consistent field conventions easier.

Per OEP-38, we're using the MySQL-friendly convention of BigInt ID as a
primary key + separate UUID column.
https://open-edx-proposals.readthedocs.io/en/latest/best-practices/oep-0038-Data-Modeling.html

We have helpers to make case sensitivity consistent across backends. MySQL is
case-insensitive by default, SQLite and Postgres are case-sensitive.
"""
from __future__ import annotations

import hashlib
import uuid

from django.db import models

from .collations import MultiCollationMixin
from .validators import validate_utc_datetime


def create_hash_digest(data_bytes: bytes, num_bytes=20) -> str:
    """
    Create a lower-case hex string representation of a hash digest.

    The hash itself is 20-bytes by default, so 40 characters when we return it
    as a hex-encoded string. We use BLAKE2b for the hashing algorithm.

    DON'T JUST MODIFY THIS HASH BEHAVIOR!!! We use hashing for de-duplication
    purposes. If this hash function ever changes, that deduplication will fail
    because the hashing behavior won't match what's already in the database.

    If we want to change this representation one day, we should create a new
    function for that and do the appropriate data migration.
    """
    return hashlib.blake2b(data_bytes, digest_size=num_bytes).hexdigest()


def case_insensitive_char_field(**kwargs) -> MultiCollationCharField:
    """
    Return a case-insensitive ``MultiCollationCharField``.

    This means that entries will sort in a case-insensitive manner, and that
    unique indexes will be case insensitive, e.g. you would not be able to
    insert "abc" and "ABC" into the same table field if you put a unique index
    on this field.

    You may override any argument that you would normally pass into
    ``MultiCollationCharField`` (which is itself a subclass of ``CharField``).
    """
    # Set our default arguments
    final_kwargs = {
        "null": False,
        "db_collations": {
            "sqlite": "NOCASE",
            # We're using utf8mb4_unicode_ci to keep MariaDB compatibility,
            # since their collation support diverges after this. MySQL is now on
            # utf8mb4_0900_ai_ci based on Unicode 9, while MariaDB has
            # uca1400_ai_ci based on Unicode 14.
            "mysql": "utf8mb4_unicode_ci",
        },
    }
    # Override our defaults with whatever is passed in.
    final_kwargs.update(kwargs)

    return MultiCollationCharField(**final_kwargs)


def case_sensitive_char_field(**kwargs) -> MultiCollationCharField:
    """
    Return a case-sensitive ``MultiCollationCharField``.

    This means that entries will sort in a case-sensitive manner, and that
    unique indexes will be case sensitive, e.g. "abc" and "ABC" would be
    distinct and you would not get a unique constraint violation by adding them
    both to the same table field.

    You may override any argument that you would normally pass into
    ``MultiCollationCharField`` (which is itself a subclass of ``CharField``).
    """
    # Set our default arguments
    final_kwargs = {
        "null": False,
        "db_collations": {
            "sqlite": "BINARY",
            "mysql": "utf8mb4_bin",
        },
    }
    # Override our defaults with whatever is passed in.
    final_kwargs.update(kwargs)

    return MultiCollationCharField(**final_kwargs)


def immutable_uuid_field() -> models.UUIDField:
    """
    Stable, randomly-generated UUIDs.

    These can be used as stable identifiers by other services that do not share
    a database, but you should prefer to make a ForeignKey to the primary (id)
    key of the model if you're in the same process.
    """
    return models.UUIDField(
        default=uuid.uuid4,
        blank=False,
        null=False,
        editable=False,
        unique=True,
        verbose_name="UUID",  # Just makes the Django admin output properly capitalized
    )


def key_field(**kwargs) -> MultiCollationCharField:
    """
    Externally created Identifier fields.

    These will often be local to a particular scope, like within a
    LearningPackage. It's up to the application as to whether they're
    semantically meaningful or look more machine-generated.

    Other apps should *not* make references to these values directly, since
    these values may in theory change (even if this is rare in practice).
    """
    return case_sensitive_char_field(max_length=500, blank=False, **kwargs)


def hash_field(**kwargs) -> models.CharField:
    """
    Holds a hash digest meant to identify a piece of content.

    Do not assume that this is secure or globally unique. Accidental collisions
    are extremely unlikely, but we don't want to get into a place where someone
    can maliciously craft a collision and affect other users.

    Use the create_hash_digest function to generate data suitable for this
    field.

    There are a couple of ways that we could have stored this more efficiently,
    but we don't at this time:

    1. A BinaryField would be the most space efficient, but Django doesn't
       support indexing a BinaryField in a MySQL database.
    2. We could make the field case-sensitive and run it through a URL-safe
       base64 encoding. But the amount of space this saves vs. the complexity
       didn't seem worthwhile, particularly the possibility of case-sensitivity
       related bugs.
    """
    default_kwargs = {
        "max_length": 40,
        "blank": False,
        "null": False,
        "editable": False,
    }
    return models.CharField(**(default_kwargs | kwargs))


def manual_date_time_field() -> models.DateTimeField:
    """
    DateTimeField that does not auto-generate values.

    The datetimes entered for this field *must be UTC* or it will raise a
    ValidationError.

    The reason for this convention is that we are often creating many rows of
    data in the same transaction. They are semantically being created or
    modified "at the same time", even if each individual row is milliseconds
    apart. This convention forces the caller to set a datetime up front and pass
    it in manually, so all the affected rows have the exact same time. This
    makes it easier to see which rows were changed at the same time more easily.

    When using these fields, the other conventions from OEP-38 still hold:

    * common field names: created, modified
    * Django's USE_TZ setting should be True
    * Times should be in UTC as a general rule
    """
    return models.DateTimeField(
        auto_now=False,
        auto_now_add=False,
        null=False,
        validators=[
            validate_utc_datetime,
        ],
    )


class MultiCollationCharField(MultiCollationMixin, models.CharField):
    """
    CharField subclass with per-database-vendor collation settings.

    Django's CharField already supports specifying the database collation, but
    that only works with a single value. So there would be no way to say, "Use
    utf8mb4_bin for MySQL, and BINARY if we're running SQLite." This is a
    problem because we run tests in SQLite (and may potentially run more later).
    It's also a problem if we ever want to support other database backends, like
    PostgreSQL. Even MariaDB is starting to diverge from MySQL in terms of what
    collations are supported.
    """


class MultiCollationTextField(MultiCollationMixin, models.TextField):
    """
    TextField subclass with per-database-vendor collation settings.

    We don't ever really want to _sort_ by a TextField, but setting a collation
    forces the compatible charset to be set in MySQL, and that's the part that
    matters for our purposes.
    """
