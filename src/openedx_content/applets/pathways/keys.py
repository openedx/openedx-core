"""
Opaque key for Pathways.

Format: path-v1:{org}+{path_id}

Can be moved to opaque-keys later if needed.
"""

import re
from typing import Self

from django.core.exceptions import ValidationError
from opaque_keys import InvalidKeyError, OpaqueKey
from opaque_keys.edx.django.models import LearningContextKeyField
from opaque_keys.edx.keys import LearningContextKey

PATHWAY_NAMESPACE = "path-v1"
PATHWAY_PATTERN = r"([^+]+)\+([^+]+)"
PATHWAY_URL_PATTERN = rf"(?P<pathway_key_str>{PATHWAY_NAMESPACE}:{PATHWAY_PATTERN})"


class PathwayKey(LearningContextKey):
    """
    Key for identifying a Pathway.

    Format: path-v1:{org}+{path_id}
    Example: path-v1:OpenedX+DemoPathway
    """

    CANONICAL_NAMESPACE = PATHWAY_NAMESPACE
    KEY_FIELDS = ("org", "path_id")
    CHECKED_INIT = False

    __slots__ = KEY_FIELDS
    _pathway_key_regex = re.compile(PATHWAY_PATTERN)

    def __init__(self, org: str, path_id: str):
        super().__init__(org=org, path_id=path_id)

    @classmethod
    def _from_string(cls, serialized: str) -> Self:
        """Return an instance of this class constructed from the given string."""
        match = cls._pathway_key_regex.fullmatch(serialized)
        if not match:
            raise InvalidKeyError(cls, serialized)
        return cls(*match.groups())

    def _to_string(self) -> str:
        """Return a string representing this key."""
        return f"{self.org}+{self.path_id}"  # type: ignore[attr-defined]


class PathwayKeyField(LearningContextKeyField):
    """Django model field for PathwayKey."""

    description = "A PathwayKey object"
    KEY_CLASS = PathwayKey
    # Declare the field types for the django-stubs mypy type hint plugin:
    _pyi_private_set_type: PathwayKey | str | None
    _pyi_private_get_type: PathwayKey | None

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 255)
        super().__init__(*args, **kwargs)

    def to_python(self, value) -> None | OpaqueKey:
        """Convert the input value to a PathwayKey object."""
        try:
            return super().to_python(value)
        except InvalidKeyError:
            # pylint: disable=raise-missing-from
            raise ValidationError("Invalid format. Use: 'path-v1:{org}+{path_id}'")
