# python/

Pure Python work: scripts, packages, tests, and middleware.

## Modules

- [`backoff-planner/`](backoff-planner/) — deadline-aware retry scheduling: capped exponential backoff, jitter, `Retry-After`
- [`cron-schedule/`](cron-schedule/) — parse five-field cron expressions and compute upcoming fire times
- [`data-cleaner/`](data-cleaner/) — clean and normalise tabular records
- [`dep-graph/`](dep-graph/) — deterministic dependency graph: ordering, layers, cycles, impact
- [`expr-eval/`](expr-eval/) — safe arithmetic and logic expression evaluator (no `eval`)
- [`india-id-kit/`](india-id-kit/) — validate, normalise and mask Aadhaar, PAN, GSTIN, IFSC, UPI, mobile, PIN and vehicle IDs
- [`json-diff/`](json-diff/) — RFC 6902 JSON Patch diff and apply
- [`pagination-kit/`](pagination-kit/) — keyset (cursor) pagination with opaque, validated cursors
- [`rate-limiter/`](rate-limiter/) — token bucket and sliding window rate limiters
- [`semver-kit/`](semver-kit/) — SemVer 2.0.0 parsing, precedence, bumping and npm-style ranges

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
