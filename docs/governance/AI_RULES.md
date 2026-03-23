# AI_RULES.md
**Last Updated:** 2026-02-08
**Purpose:** One-page “what to do every time” for any AI tool.

## Hard Gates (must obey)
- Follow `CODEX_CONSTITUTION.md`
- Edit only approved files
- If more files are needed, STOP and ask
- No renames/moves/reformats unless requested
- No new dependencies unless approved
- No “done” without proof (tests/output/screenshots)

## Default Sequence
Clarity → Plan → (War Room if needed) → Implement → Test → Verify → Summarize → Stop

## Stop Conditions
STOP immediately if:
- >5 files would be touched
- ADR conflict appears
- tenancy/RLS could break
- tests can’t be run / verification unclear
