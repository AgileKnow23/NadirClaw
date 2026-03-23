# AI_OPERATING_MODEL.md
**Status:** ACTIVE (binding)  
**Governs:** Claude (strategy), Codex (implementation), Cursor (editor/agent)  
**Last Updated:** 2026-02-08

---

## 0) Chain of Command (No Confusion)

**Order of authority (highest → lowest):**
1. `CODEX_CONSTITUTION.md`
2. `docs/decisions/**` (ADRs + decision logs)
3. `docs/**` (architecture, domain, ops)
4. Existing code patterns in the repo
5. Tool defaults (Codex/Cursor/Claude)

If any tool output conflicts with the above, the tool is wrong.

---

## 1) Roles (Who does what)

### Claude = Strategy + Clarity + Governance
Use Claude for:
- scoping and success criteria
- architecture tradeoffs and risk analysis
- “War Room Brief” synthesis
- drafting plans, test strategies, migration strategies

**Claude never does broad code edits by default.** It produces:
- outcome statement
- plan
- file list
- stop conditions
- acceptance tests

### Codex = Execution + Implementation
Use Codex for:
- targeted edits in specific files
- writing tests
- implementing features in small increments
- mechanical refactors *only inside the touched module*

**Codex must follow the Constitution, scope controls, and stop conditions.**

### Cursor = Workspace Operator
Use Cursor for:
- applying edits as diffs
- fast navigation/search
- running commands, tests, and quick fixes
- enforcing “edit only these files” discipline

Cursor is where we *see* the blast radius and keep it small.

---

## 2) The Standard Loop (Default Workflow)

### Gate 0 — Clarity
**Input:** request/problem  
**Output (required):**
- Outcome (1 sentence)
- Success criteria (bullets)
- Not in scope (bullets)
- Risks (top 3)
- Proposed file list

If ambiguous → STOP and clarify.

### Gate 1 — War Room Brief (when it matters)
Trigger War Room Brief when the change touches:
- architecture boundaries / DDD structure
- DB schema / migrations / RLS
- infra/deploy/observability
- UX flows or user language
- testing strategy, CI, or E2E

Output: go/no-go + risks + top recommendations.

### Gate 2 — Plan + File Lock
Codex produces a step plan and **exact files to change**.
**No implementation before file list approval.**

### Gate 3 — Implement in Intent Units
- smallest safe change
- ≤ 5 files or STOP
- ≤ 200 net LOC or STOP

### Gate 4 — Test + Proof
Run tests (unit/integration/E2E as required). Provide:
- commands executed
- outputs / screenshots if UI/E2E
- notes on verification

### Gate 5 — Diff Summary + Decision Note
- summarize what changed and why
- update decision log/notes if meaningful

Then STOP.

---

## 3) Enforcement Rules (How we prevent “308 files changed”)

### 3.1 Scope Locks (Mandatory)
Every Codex execution prompt must include:
- “Edit only these files: …”
- “If more files are required, STOP and ask.”
- “No renames / no moves / no reformatting entire files.”
- “No new dependencies unless approved.”

### 3.2 Blast Radius Thresholds
- >5 files touched → STOP
- new dependency → STOP
- ADR conflict → STOP
- unclear tenancy/RLS impact → STOP
- can’t run tests → STOP

### 3.3 Local Tidy Only
“Tidy First” is allowed only for the module being changed.
No repo-wide refactors. Ever. Without explicit approval.

---

## 4) Testing Doctrine (Operationalized)

Default: **test pyramid** (unit-heavy)  
E2E is mandatory when a change impacts **critical user flows** (auth, tenant routing, scheduling, invoicing, messaging).

Rules:
- new behavior → unit tests in domain layer
- boundary logic → integration tests
- critical flow impact → E2E (Playwright) + screenshot proof
- bug fix → regression test required

Reference: `testing-workflow.md`

---

## 5) Where Work Goes (Repo Navigation Rules)

Follow the repo map and DDD layout:
- feature work: `src/features/<context>/...`
- shared value objects: `src/shared/domain/`
- infra adapters: `src/shared/infrastructure/`
- scripts: `scripts/**`
- docs: `docs/**`
- plans: `.claude/Plans/**`

Also enforce terminology mapping (DB vs Domain vs UI).

---

## 6) Recommended “Tool Split” Patterns

### Pattern A — Feature Build (safe)
- Claude: clarify + plan + file list + acceptance tests
- Codex: implement + tests (intent units)
- Cursor: run tests, inspect diff, keep blast radius tight

### Pattern B — Bug Fix (fast)
- Claude: reproduce criteria + suspect area + regression test first
- Codex: add regression test → fix → run tests
- Cursor: verify locally + screenshot evidence if UI/E2E

### Pattern C — Architecture Change (careful)
- Claude: War Room Brief + ADR check + migration plan
- Codex: implement in small slices, each slice tested
- Cursor: enforce file locks and run migration/test commands

---

## 7) Definition of Done (DoD)

A change is DONE only when:
- outcome met
- tests added/updated and executed
- no terminology drift
- no multi-tenant/RLS violations
- diff summarized clearly
- docs/decision note updated when meaningful

---

## 8) Default Prompts Location

Store reusable prompts under:
- `docs/prompts/` (recommended)

See `PROMPT_TEMPLATES.md` for copy/paste templates.
