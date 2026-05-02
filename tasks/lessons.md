# Lessons

## 2026-04-30: Local discovery before clarification

When the user asks to open/read/find a file, IGLA must first use local
workspace discovery tools (`find_files`, `search_text`, later list/index tools)
before asking the user for a path. User clarification is reserved for zero
matches, ambiguous equivalent matches, policy/TODO conflicts, or hard runtime
failure.

## 2026-04-30: Linux-first target

IGLA is developed for Linux first. Architecture, sandboxing, process
supervision, path policy, and runtime behavior should optimize for Linux as
the primary target. Windows compatibility is secondary and should not drive
core design unless the user explicitly asks for it.

## 2026-04-30: Reset poisoned runtime state

The debug CLI must expose an explicit state-reset path for a workspace.
If `.igla` contains poisoned events/TODO snapshots, the user should not need
manual filesystem cleanup to recover the runtime.

Tool version references from local models may include a harmless leading `v`
(`v1.0.0`). Registry lookup should normalize such references at the boundary
instead of turning an existing tool into `UNKNOWN_TOOL`.

## 2026-04-30: User-contact gates must cover every channel

Autonomy rules cannot protect only `ask_user_clarification`. Any direct
user-contact tool, especially `tool:ask_user`, must pass through the same
policy gate. Otherwise the model can bypass the intended architecture and
ask malformed questions before local discovery.

Generic read-only actions such as `noop_observe` must not unlock
clarification. Only real workspace discovery tools (`find_files`,
`search_text`, `read_file`, or tools with explicit discovery capabilities)
can make a later clarification legitimate.

## 2026-04-30: Planner must see tool results, not just event keys

A tool invocation is useless to the model if the next prompt only says
`output_keys`. Discovery tools must publish compact, prompt-safe evidence
(`count`, `matches`, `relative_path`, snippets) into the event tail. Otherwise
the model can run `find_files`, still not know what was found, and ask the
user for a path that the runtime already discovered.

If discovery returns one clear result, user contact must be denied. The next
valid move is to use the discovered path (`read_file`) or finish the task.

## 2026-04-30: Missing action is malformed, not clarification

PlannerProposal's `action` discriminator must be required in every schema
variant. Defaults on discriminator fields can make structured-output engines
treat `{"reason": "...", "question": "..."}` as valid, which lets a weak
model bypass the intended action grammar.

The parser must not infer `ask_user_clarification` from a bare `question`.
Only the narrow missing-action `tool_invocation` shape is safe to repair
because it still goes through ToolRegistry and PolicyEngine.

## 2026-04-30: Proven tool results need deterministic stop gates

Do not spend another LLM turn when a read-only tool result already satisfies
the user's simple goal. For plain file-location requests, `find_files` with a
clear primary match is enough to complete the task; sending that result back
to a weak local model invites irrelevant searches, unknown tools, or malformed
questions.

This must remain post-tool orchestration, not ad-hoc CLI command routing. The
planner still chooses the discovery tool, but runtime may close the loop after
evidence is available. Open/read/show/inspect style requests are different:
finding the file is only an intermediate result and must continue toward
`read_file`.

Debug CLI input is part of the runtime contract. If a terminal leaks
backspace/delete or ANSI control bytes into stdin, sanitize the line before it
becomes a task goal; otherwise stale deleted text poisons the planner state.

## 2026-05-01: Capability taxonomies need explicit negative cases

Do not rely only on broad tool capabilities such as `fs.read` when enforcing
policy gates. `read_file` is workspace discovery, but `verify_file` is
post-work validation even though it reads a file. `read_task_log` is memory
inspection, not workspace discovery. Policy predicates need explicit
non-discovery exclusions and regression tests for tools that are read-only but
do not help the model find missing information.

When tests assert file hashes or backup bytes, write fixture files as bytes or
force LF newlines. Otherwise Windows CRLF translation can hide platform
assumptions in a Linux-first project.

## 2026-05-01: Preserve progress metadata separately from prompt compaction

Do not conflate prompt-size compaction with tool semantics. If a tool returns
`truncated=false`, the compact event tail must not rewrite it to `true` merely
because the prompt excerpt was shortened. Use a separate field such as
`content_excerpt_truncated` for prompt-only shortening.

For iterative tools, always carry the progress cursor in the compact output.
For `read_file`, the planner must see `start_line`, `end_line`, `total_lines`,
and `end_of_file` after every successful read. Dropping those fields makes a
weak local model reread the same ranges and look like it is "thinking" when the
runtime actually removed the state it needed to advance.

Tool APIs should be tolerant at the boundary when that preserves safe progress.
For large-file reading, `read_file(start_line=N)` should mean "read the next
safe chunk" instead of failing solely because `end_line` was omitted.

Executor validation must distinguish success payload schemas from failure
envelopes. A tool returning `status=failed` with a structured `error.code`
should keep that original error; validating its empty output against the
success schema rewrites useful causes like `FILE_TOO_LARGE` into generic
`OUTPUT_SCHEMA_INVALID` and makes the planner diagnose the wrong thing.

Failure-diagnosis mode needs explicit recovery exits for known recoverable
planning errors. A large-file unbounded read followed by a successful chunked
`read_file` should return the task to `READY`; otherwise `declare_task_done`
stays forbidden after the model has already corrected its read strategy.

## 2026-05-01: File creation/copy is not file patching

Do not force a local model to synthesize full file contents when the desired
operation is structural: copy file A to file B, append a small suffix, move a
file, or create a new file from an existing source. Those operations need
dedicated tools that move bytes inside the runtime. Using `patch_file` with
`new_content` for a large copy is both inefficient and unsafe.

`patch_file` should remain an existing-file editor with read-before-write and
hash-before-patch invariants. A non-existent destination cannot satisfy a prior
`read_file` receipt, so creation/copy flows need a separate tool such as
`copy_file` with explicit destination-exists behavior and rollback for
overwrites.

## 2026-05-01: Keep LLM backends swappable

The planner should depend on a narrow `LLMClient` protocol, not on one local
server implementation. LM Studio's OpenAI-compatible chat endpoint is useful
as a fallback, but native backends such as Ollama may expose different and
more reliable structured-output controls.

Backend selection must be explicit in config/CLI/env, and model names must be
opaque strings owned by the backend. For Ollama, accept any model tag the user
can see in `ollama list`; do not hard-code a local model catalog in IGLA.

## 2026-05-02: Failure diagnosis exits must cover corrected planner mistakes

Do not treat every `FILE_NOT_FOUND` as a changed-world failure. If the planner
used a bad path, then a later successful `find_files` result or corrected
`read_file` is evidence that the failure was a planner-path error and no
world mutation is required to recover. Return the task to `READY`; otherwise
`patch_file` stays forbidden while the only old generic exit requires a
successful patch, creating a policy deadlock.

Runtime debug logging is part of the control surface for remote/local model
testing. A `-rtlog` path must be tested like any other CLI behavior, because
uncopyable or partial logs hide prompt bloat, malformed model proposals, and
looping tool choices from diagnosis.
