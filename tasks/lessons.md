# Lessons

## 2026-04-30: Local discovery before clarification

When the user asks to open/read/find a file, IGLA must first use local
workspace discovery tools (`find_files`, `search_text`, later list/index tools)
before asking the user for a path. User clarification is reserved for zero
matches, ambiguous equivalent matches, policy/TODO conflicts, or hard runtime
failure.
