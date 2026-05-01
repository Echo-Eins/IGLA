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

# Bugfix: block premature user questions through all channels

- [x] Capture root cause: `ask_user` tool can bypass `ask_user_clarification` policy.
- [x] Gate both `ask_user_clarification` and tool `ask_user` behind discovery.
- [x] Restrict discovery unlock to real workspace discovery tools, not generic read-only tools.
- [x] Add planner regressions for malformed `question` output and direct `ask_user`.
- [x] Verify tests and document review.

## Review

- Closed the policy bypass where `tool:ask_user` could contact the user before discovery.
- `discovery_before_clarification` now covers both `ask_user_clarification` and direct `ask_user` tool invocations.
- Clarification unlock now requires a real workspace discovery tool, not any read-only tool.
- Added regressions for bare question-shaped model output and direct `ask_user` proposals.
- Verified with `pytest tests/`: 85 passed.

# Bugfix: expose tool outputs to planner before clarification

- [x] Capture root cause: planner sees `output_keys`, not found paths/content.
- [x] Store compact read-only tool output in tool completion events.
- [x] Include compact tool output in planner event tail.
- [x] Deny user questions when discovery already produced an unambiguous result.
- [x] Add regressions for `AGENTS.md` discovery result visibility and no-question behavior.
- [x] Verify tests and document review.

## Review

- Tool completion events now store compact output for `find_files`, `search_text`, and `read_file`.
- Planner event tail now includes compact tool output, so the next LLM turn sees found paths/content excerpts.
- Policy now denies user questions when discovery already produced an unambiguous result.
- Added regressions proving `AGENTS.md` appears in the next planner prompt and a question after that result is rejected.
- Verified with `pytest tests/`: 88 passed.

# Bugfix: strict planner action boundary

- [x] Capture root cause: schema variants allow missing `action` because discriminator fields have defaults.
- [x] Make `action` required in every PlannerProposal JSON Schema variant.
- [x] Stop coercing bare `question` payloads into user clarification.
- [x] Update malformed proposal regressions.
- [x] Verify tests and document lessons.

## Review

- PlannerProposal JSON Schema now requires `action` in every concrete variant and removes the discriminator default from schema output.
- Parser no longer infers `ask_user_clarification`, `todo_branch`, or `declare_task_done` from missing-action payloads.
- Bare `{"reason": "...", "question": "..."}` is now `MALFORMED_PROPOSAL`, not `QUESTION`.
- Kept only the narrow missing-action `tool_invocation` repair path because it still passes through ToolRegistry/PolicyEngine.
- Verified with `pytest tests/`: 89 passed.

# Bugfix: finish proven simple find tasks without another LLM turn

- [x] Capture failure mode: `find_files` returns README.md, then weak LLM keeps planning and corrupts the task.
- [x] Add a small post-tool completion gate for simple file-find requests.
- [x] Keep open/read flows model-driven after discovery; finding a file is not enough for "open/read".
- [x] Clean terminal control characters/backspaces from plain CLI input.
- [x] Add focused regressions for one-turn find completion and input cleanup.
- [x] Verify tests and document review.

## Review

- Added a deterministic post-`find_files` completion gate for simple file-location tasks.
- The gate only fires after a successful tool result and refuses to close open/read/show/inspect style tasks.
- Added plain CLI input cleanup for leaked backspace/delete and ANSI CSI sequences.
- Added regressions for one-turn README discovery completion and input cleanup.
- Verified with `pytest tests/`: 92 passed.
- Verified changed files with `ruff check src/igla/planner/planner.py src/igla/console_io.py tests/test_planner_loop.py tests/test_console_io.py`.
- Verified changed source files with `mypy src/igla/planner/planner.py src/igla/console_io.py`.
