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
