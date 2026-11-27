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
import re
import uuid
from typing import Any

from django.core.validators import RegexValidator
from django.db import models
from django.db.models.lookups import Regex
from django.utils.translation import gettext_lazy as _

from .collations import MultiCollationMixin
# Re-export these fields which are in a separate file so we can use .pyi type stubs:
from .id_fields import TypedAutoField, TypedBigAutoField  # pylint: disable=unused-import
from .validators import INVALID_PATH_MSG, PATH_MAX_LENGTH, PATH_RE, validate_path_value, validate_utc_datetime


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


# Alphanumeric, hyphens, underscores, periods
CODE_REGEX_ASCII = re.compile(r"^[a-zA-Z0-9_.-]+\Z")

# Anything which passes isalnum(), plus underscores, hyphens, and periods
CODE_REGEX_UNICODE = re.compile(r"^[\w.-]+\Z", flags=re.UNICODE)

_CODE_VIOLATION_MSG_ASCII = _(
    'Enter a valid "code name" consisting of latin letters (A-Z, a-z), numbers, underscores, hyphens, or periods.'
)

_CODE_VIOLATION_MSG_UNICODE = _(
    'Enter a valid "code name" consisting of any letters, numbers, underscores, hyphens, or periods.'
)


def code_field(unicode: bool, **kwargs) -> MultiCollationCharField:
    """
    Field to hold a 'code', i.e. a slug-like local identifier.

    Use together with :func:`code_field_check` to enforce the same regex at
    the database level via a ``CheckConstraint``.
    """
    return case_sensitive_char_field(
        max_length=255,
        blank=False,
        validators=[
            RegexValidator(
                CODE_REGEX_UNICODE if unicode else CODE_REGEX_ASCII,
                _CODE_VIOLATION_MSG_UNICODE if unicode else _CODE_VIOLATION_MSG_ASCII,
                "invalid",
            ),
        ],
        **kwargs,
    )


def code_field_check(field_name: str, *, name: str, unicode: bool) -> models.CheckConstraint:
    """
    Return a ``CheckConstraint`` that enforces :data:`CODE_REGEX_UNICODE` or :data:`CODE_REGEX_ASCII` at the DB level.

    Django validators (used by :func:`code_field`) are not called on ``.save()``
    or ``.update()``.  Adding this constraint ensures the regex is also enforced
    by the database itself, and Django will additionally run it as a Python-level
    validator automatically.

    Usage::

        class Meta:
            constraints = [
                code_field_check(
                    "my_code_field",
                    name="myapp_mymodel_my_code_field_regex",
                    unicode=True/False,  # Make sure this matches the code_field!
                ),
            ]
    """
    return models.CheckConstraint(
        condition=Regex(
            models.F(field_name),
            (CODE_REGEX_UNICODE if unicode else CODE_REGEX_ASCII).pattern,
        ),
        name=name,
        violation_error_message=(_CODE_VIOLATION_MSG_UNICODE if unicode else _CODE_VIOLATION_MSG_ASCII),
    )


def path_field(**kwargs) -> MultiCollationCharField:
    """
    Field to hold a path/filename.

    You should always call :func:`normalize_unicode` before saving a path value.

    Use together with :func:`path_field_checks` to enforce path value
    requirements at the database level via a ``CheckConstraint``.
    """
    kwargs.setdefault("blank", False)
    return case_sensitive_char_field(
        max_length=PATH_MAX_LENGTH,
        validators=[validate_path_value],
        **kwargs,
    )


def path_field_checks(field_name: str, *, name: str) -> list[models.CheckConstraint]:
    """
    Return a ``CheckConstraint`` that enforces validation of a path/filename
    field.

    Django validators (used by :func:`code_field`) are not called on ``.save()``
    or ``.update()``.  Adding this constraint ensures the regex is also enforced
    by the database itself, and Django will additionally run it as a Python-level
    validator automatically.

    Usage::

        class Meta:
            constraints = [
                *path_field_checks(
                    "my_path_field",
                    name="myapp_mymodel_my_path_field_regex",
                ),
            ]
    """
    return [
        models.CheckConstraint(
            condition=Regex(models.F(field_name), PATH_RE.pattern),
            name=name,
            violation_error_message=INVALID_PATH_MSG,
        ),
        # Make SQLite enforce a maximum path length like MySQL does
        models.CheckConstraint(
            condition=models.Q(**{f"{field_name}__length__lte": PATH_MAX_LENGTH}), name=f"{name}_maxlength"
        ),
    ]


def ref_field(**kwargs) -> MultiCollationCharField:
    """
    Opaque reference string fields.

    These hold externally-created identifiers that are local to a particular
    scope, like within a LearningPackage. Consumers must treat the value as
    an atomic string and must never parse or reconstruct it.
    """
    return case_sensitive_char_field(max_length=500, blank=False, **kwargs)


def hash_field(**kwargs: Any) -> models.CharField:
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
    default_kwargs: dict[str, Any] = {
        "max_length": 40,
        "blank": False,
        "null": False,
        "editable": False,
    }
    merged: dict[str, Any] = {**default_kwargs, **kwargs}
    return models.CharField(**merged)


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
