"""Tests for the pure slot-finding logic."""

from datetime import datetime, timedelta, timezone

import pytest

from core import (
    InputError,
    merge_intervals,
    normalise_weekdays,
    parse_busy,
    parse_instant,
    subtract_intervals,
    find_slots,
)

UTC = timezone.utc


def utc(text: str) -> datetime:
    return parse_instant(text)


def starts(result):
    return [slot["start"] for slot in result["slots"]]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_instant_normalises_to_utc():
    assert parse_instant("2026-11-16T09:00+05:30") == datetime(2026, 11, 16, 3, 30, tzinfo=UTC)
    assert parse_instant("2026-11-16T03:30Z") == datetime(2026, 11, 16, 3, 30, tzinfo=UTC)
    assert parse_instant(" 2026-11-16T03:30z ") == datetime(2026, 11, 16, 3, 30, tzinfo=UTC)


def test_parse_instant_rejects_naive_times():
    with pytest.raises(InputError, match="no UTC offset"):
        parse_instant("2026-11-16T09:00")


def test_parse_instant_rejects_junk():
    with pytest.raises(InputError, match="not an ISO-8601"):
        parse_instant("next tuesday")


def test_parse_busy_skips_comments_and_blanks_and_merges():
    text = """
    # Monday morning
    2026-11-16T09:00+05:30/2026-11-16T10:00+05:30

    2026-11-16T09:30+05:30/2026-11-16T11:00+05:30   # overlaps the first
    2026-11-16T11:00+05:30/2026-11-16T11:30+05:30   # touches the second
    """
    merged = parse_busy(text)
    assert merged == [(utc("2026-11-16T03:30Z"), utc("2026-11-16T06:00Z"))]


def test_parse_busy_reports_the_offending_line():
    with pytest.raises(InputError, match="busy line 2"):
        parse_busy("2026-11-16T09:00+05:30/2026-11-16T10:00+05:30\nnonsense")


def test_parse_busy_rejects_backwards_block():
    with pytest.raises(InputError, match="end must be after start"):
        parse_busy("2026-11-16T10:00+05:30/2026-11-16T09:00+05:30")


def test_parse_busy_accepts_empty_input():
    assert parse_busy("") == []
    assert parse_busy("   \n# only a comment\n") == []


# ---------------------------------------------------------------------------
# Interval arithmetic
# ---------------------------------------------------------------------------


def test_merge_intervals_joins_touching_blocks():
    a = (utc("2026-11-16T03:00Z"), utc("2026-11-16T04:00Z"))
    b = (utc("2026-11-16T04:00Z"), utc("2026-11-16T05:00Z"))
    assert merge_intervals([b, a]) == [(a[0], b[1])]


def test_subtract_intervals_basic():
    window = (utc("2026-11-16T03:30Z"), utc("2026-11-16T12:30Z"))
    busy = [(utc("2026-11-16T04:00Z"), utc("2026-11-16T05:00Z"))]
    assert subtract_intervals(window, busy) == [
        (utc("2026-11-16T03:30Z"), utc("2026-11-16T04:00Z")),
        (utc("2026-11-16T05:00Z"), utc("2026-11-16T12:30Z")),
    ]


def test_subtract_intervals_ignores_busy_outside_the_window():
    window = (utc("2026-11-16T03:30Z"), utc("2026-11-16T12:30Z"))
    busy = [
        (utc("2026-11-16T01:00Z"), utc("2026-11-16T02:00Z")),
        (utc("2026-11-16T13:00Z"), utc("2026-11-16T14:00Z")),
    ]
    assert subtract_intervals(window, busy) == [window]


def test_subtract_intervals_fully_booked_day_is_empty():
    window = (utc("2026-11-16T03:30Z"), utc("2026-11-16T12:30Z"))
    busy = [(utc("2026-11-16T00:00Z"), utc("2026-11-17T00:00Z"))]
    assert subtract_intervals(window, busy) == []


# ---------------------------------------------------------------------------
# Weekday selection
# ---------------------------------------------------------------------------


def test_normalise_weekdays_accepts_names_ints_and_mixes():
    assert normalise_weekdays(None) == (0, 1, 2, 3, 4)
    assert normalise_weekdays(["Mon", "wednesday", 4]) == (0, 2, 4)
    assert normalise_weekdays("sat") == (5,)


def test_normalise_weekdays_rejects_bad_values():
    with pytest.raises(InputError, match="outside 0"):
        normalise_weekdays([7])
    with pytest.raises(InputError, match="not a weekday name"):
        normalise_weekdays(["funday"])
    with pytest.raises(InputError, match="at least one"):
        normalise_weekdays([])


# ---------------------------------------------------------------------------
# find_slots
# ---------------------------------------------------------------------------


def test_empty_calendar_walks_the_whole_grid():
    result = find_slots(
        [],
        timezone_name="Asia/Kolkata",
        start_date="2026-11-16",
        end_date="2026-11-16",
        duration_min=30,
        step_min=30,
        limit=100,
    )
    assert starts(result)[:2] == [
        "2026-11-16T09:00:00+05:30",
        "2026-11-16T09:30:00+05:30",
    ]
    # 09:00–18:00 in 30-minute steps, last one starting 17:30.
    assert len(result["slots"]) == 18
    assert result["slots"][-1]["start"] == "2026-11-16T17:30:00+05:30"
    assert result["days"][0]["free_minutes"] == 540
    assert result["complete"] is True


