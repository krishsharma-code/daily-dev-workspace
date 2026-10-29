# python/

Pure Python work: scripts, packages, tests, and middleware.

## Modules

- [`cron-schedule/`](cron-schedule/) — parse five-field cron expressions and compute upcoming fire times
- [`data-cleaner/`](data-cleaner/) — clean and normalise tabular records
- [`dep-graph/`](dep-graph/) — deterministic dependency graph: ordering, layers, cycles, impact
- [`json-diff/`](json-diff/) — RFC 6902 JSON Patch diff and apply
- [`rate-limiter/`](rate-limiter/) — token bucket and sliding window rate limiters

## Conventions

Each module lives in its own folder and is self-contained:

```
python/my-module/
├── README.md        # what it does and how to run it
├── my-module.py      # implementation
├── example.py       # runnable demo
└── tests/           # pytest tests
    └── test_my-module.py
```

Run a module's tests from its own folder:

```bash
cd python/my-module
python -m pytest
```
