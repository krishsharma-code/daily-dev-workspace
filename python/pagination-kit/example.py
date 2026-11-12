"""Runnable demo for pagination_kit: walk a keyset-paginated list.

    python example.py
"""

from pagination_kit import (
    CursorError,
    PaginationError,
    decode_cursor,
    iter_pages,
    paginate,
)

# Seven orders, several of them created in the same second. A unique
# tiebreaker ("id") is what makes keyset pagination safe here.
ORDERS = [
    {"id": 101, "created_at": "2026-11-12T09:15:00Z", "total": 2499},
    {"id": 102, "created_at": "2026-11-12T09:15:00Z", "total": 799},
    {"id": 103, "created_at": "2026-11-12T10:02:00Z", "total": 15999},
    {"id": 104, "created_at": "2026-11-12T11:40:00Z", "total": 450},
    {"id": 105, "created_at": "2026-11-12T11:40:00Z", "total": 1299},
    {"id": 106, "created_at": "2026-11-12T12:05:00Z", "total": 3200},
    {"id": 107, "created_at": "2026-11-12T13:30:00Z", "total": 640},
]

SORT = ("created_at", "id")


def show(title):
    print(f"\n{title}\n{'-' * len(title)}")


def main():
    show("Page 1 (limit 2)")
    first = paginate(ORDERS, sort_keys=SORT, limit=2)
    for order in first:
        print(f"  #{order['id']}  {order['created_at']}  Rs {order['total']}")
    print(f"  has_more={first.has_more}  next_cursor={first.next_cursor}")
    decoded = decode_cursor(first.next_cursor)
    print(f"  cursor decodes to values={decoded.values} direction={decoded.direction}")

    show("Every page, following next_cursor")
    for number, page in enumerate(iter_pages(ORDERS, sort_keys=SORT, limit=3), 1):
        ids = ", ".join(f"#{order['id']}" for order in page)
        print(f"  page {number}: {ids}  (has_more={page.has_more})")

    show("Newest first")
    latest = paginate(ORDERS, sort_keys=SORT, limit=3, direction="desc")
    print("  " + ", ".join(f"#{order['id']}" for order in latest))

    show("A limit of 5000 is clamped to the 100-row ceiling")
    print(f"  requested 5000 -> page.limit={paginate(ORDERS, sort_keys=SORT, limit=5000).limit}")

    show("Cursors are untrusted input")
    for bad, note in [
        (first.next_cursor[:-1] + "!", "hand-edited character"),
        ("eyJ2Ijo5OSwiZCI6ImFzYyIsImsiOlsiYSJdfQ", "a version this build does not know"),
        (None, "no cursor at all is fine"),
    ]:
        try:
            page = paginate(ORDERS, sort_keys=SORT, limit=2, cursor=bad)
            print(f"  {note}: accepted, {page.count} item(s)")
        except CursorError as exc:
            print(f"  {note}: CursorError - {exc}")

    show("Sort keys must identify a row")
    try:
        paginate(ORDERS, sort_keys="created_at", limit=2)
    except PaginationError as exc:
        print(f"  PaginationError - {exc}")


if __name__ == "__main__":
    main()
