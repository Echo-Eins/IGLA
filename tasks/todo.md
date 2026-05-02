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

# Feature: list_dir tool with depth + constitution + motivation

- [x] Implement `list_dir` tool, depth-limited (max 4).
- [x] Add `list_dir_depth_limit` constitution predicate.
- [x] Update motivation to include `list_dir` in failure-diagnosis allowed actions and add `log_workspace_structure_known`.
- [x] Register tool in cli/plain/repl wiring.
- [x] Update planner prompts and TOOL_USAGE_EXAMPLES.
- [x] Add comprehensive list_dir tests.

## Review

- `list_dir` returns a flat DFS-ordered tree with per-directory file/dir counts and an `expanded` flag so the model can decide whether to drill deeper without another round-trip.
- `_DEPTH_LIMIT=4` enforced as a constitution predicate (`DEPTH_LIMIT_EXCEEDED`) with a strategy hint.
- Verified with `pytest tests/`: 150 passed.

# Feature: patch_file + RollbackManager

- [x] Add `RollbackManager` kernel service backed by `ArtifactStore`.
- [x] Add protocol envelopes: `SnapshotDescriptor`, `RollbackPlan`, `RollbackResult`.
- [x] Extend `read_file` with `file_sha256` (full-file hash regardless of partial reads).
- [x] Extend `FileReadReceipt` and `ReceiptManager` with `file_sha256`.
- [x] Implement `patch_file` tool: read-before-write + hash-before-patch + backup-before-mutation; supports `new_content` and `search`+`replacement` modes.
- [x] Implement `restore_file` tool for explicit revert via `backup_artifact_id`.
- [x] Add constitution predicates `read_before_write` and `hash_matches_receipt`.
- [x] Update motivation: `mark_changed_condition_after_patch`, `log_restore_file_completed`.
- [x] Register tools in cli/plain/repl wiring.
- [x] Update planner prompts and TOOL_USAGE_EXAMPLES.
- [x] Add comprehensive tests for RollbackManager, patch_file, restore_file.

## Review

- **RollbackManager** delegates storage to `ArtifactStore`: every snapshot is an immutable artifact of type `FileSnapshot` with `original_path`/`task_id`/`step_id` metadata. Plans live in an in-process map keyed by `plan_id`. `execute(plan_id)` is best-effort across snapshots and returns a structured `RollbackResult` (does not raise).
- **patch_file** enforces three invariants: prior `read_file` receipt exists, on-disk SHA-256 matches `base_sha256` (re-checked in the tool itself, not just at the policy layer), and a backup snapshot is created BEFORE any write. Atomic write via temp-file rename; on `WRITE_FAILED` the backup is restored best-effort.
- Two patch modes: `new_content` (full replace) and `search`+`replacement` (point edit). Default `replace_all=false` makes ambiguous matches a hard rejection (`AMBIGUOUS_SEARCH`).
- After a successful patch the read receipt is refreshed with the new `file_sha256`, so any follow-up patch must use the new hash. Iterative patches still need a fresh `read_file` of the target.
- **restore_file** is the planner-callable form of `RollbackManager.restore_snapshot` keyed by `backup_artifact_id` from a prior `patch_file` output. The `original_path` is taken from the artifact metadata; the planner cannot redirect the restore.
- **read_file** now exposes `file_sha256` (whole-file hash, regardless of partial reads) alongside `sha256` (slice hash). The same value is stored in the receipt for `hash_matches_receipt`.
- Constitution predicates: `read_before_write` denies `patch_file` without a prior receipt for the resolved path; `hash_matches_receipt` denies when the supplied `base_sha256` differs from the stored receipt hash. Both give the model concrete `allowed_next` and hints.
- Motivation: a successful `patch_file` sets `changed_condition_declared` so the runtime can later exit `FAILURE_DIAGNOSIS_REQUIRED` once the failure is classified.
- Verified with `pytest tests/`: 199 passed (49 new tests across `test_rollback_manager.py` and `test_patch_file.py`).
- Verified with `ruff check` on all changed source files: clean.

# Continue transfer note: verifier + per-task memory hardening

- [x] Read transfer note and inspect the last commit additions.
- [x] Verify `verify_file` / `read_task_log` do not unlock user clarification as discovery.
- [x] Add focused tests for `verify_file` receipt gate, syntax checks, lint/pytest command paths, and failure modes.
- [x] Add focused tests for `TaskWorkLog` / `read_task_log` closure, truncation, and summaries.
- [x] Check default toolset wiring for new tools.
- [x] Run full tests and focused lint/type checks.
- [x] Document review and setup notes.

## Review

- `verify_file` and `read_task_log` are explicitly excluded from workspace discovery unlocks; they cannot legitimize asking the user for paths.
- `verify_file` now runs subprocess checks through the current interpreter (`python -m ruff` / `python -m pytest` via `sys.executable`), which is venv-safe.
- `TaskWorkLog` summaries now match current tool output shapes (`find_files.matches/count`, `search_text.matches/count`, `list_dir.root`).
- Test runtime wiring now uses `build_default_toolset`, so planner tests exercise the same default tool registration surface as the CLI.
- Added `test_verify_file.py`, `test_task_work_log.py`, new policy regressions, and default toolset coverage.
- Fixed CRLF-sensitive `test_patch_file.py` writes by using explicit LF bytes where tests assert hashes/backup bytes.
- Documented current `verify_file` setup and behavior in `docs/10-verification-rollback.md`.
- Verified with `pytest tests/`: 214 passed.
- Verified changed files with focused `ruff check`: clean.
- Verified changed source files with focused `mypy`: clean.

