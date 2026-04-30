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

# Bugfix: reduce repeated LM Studio prompt payload

- [x] Confirm repeated system prompt path in planner loop.
- [x] Remove per-iteration system message from LM Studio requests.
- [x] Keep large planner bootstrap prompt in code for a future stateful/session backend.
- [x] Compact JSON-only context payload sent to LM Studio during runtime steps.
- [x] Add prompt regression test.
- [x] Verify tests.

## Review

- Runtime planner requests no longer include `role=system`.
- `PLANNER_BOOTSTRAP_PROMPT` exists for a future stateful/session backend; current LM Studio Chat Completions is stateless, so a fake init request is not sent.
- Per-step model input is now a single compact JSON block.
- Verified with `pytest tests/`: 65 passed.
