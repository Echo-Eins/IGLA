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

# Temporary Plain CLI

- [x] Capture requirement: disable Rich REPL as default and provide copyable plain output.
- [x] Add a simple plain interactive CLI loop.
- [x] Add a one-shot command for a single request.
- [x] Keep every user request on a fresh task id.
- [x] Add focused tests for plain output rendering / parser wiring.
- [x] Verify tests.
- [x] Review current prompt-send path without changing it.

## Review

- Default `igla` and `igla chat` now use plain text interactive CLI.
- Added `igla run <request...>` for one-shot copyable debugging.
- Old Rich UI remains available as `igla rich-chat`.
- Each plain request creates a fresh task id; output lists status, TODO, and full task events.
- Verified with `pytest tests/`: 75 passed.

# Bugfix: reset poisoned state and v-prefixed tool versions

- [x] Capture failure mode from plain CLI log.
- [x] Add safe workspace state reset command.
- [x] Add `/reset-state` to the plain CLI and rewire in-process runtime after reset.
- [x] Accept planner tool versions with a leading `v` prefix.
- [x] Add focused regression tests.
- [x] Verify tests and document review.

## Review

- Added safe reset helper for workspace `.igla` state.
- Added `igla reset-state` and `/reset-state`; the plain CLI rewires Kernel/TodoStore/Planner after reset.
- Registry now accepts `v1.0.0` / `V1.0.0` references for tools registered as `1.0.0`.
- Documented the new command in `docs/ENGINE.md`.
- Verified with `pytest tests/`: 81 passed.
