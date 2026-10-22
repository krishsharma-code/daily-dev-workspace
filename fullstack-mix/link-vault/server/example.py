"""Runnable tour of Link Vault's server-side pieces.

This uses the pure API router, so it needs no sockets and no browser::

    python server/example.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import route_request  # noqa: E402
from store import LinkStore  # noqa: E402


def main() -> None:
    store = LinkStore(":memory:")

    print("== saving a few links ==")
    for url, title, tags in [
        ("news.ycombinator.com", "Hacker News", ["news", "tech"]),
        ("https://docs.python.org/3/", "Python docs", ["python", "docs"]),
        ("https://github.com/trending", "GitHub Trending", ["tech"]),
    ]:
        status, _, payload = route_request(
            store,
            "POST",
            "/api/links",
            {},
            json.dumps({"url": url, "title": title, "tags": tags}).encode(),
        )
        link = payload["link"]
        print(f"  [{status}] #{link['id']} {link['title']} -> {link['url']} {link['tags']}")

    print("\n== all links (newest first) ==")
    _, _, payload = route_request(store, "GET", "/api/links")
    for link in payload["links"]:
        print(f"  #{link['id']} {link['title']}")

    print("\n== filtered by tag=tech ==")
    _, _, payload = route_request(store, "GET", "/api/links", {"tag": ["tech"]})
    for link in payload["links"]:
        print(f"  #{link['id']} {link['title']}")

    print("\n== search q=docs ==")
    _, _, payload = route_request(store, "GET", "/api/links", {"q": ["docs"]})
    for link in payload["links"]:
        print(f"  #{link['id']} {link['title']}")

    print("\n== health ==")
    _, _, payload = route_request(store, "GET", "/api/health")
    print(f"  {payload}")

    store.close()


if __name__ == "__main__":
    main()
