# daily-dev-workspace

A structured workspace for daily, self-contained development work. Contributions are organised strictly by technology so that nothing loose ever sits in the repository root.

## Layout

| Folder | Contents |
| --- | --- |
| [`python/`](python/) | Pure Python scripts, packages, tests, and middleware |
| [`web-frontend/`](web-frontend/) | Standalone HTML, CSS, and modern JavaScript components/utilities |
| [`fullstack-mix/`](fullstack-mix/) | Mixed projects, each split into `client/` and `server/` |

## Rules

- No loose files or code in the repository root — only this README, `.gitignore`, and the top-level technology folders.
- Every contribution lives in its own subfolder named for the tool, module, or feature (for example `python/data-cleaner/` or `web-frontend/custom-toggle-widget/`).
- Each subfolder is self-contained: its own code, its own tests or runnable examples, and a short `README.md` explaining how to run or use it.
- Code must run cleanly and all tests must pass before it is committed.
- Commits use conventional messages (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`).
