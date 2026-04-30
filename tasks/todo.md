# Bugfix: workspace discovery tools

- [x] Confirm the failure mode from the screenshot: the planner asks for a file location because it has no search/list tool.
- [x] Add pure, workspace-bounded tools for filename and text search.
- [x] Register the tools in chat, doctor, and test runtime wiring.
- [x] Tighten planner instructions: search locally before asking the user for paths.
- [x] Add focused tests for discovery tool behavior and runtime registration.
- [x] Verification summary.

## Review

- Added `find_files` and `search_text` as read-only, workspace-bounded tools.
- Registered discovery tools in chat, doctor, and test runtime wiring.
- Updated planner prompt: local discovery comes before path clarification.
- Updated failure-diagnosis policy to allow explicitly listed read-only diagnostic tools.
- Fixed Windows executor timeout fallback: no `SIGALRM` means no-op timeout until sandbox/process supervisor exists.
- Verified with `pytest tests/`: 64 passed.
