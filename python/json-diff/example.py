"""Runnable demo for jsondiff: diff two documents, then replay the patch.

    python example.py
"""

import json

from jsondiff import PatchError, apply, diff

BEFORE = {
    "service": "billing",
    "replicas": 2,
    "env": {"LOG_LEVEL": "info", "REGION": "ap-south-1", "TZ": "Asia/Kolkata"},
    "ports": [8080, 9090],
    "feature_flags": {"retries": True},
    "labels": {"team": "payments", "tier": "critical", "owner": "krish"},
    "resources": {
        "requests": {"cpu": "250m", "memory": "512Mi"},
        "limits": {"cpu": "1", "memory": "1Gi"},
    },
    "probes": {
        "liveness": {"path": "/healthz", "period_seconds": 10},
        "readiness": {"path": "/ready", "period_seconds": 5},
    },
}

AFTER = json.loads(json.dumps(BEFORE))
AFTER["replicas"] = 3
AFTER["env"]["LOG_LEVEL"] = "debug"
AFTER["ports"].append(9091)
AFTER["feature_flags"]["idempotency"] = True


def show(title, value):
    print(f"{title}")
    print(json.dumps(value, indent=2, sort_keys=True))
    print()


def main():
    patch = diff(BEFORE, AFTER)

    show("patch (RFC 6902):", patch)

    full = len(json.dumps(AFTER, separators=(",", ":")))
    delta = len(json.dumps(patch, separators=(",", ":")))
    print(f"full document: {full} bytes, patch: {delta} bytes "
          f"({delta / full:.0%} of the whole document -- most of it is "
          f"unchanged, so the patch stays small)\n")

    show("apply(before, patch) == after:", apply(BEFORE, patch) == AFTER)

    # A hand-written patch can use move/copy/test, which diff never emits.
    hand_written = [
        {"op": "test", "path": "/service", "value": "billing"},
        {"op": "move", "from": "/env/LOG_LEVEL", "path": "/log_level"},
        {"op": "copy", "from": "/ports/0", "path": "/healthcheck_port"},
    ]
    show("hand-written patch result:", apply(BEFORE, hand_written))

    # A failing test op aborts the whole patch and leaves the input untouched.
    try:
        apply(BEFORE, [{"op": "test", "path": "/replicas", "value": 99}])
    except PatchError as error:
        print(f"rejected as expected: {error}")
    print(f"BEFORE still has replicas={BEFORE['replicas']}")

    # A malformed patch from a peer is rejected as a PatchError. Without the
    # type checks this would escape as TypeError: unhashable type: 'dict'.
    try:
        apply(BEFORE, [{"op": {"op": "add"}, "path": "/x", "value": 1}])
    except PatchError as error:
        print(f"malformed patch rejected: {error}")


if __name__ == "__main__":
    main()
