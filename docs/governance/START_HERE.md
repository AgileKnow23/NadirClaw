# START_HERE.md
**Last Updated:** 2026-02-08

This repo is governed by three documents:

1. `CODEX_CONSTITUTION.md` — the law (non-negotiable rules)
2. `AI_OPERATING_MODEL.md` — how Claude/Codex/Cursor work together
3. `docs/prompts/` — copy/paste prompt spells

## 1) Install the Law (required)
Place these at the repo root:
- `CODEX_CONSTITUTION.md`
- `AI_OPERATING_MODEL.md`
- `PROMPT_TEMPLATES.md` (optional root copy; canonical lives in `docs/prompts/`)

## 2) Standard Workflow (every task)
1. Write Outcome + Success + Not-in-scope + Risks + File list
2. (If risky) run War Room Brief
3. Lock file scope (≤ 5 files) **before** implementing
4. Implement in “intent units”
5. Run tests + provide proof
6. Summarize diff + update decision log if meaningful

## 3) Quick Command Checklist (copy/paste into PRs)
- [ ] Outcome stated + scope locked
- [ ] ≤ 5 files touched (or explicit approval)
- [ ] No new deps (or explicit approval)
- [ ] Tests added/updated + executed
- [ ] E2E added if critical flow impacted
- [ ] Diff summary included
- [ ] Decision note updated if needed

## 4) Where to put what
- Product/architecture decisions: `docs/decisions/` and `docs/adr/`
- Prompts: `docs/prompts/`
- AI governance: `docs/ai/`
- Plans/missions: `.claude/Plans/`
