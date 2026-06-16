# Repository Guidelines

## Project Structure & Module Organization

JianerCore is a Python package published as `jianer-bot` and imported as `jianer`.
Core source lives in `jianer/`: top-level modules such as `events.py`, `listener.py`,
`network.py`, and `segments.py` define the framework surface, while
`jianer/adapters/` and `jianer/LecAdapters/` contain protocol adapter code for
OneBot, Milky, and Kritor. Shared helpers are under `jianer/utils/`.

Tests live in `tests/` and should mirror package behavior with files named
`test_*.py`. Project documentation is in `README.md` and `documents/`. Root assets
such as `ban.png` and `logo.ico` are packaging/project assets. `config.json` is a
local runtime example; avoid committing real tokens, QQ IDs, or deployment secrets.

## Build, Test, and Development Commands

Create and install a local development environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

Run tests with `.\.venv\Scripts\python.exe -m pytest`. Build distribution artifacts
with `.\.venv\Scripts\python.exe -m build`; outputs are written to `dist/`.

## Coding Style & Naming Conventions

Use Python 3.9+ syntax and 4-space indentation. Prefer explicit names for public
classes, events, adapters, and actions. Modules and functions should use
`snake_case`; classes should use `PascalCase`; constants should use `UPPER_SNAKE_CASE`.
Keep imports grouped as standard library, third-party packages, then local `jianer`
imports. There is no dedicated formatter configuration yet, so match the surrounding
style and keep changes narrowly scoped.

## Testing Guidelines

Use `pytest` for all tests. Add or update tests in `tests/` when changing package
identity, logging, event dispatch, adapter behavior, or public APIs. Test names
should describe behavior, for example `test_logger_level_filtering`. Run the full
suite before committing; add focused tests for regressions whenever a bug is fixed.

## Commit & Pull Request Guidelines

Recent history uses short imperative messages, often Conventional Commit prefixes
such as `feat:` and `build:`. Prefer that style, for example
`docs: update contributor guide` or `fix: handle unknown event type`.

Pull requests should include a concise summary, test results, linked issues when
available, and screenshots or logs only when they clarify user-visible behavior.
Keep PRs focused on one topic and call out packaging or configuration changes.

## Agent-Specific Instructions

After each user-requested round of code or documentation changes, run the relevant
checks and create one local Git commit containing that completed work. Never push
commits or tags unless the user explicitly requests a push.