def test_slots_never_overlap_a_busy_block():
    busy = parse_busy("2026-11-16T10:00+05:30/2026-11-16T10:45+05:30")
    result = find_slots(
        busy,
        timezone_name="Asia/Kolkata",
        start_date="2026-11-16",
        end_date="2026-11-16",
        duration_min=30,
        step_min=15,
        limit=100,
    )
    for slot in result["slots"]:
        slot_start = parse_instant(slot["start"])
        slot_end = parse_instant(slot["end"])
        for busy_start, busy_end in busy:
            assert slot_end <= busy_start or slot_start >= busy_end
    assert "2026-11-16T10:00:00+05:30" not in starts(result)
    assert "2026-11-16T10:45:00+05:30" in starts(result)


def test_grid_is_anchored_at_the_working_day_start():
    # Busy 09:00–09:40, so the first free minute is 09:40; the 15-minute grid
    # still offers 09:45, not 09:40.
    busy = parse_busy("2026-11-16T09:00+05:30/2026-11-16T09:40+05:30")
    result = find_slots(
        busy,
        timezone_name="Asia/Kolkata",
        start_date="2026-11-16",
        end_date="2026-11-16",
        duration_min=30,
        step_min=15,
        limit=1,
    )
    assert starts(result) == ["2026-11-16T09:45:00+05:30"]
    assert result["truncated"] is True


def test_limit_stops_the_search_early():
    result = find_slots(
        [],
        timezone_name="Asia/Kolkata",
        start_date="2026-11-16",
        end_date="2026-11-20",
        duration_min=60,
        step_min=60,
        limit=3,
    )
    assert len(result["slots"]) == 3
    assert result["truncated"] is True
    assert result["complete"] is False
    assert len(result["days"]) == 1


def test_weekends_are_skipped_by_default():
    result = find_slots(
        [],
        timezone_name="Asia/Kolkata",
        start_date="2026-11-14",  # Saturday
        end_date="2026-11-15",  # Sunday
    )
    assert result["slots"] == []
    assert [day["included"] for day in result["days"]] == [False, False]
    assert result["days"][0]["reason"] == "not a working day"


def test_weekdays_can_include_saturday():
    result = find_slots(
        [],
        timezone_name="Asia/Kolkata",
        start_date="2026-11-14",
        end_date="2026-11-14",
        weekdays=["sat"],
        duration_min=60,
        step_min=60,
        limit=5,
    )
    assert starts(result)[0] == "2026-11-14T09:00:00+05:30"


def test_busy_blocks_in_other_offsets_land_on_the_right_local_minutes():
    # 04:00Z–05:00Z is 09:30–10:30 IST, so 09:00 and 10:30 survive.
    busy = parse_busy("2026-11-16T04:00Z/2026-11-16T05:00Z")
    result = find_slots(
        busy,
        timezone_name="Asia/Kolkata",
        start_date="2026-11-16",
        end_date="2026-11-16",
        duration_min=30,
        step_min=30,
        limit=2,
    )
    assert starts(result) == ["2026-11-16T09:00:00+05:30", "2026-11-16T10:30:00+05:30"]
    assert result["slots"][1]["start_utc"] == "2026-11-16T05:00:00Z"


def test_earliest_drops_slots_that_already_started():
    result = find_slots(
        [],
        timezone_name="Asia/Kolkata",
        start_date="2026-11-16",
        end_date="2026-11-16",
        duration_min=30,
        step_min=30,
        limit=1,
        earliest=utc("2026-11-16T06:00Z"),  # 11:30 IST
    )
    assert starts(result) == ["2026-11-16T11:30:00+05:30"]


def test_daylight_saving_day_loses_an_hour_of_free_time():
    # New York springs forward at 02:00 on 2026-03-08, so a 00:00–06:00
    # working window is five real hours long, not six.
    result = find_slots(
        [],
        timezone_name="America/New_York",
        start_date="2026-03-08",
        end_date="2026-03-08",
        weekdays=["sun"],
        work_start="00:00",
        work_end="06:00",
        duration_min=60,
        step_min=60,
        limit=100,
    )
    assert result["days"][0]["free_minutes"] == 300
    assert len(result["slots"]) == 5


def test_each_day_reports_its_own_free_minutes():
    busy = parse_busy("2026-11-17T09:00+05:30/2026-11-17T12:00+05:30")
    result = find_slots(
        busy,
        timezone_name="Asia/Kolkata",
        start_date="2026-11-16",
        end_date="2026-11-17",
        duration_min=60,
        step_min=60,
        limit=100,
    )
    free = {day["date"]: day["free_minutes"] for day in result["days"]}
    assert free == {"2026-11-16": 540, "2026-11-17": 360}
    assert result["slots"][0]["date"] == "2026-11-16"
    assert result["slots"][-1]["date"] == "2026-11-17"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"duration_min": 0}, "duration_min"),
        ({"step_min": 500}, "step_min"),
        ({"limit": 0}, "limit"),
        ({"work_start": "18:00", "work_end": "09:00"}, "work_end must be after"),
        ({"timezone_name": "Mars/Olympus"}, "not a known IANA time zone"),
        ({"start_date": "2026-11-16", "end_date": "2026-11-10"}, "must not be before"),
        ({"start_date": "2026-01-01", "end_date": "2026-12-31"}, "maximum is 62"),
        ({"work_start": "9am"}, "not a time like"),
    ],
)
def test_invalid_input_is_rejected(kwargs, message):
    base = dict(
        timezone_name="Asia/Kolkata",
        start_date="2026-11-16",
        end_date="2026-11-16",
        duration_min=30,
        step_min=15,
        limit=10,
    )
    base.update(kwargs)
    with pytest.raises(InputError, match=message):
        find_slots([], **base)


def test_earliest_must_be_aware():
    with pytest.raises(InputError, match="needs a UTC offset"):
        find_slots(
            [],
            timezone_name="Asia/Kolkata",
            start_date="2026-11-16",
            end_date="2026-11-16",
            earliest=datetime(2026, 11, 16, 9, 0),
        )
