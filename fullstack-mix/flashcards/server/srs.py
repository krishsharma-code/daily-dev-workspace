"""SM-2 style spaced-repetition scheduling.

This module is pure and dependency-free: it contains no I/O and no knowledge of
SQLite or HTTP, so the scheduling maths can be reasoned about and tested in
isolation. The algorithm is a small, well-documented variant of SuperMemo 2.

A :class:`CardState` holds everything the scheduler needs about one card: the
ease factor, the current interval, how many times it has been reviewed in a row
and how many times it has lapsed. :func:`sm2_update` takes that state plus a
review quality in ``0..5`` and returns the *next* state, including the new due
date computed from an explicit ``now`` (injected rather than read from the
clock, so callers and tests stay deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

__all__ = [
    "CardState",
    "ValidationError",
    "MIN_EASE",
    "DEFAULT_EASE",
    "MIN_QUALITY",
    "MAX_QUALITY",
    "sm2_update",
    "is_due",
]

#: Ease factors are clamped to this floor so an interval never stops growing
#: (a card that keeps failing still eventually graduates).
MIN_EASE = 1.3

#: Ease factor a brand-new card starts with.
DEFAULT_EASE = 2.5

MIN_QUALITY = 0
MAX_QUALITY = 5

#: Interval (in days) applied after the first successful review.
_FIRST_INTERVAL_DAYS = 1
#: Interval (in days) applied after the second successful review.
_SECOND_INTERVAL_DAYS = 6
#: Interval (in days) applied when a card lapses (quality < 3).
_LAPSE_INTERVAL_DAYS = 1
#: Ease is reduced by this much on a lapse.
_LAPSE_EASE_PENALTY = 0.2


class ValidationError(ValueError):
    """Raised for an invalid review quality or malformed card state."""


@dataclass(frozen=True)
class CardState:
    """The scheduling state of a single card.

    ``due`` is timezone-aware: it is always compared and produced in UTC.
    """

    ease: float = DEFAULT_EASE
    interval_days: int = 0
    repetitions: int = 0
    lapses: int = 0
    due: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ease, (int, float)) or isinstance(self.ease, bool):
            raise ValidationError("ease must be a number")
        if self.ease < MIN_EASE:
            raise ValidationError(f"ease must be >= {MIN_EASE}")
        if not isinstance(self.interval_days, int) or isinstance(self.interval_days, bool):
            raise ValidationError("interval_days must be an integer")
        if self.interval_days < 0:
            raise ValidationError("interval_days must be >= 0")
        if not isinstance(self.repetitions, int) or isinstance(self.repetitions, bool):
            raise ValidationError("repetitions must be an integer")
        if self.repetitions < 0:
            raise ValidationError("repetitions must be >= 0")
        if not isinstance(self.lapses, int) or isinstance(self.lapses, bool):
            raise ValidationError("lapses must be an integer")
        if self.lapses < 0:
            raise ValidationError("lapses must be >= 0")
        if self.due is not None and not isinstance(self.due, datetime):
            raise ValidationError("due must be a datetime or None")

    def to_dict(self) -> dict:
        return {
            "ease": round(self.ease, 2),
            "interval_days": self.interval_days,
            "repetitions": self.repetitions,
            "lapses": self.lapses,
            "due": self.due.isoformat() if self.due else None,
        }


def _validate_quality(quality: int) -> int:
    if isinstance(quality, bool) or not isinstance(quality, int):
        raise ValidationError("quality must be an integer")
    if not MIN_QUALITY <= quality <= MAX_QUALITY:
        raise ValidationError(f"quality must be between {MIN_QUALITY} and {MAX_QUALITY}")
    return quality


def _validate_now(now: datetime) -> datetime:
    if not isinstance(now, datetime):
        raise ValidationError("now must be a datetime")
    if now.tzinfo is None:
        raise ValidationError("now must be timezone-aware")
    return now


def sm2_update(state: CardState, quality: int, now: datetime) -> CardState:
    """Return the next :class:`CardState` after reviewing at ``quality``.

    ``quality`` follows the SuperMemo scale: ``0`` is a total blackout and
    ``5`` is a perfect recall. Anything below ``3`` counts as a lapse: the
    repetition counter resets and the ease is reduced. ``now`` must be
    timezone-aware; the returned state's ``due`` is ``now + interval``.
    """
    _validate_quality(quality)
    _validate_now(now)

    if quality < 3:
        # A lapse: forget the streak, apply a short relearn interval and dock
        # the ease a little (but never below the floor).
        new_ease = max(MIN_EASE, round(state.ease - _LAPSE_EASE_PENALTY, 4))
        interval = _LAPSE_INTERVAL_DAYS
        repetitions = 0
        lapses = state.lapses + 1
    else:
        if state.repetitions == 0:
            interval = _FIRST_INTERVAL_DAYS
        elif state.repetitions == 1:
            interval = _SECOND_INTERVAL_DAYS
        else:
            # Grow the interval by the *current* ease, then adjust the ease.
            interval = max(1, int(round(state.interval_days * state.ease)))
        repetitions = state.repetitions + 1
        lapses = state.lapses
        # Standard SM-2 ease update; quality 4 is neutral, 5 raises the ease,
        # 3 lowers it.
        delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
        new_ease = max(MIN_EASE, round(state.ease + delta, 4))

    return replace(
        state,
        ease=new_ease,
        interval_days=interval,
        repetitions=repetitions,
        lapses=lapses,
        due=now + timedelta(days=interval),
    )


def is_due(state: CardState, now: datetime) -> bool:
    """Return ``True`` when the card is due for review at ``now``."""
    _validate_now(now)
    if state.due is None:
        return True
    return state.due <= now
