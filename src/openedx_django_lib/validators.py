"""
Useful validation methods
"""

import re
import unicodedata
from datetime import UTC, datetime

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_utc_datetime(dt: datetime) -> None:
    if dt.tzinfo != UTC:
        raise ValidationError(
            _("The timezone for %(datetime)s is not UTC."),
            params={"datetime": dt},
        )


def normalize_unicode(value: str) -> str:
    """
    Normalize unicode values in the provided string.

    >>> normalize_unicode("caf\u00e9")
    'café'
    >>> normalize_unicode("cafe\u0301")
    'café'
    >>> normalize_unicode("caf\u00e9") == normalize_unicode("cafe\u0301")
    True
    >>> "caf\u00e9" == "cafe\u0301"
    False
    """
    return unicodedata.normalize("NFC", value)


# Helper for PATH_RE:
_PATH_SEGMENT = r"[^/\\*\s](?:[^/\\*]*[^/\\*\s])?"

# Validation pattern for a filename/path field.
# Disallows starting slash, control chars, '//', '\', '*', './' and '../', and whitespace at the start or end of any
# path segment. This is also used in a DB CheckConstraint, so it must stay portable across Python, MySQL (ICU) and
# PostgreSQL (ARE) regex engines: no lookbehind, no \z, no \S/\W inside brackets.
# A single path segment: no slash, backslash or '*', and does not start or end with whitespace.
#
# On its own, this regex does NOT account for Unicode normalization, and there are a number of
# ugly things that we still allow, like `a?b.png`, `a#b.png`, `%2e%2e/b`, `a|b`, `image.`.
PATH_RE = re.compile(
    r"^"  # beginning of string
    r"(?![^\x00-\x1f\x7f]*[\x00-\x1f\x7f])"  # No control chars anywhere in the string
    r"(?!.*(?:^|/)\.{1,2}(?:/|$))"  # no "." or ".." path segments
    rf"{_PATH_SEGMENT}"  # first segment
    rf"(?:/{_PATH_SEGMENT})*"  # more segments, each after exactly one "/"
    r"$"  # end of string
)

INVALID_PATH_MSG = "Path/filename is invalid or contains invalid characters."

# Allows storage in char fields limited to 500 chars, with a prefix included,
# e.g. the entity_ref field is limited to 500 chars and is "openedx-asset:{path}"
PATH_MAX_LENGTH = 480


def is_valid_path_value(value: str) -> bool:
    """
    Check if a path/filename value is valid

    ✅ Valid: "image.png", "folder/example.pdf", "Αθήνα.md"

    ❌ Invalid: "/image.png", "../x", "*.png", "space_after.pdf "

    See the test case and the ``PATH_RE`` docstring for more exhaustive details.
    """
    # Normalize before checking length:
    if normalize_unicode(value) != value:
        return False
    if len(value) > PATH_MAX_LENGTH:
        return False
    return PATH_RE.fullmatch(value) is not None


def validate_path_value(value: str) -> None:
    """Validate a path/filename value, raising ValidationError if invalid."""
    if not is_valid_path_value(value):
        raise ValidationError(INVALID_PATH_MSG)