# Bugfix: copy new files without synthesizing full content in the LLM

- [x] Capture the transfer-note failure mode.
- [x] Add a workspace-bounded `copy_file` tool for source-to-destination copies with optional append text.
- [x] Register `copy_file` in all runtime toolsets and planner examples.
- [x] Expose compact `copy_file` output / work-log summaries.
- [x] Add regressions for copy, destination safety, tool registration, and planner-visible output.
- [x] Verify tests and document review.

## Review

- Fresh transfer note shows a new failure mode: after a user asked to copy `README.md` to `README1.md` and append `hello`, the planner tried to use `patch_file` with `new_content` for a non-existent destination. Policy correctly denied it with `MUST_READ_BEFORE_WRITE`, because `patch_file` is for existing files and requires a prior read receipt of the same target path.
- Added `copy_file` as the correct primitive for this operation: workspace-bounded source/destination, optional `append_text`, no overwrite by default, `overwrite=true` creates a rollback snapshot before replacing an existing destination.
- `copy_file` records a destination read receipt after writing, so follow-up verification or patching has a current `file_sha256`.
- Registered `copy_file` in the default toolset, plain CLI, and Rich REPL.
- Updated planner hard rules and tool examples: creating a copy must use `copy_file`, not full-file `patch_file.new_content`.
- Added compact planner output and TaskWorkLog summaries for `copy_file`.
- Added regressions for copy+append, destination safety, overwrite backup, workspace escape denial, default registration, planner compact output, work-log summary, and a planner flow that creates `README1.md`.
- Verified with `pytest tests/`: 227 passed.
- Verified changed files with focused `ruff check`: clean.
- Verified changed source files with focused `mypy`: clean.

# Feature: Ollama LLM backend

- [x] Capture requirement: native Ollama localhost API with arbitrary model name from `ollama list`.
- [x] Add config/env/CLI provider selection without removing LM Studio fallback.
- [x] Implement `OllamaClient` on `/api/chat` with `stream=false` and schema/json format.
- [x] Wire client factory and doctor output.
- [x] Add tests for request payload, response parsing, and CLI settings.
- [x] Verify tests and document review.

## Review

- Added `llm_provider` settings with `lmstudio` as the default and `ollama` as an opt-in backend.
- Added `OllamaSettings`: `base_url`, `model`, optional `api_key`, generation options, `use_json_schema_response`, `repeat_penalty`, and `keep_alive`.
- Implemented `OllamaClient` using native `POST <base_url>/api/chat`, `stream=false`, `messages`, `options`, and `format` as either JSON Schema or `"json"` fallback.
- Added CLI/env selection:
  - CLI: `--llm-provider ollama --ollama-model <name>` or generic `--model <name>` when provider is Ollama.
  - Env: `IGLA_LLM_PROVIDER=ollama`, `IGLA_OLLAMA_URL`, `IGLA_OLLAMA_MODEL`, `IGLA_OLLAMA_KEY`.
- `doctor` now prints the selected provider and both backend endpoints/models.
- Added tests for Ollama request payload, schema/json format modes, HTTP errors, CLI overrides, and env loading.
- Verified with `pytest tests/`: 234 passed.
- Verified changed files with focused `ruff check`: clean.
- Verified changed source files with focused `mypy`: clean.

# Bugfix: stop read_file chunk loops

- [x] Capture the transfer-note loop pattern.
- [x] Preserve `read_file` range metadata in planner compact output.
- [x] Separate prompt excerpt truncation from tool-level truncation.
- [x] Allow `read_file(start_line=N)` to default to the next safe chunk.
- [x] Preserve original failed tool errors instead of replacing them with success-output schema errors.
- [x] Exit large-file read diagnosis after a successful chunked `read_file`.
- [x] Add regressions for compact output and start-line-only chunking.
- [x] Verify tests and document review.

## Review

- Transfer note shows repeated `read_file` calls over already-read README chunks. The planner event tail exposed content excerpts but dropped `start_line`, `end_line`, `total_lines`, and `end_of_file`, so the next LLM turn could not reliably know what had already been read.
- `_compact_tool_output(read_file)` now preserves chunk progress metadata, slice/full-file hashes, and receipt id.
- Prompt excerpt truncation is now reported as `content_excerpt_truncated`; the tool-level `truncated` flag now reflects only the real tool output. This prevents the planner from treating prompt compaction as a failed/incomplete file read.
- `read_file(start_line=N)` without `end_line` now reads the next `_LINE_LIMIT` lines on large files. A fully unbounded read on a large file still returns `FILE_TOO_LARGE`.
- Executor now skips success-output schema validation for failed tool results, so a structured `FILE_TOO_LARGE` does not get overwritten by `OUTPUT_SCHEMA_INVALID`.
- Motivation now returns from `FAILURE_DIAGNOSIS_REQUIRED` to `READY` after a successful chunked `read_file` that recovered from `FILE_TOO_LARGE`; this prevents `declare_task_done` from being incorrectly forbidden after the recovery read.
- Added regressions for compact output progress visibility, start-line-only chunk reads, failed-result executor behavior, and large-file diagnosis recovery.
- Verified with `pytest tests/`: 221 passed.
- Verified changed files with focused `ruff check`: clean.
- Verified changed source files with focused `mypy`: clean.
