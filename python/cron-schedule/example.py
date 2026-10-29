"""Runnable demo for cronschedule: read a few schedules, then probe one.

    python example.py
"""

import datetime as dt

from cronschedule import CronExpression, ScheduleExhausted

NOW = dt.datetime(2026, 10, 29, 21, 2)  # a Thursday evening

SCHEDULES = [
    "*/15 * * * *",
    "0 9-17 * * MON-FRI",
    "30 6 * * SAT,SUN",
    "0 0 1 * *",
    "0 0 13 * FRI",
    "0 0 29 2 *",
    "@daily",
]


def main():
    print(f"Starting point: {NOW:%a %d %b %Y %H:%M}\n")

    width = max(len(expression) for expression in SCHEDULES)
    for text in SCHEDULES:
        schedule = CronExpression(text)
        upcoming = schedule.iter_after(NOW, 3)
        rendered = ", ".join(moment.strftime("%a %d %b %H:%M") for moment in upcoming)
        print(f"{text:<{width}}  {schedule.describe()}")
        print(f"{'':<{width}}  -> {rendered}\n")

    print("A single schedule, step by step:")
    deploy = CronExpression("0 2 * * TUE,THU")
    print(f"  {deploy}  ({deploy.describe()})")
    for index, moment in enumerate(deploy.iter_after(NOW, 4), start=1):
        print(f"  run {index}: {moment:%A %d %B %Y at %H:%M}")

    print("\nDay-of-month and day-of-week together mean OR:")
    mixed = CronExpression("0 9 13 * FRI")
    print(f"  {mixed}")
    print(f"  {mixed.describe()}")
    for moment in mixed.iter_after(NOW, 5):
        print(f"  {moment:%a %d %b %Y}")

    print("\nImpossible schedules are reported, not silently ignored:")
    try:
        CronExpression("0 0 31 2 *").next_after(NOW)
    except ScheduleExhausted as error:
        print(f"  {error}")


if __name__ == "__main__":
    main()
