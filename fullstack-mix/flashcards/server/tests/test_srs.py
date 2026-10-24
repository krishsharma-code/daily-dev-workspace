"""Tests for the SM-2 scheduler (``server/srs.py``).

The scheduler is pure, so every case is a direct call with an injected clock -
no fixtures beyond a fixed ``NOW``.
"""

from datetime import datetime, timedelta, timezone

import pytest

from srs import (
    DEFAULT_EASE,
    MIN_EASE,
    CardState,
    ValidationError,
    is_due,
    sm2_update,
)

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def fresh(**overrides) -> CardState:
    """A brand-new card, due at NOW."""
    base = dict(ease=DEFAULT_EASE, interval_days=0, repetitions=0, lapses=0, due=NOW)
    base.update(overrides)
    return CardState(**base)


class TestValidation:
    @pytest.mark.parametrize("bad", [-1, 6, 100])
    def test_quality_out_of_range(self, bad):
        with pytest.raises(ValidationError):
            sm2_update(fresh(), bad, NOW)

    @pytest.mark.parametrize("bad", [1.5, "5", None, True])
    def test_quality_must_be_int(self, bad):
        with pytest.raises(ValidationError):
            sm2_update(fresh(), bad, NOW)

    def test_naive_now_rejected(self):
        with pytest.raises(ValidationError):
            sm2_update(fresh(), 4, datetime(2026, 1, 1, 9, 0))

    def test_now_must_be_datetime(self):
        with pytest.raises(ValidationError):
            sm2_update(fresh(), 4, "2026-01-01")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"ease": 1.0},            # below the floor
            {"ease": "x"},
            {"interval_days": -1},
            {"repetitions": -1},
            {"lapses": -1},
            {"interval_days": 1.5},
            {"due": "not-a-date"},
        ],
    )
    def test_bad_state_rejected(self, kwargs):
        with pytest.raises(ValidationError):
            fresh(**kwargs)


class TestIntervals:
    def test_first_successful_review_is_one_day(self):
        nxt = sm2_update(fresh(), 5, NOW)
        assert nxt.interval_days == 1
        assert nxt.repetitions == 1

    def test_second_successful_review_is_six_days(self):
        first = sm2_update(fresh(), 5, NOW)
        second = sm2_update(first, 5, NOW)
        assert second.interval_days == 6
        assert second.repetitions == 2

    def test_third_review_grows_by_current_ease(self):
        s = fresh()
        for _ in range(2):
            s = sm2_update(s, 5, NOW)
        # interval was 6, ease is now 2.7 -> round(6 * 2.7) = 16
        third = sm2_update(s, 5, NOW)
        assert third.interval_days == 16

    def test_growth_sequence_is_deterministic(self):
        s = fresh()
        seen = []
        for _ in range(4):
            s = sm2_update(s, 5, NOW)
            seen.append(s.interval_days)
        assert seen == [1, 6, 16, 45]

    def test_due_is_now_plus_interval(self):
        nxt = sm2_update(fresh(), 5, NOW)
        assert nxt.due == NOW + DAY


class TestEase:
    def test_quality_five_raises_ease(self):
        nxt = sm2_update(fresh(), 5, NOW)
        assert nxt.ease == pytest.approx(2.6)

    def test_quality_four_keeps_ease(self):
        nxt = sm2_update(fresh(), 4, NOW)
        assert nxt.ease == pytest.approx(DEFAULT_EASE)

    def test_quality_three_lowers_ease(self):
        nxt = sm2_update(fresh(), 3, NOW)
        assert nxt.ease == pytest.approx(2.36)

    def test_ease_never_drops_below_floor(self):
        s = fresh(ease=MIN_EASE)
        for _ in range(10):
            s = sm2_update(s, 0, NOW)
        assert s.ease == MIN_EASE


class TestLapse:
    def test_lapse_resets_streak_and_penalises(self):
        s = fresh()
        for _ in range(3):
            s = sm2_update(s, 5, NOW)
        lapsed = sm2_update(s, 1, NOW)
        assert lapsed.repetitions == 0
        assert lapsed.interval_days == 1
        assert lapsed.lapses == 1
        assert lapsed.ease == pytest.approx(s.ease - 0.2)

    def test_lapse_after_lapse_counts_up(self):
        s = fresh()
        s = sm2_update(s, 1, NOW)
        s = sm2_update(s, 2, NOW)
        assert s.lapses == 2

    def test_success_does_not_increment_lapses(self):
        s = sm2_update(fresh(), 5, NOW)
        assert s.lapses == 0


class TestIsDue:
    def test_due_now(self):
        assert is_due(fresh(), NOW) is True

    def test_not_due_before_due_date(self):
        nxt = sm2_update(fresh(), 5, NOW)
        assert is_due(nxt, NOW) is False

    def test_due_after_due_date(self):
        nxt = sm2_update(fresh(), 5, NOW)
        assert is_due(nxt, NOW + DAY) is True

    def test_card_without_due_is_due(self):
        assert is_due(CardState(), NOW) is True
