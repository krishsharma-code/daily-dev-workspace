# python/

Pure Python work: scripts, packages, tests, and middleware.

## Modules

_No modules yet. Each new module gets its own subfolder here._

## Conventions

Each module lives in its own folder and is self-contained:

```
python/<module-name>/
├── README.md        # what it does and how to run it
├── <module>.py      # implementation
└── tests/           # pytest tests
    └── test_<module>.py
```

Run a module's tests from its own folder:

```bash
cd python/<module-name>
python -m pytest
```
