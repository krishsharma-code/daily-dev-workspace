"""A runnable tour of the streak engine and the store -- no sockets involved.

Run from this folder::

    python example.py
"""

from datetime import date, timedelta

from store import HabitStore
from streak import completion_rate, current_streak, longest_streak, weekday_label

TODAY = date(2026, 11, 25)  # a Wednesday


def show(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> None:
    with HabitStore(":memory:") as store:
        show("1. Create two habits")
        gym = store.create_habit("Gym", ["mon", "wed", "fri"])
        reading = store.create_habit("Read 20 pages")
        for habit in (gym, reading):
            names = ", ".join(weekday_label(day) for day in habit["weekdays"])
            print(f"  {habit['id']:<16} {habit['name']:<16} due on {names}")

        show("2. Check in some days")
        for offset in range(5, 0, -1):
            day = TODAY - timedelta(days=offset)
            result = store.check_in("read-20-pages", day, today=TODAY)
            print(f"  read-20-pages {day}  created={result['created']}")
        # Friday 20 Nov, Monday 23 Nov and today.
        for day in (date(2026, 11, 20), date(2026, 11, 23), TODAY):
            store.check_in("gym", day, today=TODAY)
            print(f"  gym            {day}  created=True")

        show("3. Checking in twice is a no-op, not an error")
        again = store.check_in("read-20-pages", TODAY - timedelta(days=1), today=TODAY)
        print(f"  created={again['created']}  (history still has "
              f"{len(store.checkins('read-20-pages'))} entries)")

        show("4. Today is still open, so it does not break a streak")
        gym_days = store.checkin_days("gym")
        print(f"  gym check-ins: {gym_days}")
        print(f"  gym current streak: {current_streak(gym_days, TODAY, gym['weekdays'])}")
        print(f"  gym best streak:    {longest_streak(gym_days, gym['weekdays'])}")

        show("5. Completion over the last 7 days")
        week_start = TODAY - timedelta(days=6)
        for habit in store.list_habits():
            rate = completion_rate(
                store.checkin_days(habit["id"]), week_start, TODAY, habit["weekdays"]
            )
            print(
                f"  {habit['id']:<16} {rate['done']}/{rate['scheduled']} "
                f"({rate['percent']}%)  missed: {rate['missed']}"
            )

        show("6. The board payload the client renders")
        board = store.board(TODAY)
        print(f"  today={board['today']}  counts={board['counts']}")
        for habit in board["habits"]:
            summary = habit["summary"]
            print(
                f"  {habit['name']:<16} current={summary['current']} "
                f"longest={summary['longest']} done_today={summary['done_today']}"
            )

        show("7. The store refuses a future check-in")
        try:
            store.check_in("gym", TODAY + timedelta(days=1), today=TODAY)
        except ValueError as exc:
            print(f"  refused: {exc}")


if __name__ == "__main__":
    main()
