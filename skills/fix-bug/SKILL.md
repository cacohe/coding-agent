---
name: fix-bug
description: Reproduce, locate, fix with a minimal diff, then verify—standard bugfix loop.
---

# Fix Bug

1. Restate the failing behavior and any error text the user gave.
2. Locate the suspect code with `glob` + `read_file` (do not guess paths).
3. Form a hypothesis; change the smallest surface with `edit_file`.
4. Verify: re-read the edit; run the relevant test/command via `exec` if available.
5. Summarize root cause + fix; note residual risk if any.
