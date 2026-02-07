"""
Useful validation methods
"""
from datetime import datetime, timezone

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_utc_datetime(dt: datetime) -> None:
    if dt.tzinfo != timezone.utc:
        raise ValidationError(
            _("The timezone for %(datetime)s is not UTC."),
            params={"datetime": dt},
        )
