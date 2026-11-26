"""Runnable demo: turn a support ticket into an SLA deadline.

    python example.py
"""

from datetime import date, datetime, timedelta

from business_hours import BusinessCalendar

# India support desk: Monday to Friday, 09:30-18:30, closed on Republic Day.
desk = BusinessCalendar(
    ("09:30", "18:30"),
    holidays=["2026-01-26"],
    tz="Asia/Kolkata",
)


def show(label, value):
    print(f"{label:<46} {value}")


print(f"Calendar: {desk}\n")

# A ticket that arrives on Friday evening, with an 8 working-hour SLA.
opened = datetime(2026, 11, 27, 17, 0)  # Friday
due = desk.add_business_hours(opened, 8)
show("Ticket opened", opened.strftime("%a %d %b %Y %H:%M"))
show("SLA (8 working hours) due", due.strftime("%a %d %b %Y %H:%M"))

# 1.5 hours were already spent on Friday, so Monday carries the rest.
show("Business time used before the weekend", desk.business_time_between(opened, due))

# Pausing and resuming: how much open time passed between two moments?
paused = datetime(2026, 11, 27, 18, 0)  # Friday, just before the close
resumed = datetime(2026, 11, 30, 11, 0)  # Monday morning
show("Open time across the weekend", desk.business_time_between(paused, resumed))

# An early-morning arrival waits for the doors to open.
show("Next opening after 06:00 Monday", desk.next_open(datetime(2026, 11, 30, 6, 0)))

# A split shift: the same day, with a lunch break that does not count.
split = BusinessCalendar.from_windows(
    {0: [("09:00", "13:00"), ("14:00", "18:00")]},
    holidays=[],
)
monday = date(2026, 11, 30)
show(
    "Split shift: 12:00 + 2h (lunch excluded)",
    split.add_business_hours(datetime.combine(monday, datetime.min.time()).replace(hour=12), 2),
)

# Deadlines can be walked backwards too.
show("8 working hours before Monday 18:30", desk.add_business_hours(datetime(2026, 11, 30, 18, 30), -8))

# A holiday behaves like any other closed day.
closed_day = BusinessCalendar(("09:30", "18:30"), holidays=["2026-11-30"])
show(
    "Holiday on Monday: 2h from Friday 17:00",
    closed_day.add_business_hours(datetime(2026, 11, 27, 17, 0), 2),
)

# Weekly open capacity is a plain query, no arithmetic needed.
week_open = sum(
    (desk.business_time_between(
        datetime(2026, 11, 30) + timedelta(days=n),
        datetime(2026, 11, 30) + timedelta(days=n + 1),
    ) for n in range(7)),
    timedelta(0),
)
show("Open time in the week from 30 Nov", week_open)
