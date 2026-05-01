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
