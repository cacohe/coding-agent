---
name: explore-codebase
description: How to map an unfamiliar repo before changing code—glob, read entrypoints, then edit.
---

# Explore Codebase

Before changing code in an unfamiliar workspace:

1. `glob` for entrypoints (`**/main.py`, `**/cli.py`, `**/package.json`, `**/pyproject.toml`).
2. `read_file` the README and the nearest module to the request.
3. Identify the smallest file set that must change.
4. Only then call `edit_file` / `write_file`.
5. Prefer verifying with `exec` (tests or typecheck) when the project has them.
