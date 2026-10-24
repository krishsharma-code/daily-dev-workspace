"""A runnable tour of the Flashcards logic - no sockets, no HTTP.

Run it with::

    python server/example.py

It creates a couple of cards, reviews them with the SM-2 scheduler and prints
how their intervals grow. The same ``CardStore`` backs the HTTP server.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import CardStore  # noqa: E402

DAY = timedelta(days=1)


def main() -> None:
    store = CardStore(":memory:")
    clock = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)

    cards = [
        store.add("What is the capital of Japan?", "Tokyo", deck="geography", now=clock),
        store.add("What does HTTP stand for?", "HyperText Transfer Protocol", deck="networking", now=clock),
    ]
    print(f"Created {store.count()} cards across {len(store.decks(now=clock))} decks.")
    print(f"Due right now: {store.due_count(now=clock)}\n")

    # Review the first card four times with a perfect score and watch the
    # interval grow: 1 day -> 6 days -> 16 days -> 45 days.
    print("Reviewing card 1 with quality 5 each time:")
    card = cards[0]
    for i in range(4):
        card = store.review(card.id, 5, now=clock)
        print(f"  review {i + 1}: interval={card.interval_days:>3}d ease={card.ease:.2f} due={card.due}")

    # A lapse resets the streak and docks the ease.
    print("\nNow a lapse (quality 1):")
    card = store.review(card.id, 1, now=clock)
    print(f"  interval={card.interval_days}d ease={card.ease:.2f} reps={card.repetitions} lapses={card.lapses}")

    print("\nDeck summary:")
    for deck in store.decks(now=clock):
        print(f"  {deck['deck']:<12} total={deck['total']} due={deck['due']}")

    print(f"\nDue tomorrow: {store.due_count(now=clock + DAY)}")
    store.close()


if __name__ == "__main__":
    main()
