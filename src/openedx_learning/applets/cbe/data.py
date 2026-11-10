"""
Value objects passed across the CBE applet's public API boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, kw_only=True, slots=True)
class GradedObjectScore:
    """
    One learner's final, override-adjusted fraction earned on one graded object.

    ``fraction`` is the caller's final answer for how much of ``object_id`` this learner earned;
    this library cannot detect whether an override was already folded into it. Leave an object
    worth zero possible points out of the list entirely rather than passing it with a
    ``Decimal("0")`` fraction, since it has no meaningful fraction to evaluate. ``Decimal("0")``
    itself is a valid, meaningful input: it means the learner attempted the object and earned
    nothing on it.

    ``fraction`` must be built from a string or from ``Decimal`` arithmetic, never from a
    ``float``: ``Decimal(0.7)`` is a ``Decimal`` instance, so it passes this class's own type
    check, but its value is ``0.69999999999999995559...``, not ``0.7``.
    """

    object_id: str
    fraction: Decimal

    def __post_init__(self) -> None:
        """Enforce this class's type and range contract once, at construction time."""
        if not isinstance(self.fraction, Decimal):
            raise TypeError(f"fraction must be a Decimal, not {type(self.fraction)!r}")
        # is_finite() must run first: 0 <= Decimal("NaN") raises decimal.InvalidOperation, not
        # ValueError, so checking finiteness first is what makes every invalid input, including
        # NaN and Infinity, actually raise the documented ValueError.
        if not self.fraction.is_finite() or not 0 <= self.fraction <= 1:
            raise ValueError(f"fraction must be finite and between 0 and 1 inclusive, not {self.fraction!r}")
