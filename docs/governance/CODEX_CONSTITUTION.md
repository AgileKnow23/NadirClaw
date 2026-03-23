# CODEX_CONSTITUTION.md
**Status:** ACTIVE (binding)  
**Applies to:** Codex, Claude, Cursor, Copilot, Humans  
**Default posture:** Surgical changes, minimal impact, verified outcomes.

---

## 0. The Prime Directive (Non‑Negotiable)

**We build a multi-tenant service-business operating system.**  
Everything we do must reduce complexity, preserve optionality, and ship verified value.

If any instruction conflicts with another, follow this precedence order:

1. **This Constitution**
2. **docs/decisions/** (ADR decisions)
3. **docs/** architecture + domain docs
4. **Existing code patterns in repo**
5. **Tool defaults (Codex/Cursor/Claude)**

If you are unsure, STOP and ask.

---

## 1. Outcome First (Clarity Officer Law)

Before any non-trivial change (3+ steps, new files, architecture decisions), you MUST produce:

### Outcome Statement
- **Outcome:** one sentence describing what we are building/fixing
- **Success Criteria:** measurable bullets
- **NOT in scope:** explicit exclusions
- **Risks:** top 3 risks
- **Proposed Files:** exact list of files you intend to change

**You may not implement until the file list is confirmed.**

Reference philosophy: `CLAUDE.md` and War Room doctrine.

---

## 2. Plan Mode Default (Execution Protocol)

For any task that is not a trivial single-file edit:

### REQUIRED Workflow
1. **Plan**
2. **War Room Brief** (if architecture/UX/data/infra changes)
3. **Implement (smallest safe step)**
4. **Test**
5. **Verify**
6. **Summarize diff + reasoning**
7. **Stop**

**Never skip verification.**  
**Never claim “done” without proof.**

---

## 3. Scope Control & Change Discipline (Anti-Chaos Law)

### 3.1 Minimal Impact Rule
Only touch what is necessary to accomplish the outcome.

- ❌ No drive-by refactors
- ❌ No mass renames
- ❌ No file moves unless requested
- ❌ No “cleanup” commits that aren’t tied to the outcome
- ❌ No formatting entire files unless required

### 3.2 File Explosion Stop Condition
If the change affects **more than 5 files** (or 200 lines net change), STOP and ask for approval.

### 3.3 Refactor Containment Rule
If you must refactor, refactor **only in the module you are currently changing**.

Kent Beck principle: **Tidy First** (local tidying only).

---

## 4. Architecture Law (DDD + Clean Architecture)

### 4.1 Dependency Direction Rule
**Dependencies point inward.**

- Domain must not depend on Supabase, HTTP, UI, frameworks.
- Infrastructure depends on domain.
- UI depends on application boundaries (api.ts / hooks), not raw DB.

### 4.2 Deep Module Rule (PoSD)
Prefer **deep modules**:
- simple interface
- powerful implementation
- complexity hidden

Avoid shallow modules that leak implementation details.

### 4.3 Bounded Context Rule
All work must respect bounded contexts under:

```
src/features/<context>/
```

Cross-context interaction should be explicit (events, adapters, shared abstractions).

### 4.4 No Framework Worship
Frameworks are tools, not architecture.  
Do not design the system around React/Supabase limitations.

---

## 5. Terminology & Semantic Layer Law (CRITICAL)

**We have a strict semantic mapping between DB, Domain, and UI.**

### 5.1 Database Layer
- uses `contacts`, `contact_id`, snake_case columns

### 5.2 Domain/Code Layer
- uses `Customer`, `customerId`, camelCase

### 5.3 UI Layer
- always says **Customer(s)**
- never says “Lead(s)”
- never leaks “contacts table” language

### 5.4 Forbidden Types
- ❌ Do not create `Lead` domain types.
- ❌ Do not rename the `contacts` DB table.
- ❌ Do not introduce UI language drift.

Reference: repo memory and ADR-001.

---

## 6. Data & Multi-Tenant Law (RLS Discipline)

### 6.1 Tenant Isolation is Sacred
Every tenant-scoped table must include:

- `id`
- `tenant_id`
- `created_at`
- `updated_at`
- `deleted_at` (if soft delete applies)

### 6.2 RLS Required
Every table must have Row Level Security policies enforcing tenant isolation.

### 6.3 Soft Delete Default
Hard deletes are forbidden unless explicitly approved.

---

## 7. Testing & Verification Law

### 7.1 The Pyramid (Default)
- **Unit tests**: ~80% (domain behaviors)
- **Integration tests**: ~15% (repositories, Supabase boundaries)
- **E2E tests**: ~5% (critical user flows)

### 7.2 Beyoncé Rule
If it matters, it gets a test.

### 7.3 Regression Rule
Every bug fix must add a regression test reproducing the bug.

### 7.4 E2E Gate (Critical Flow Rule)
E2E tests are REQUIRED when:
- new page/workflow is introduced
- a critical user path is changed
- authentication, tenant routing, scheduling, invoicing, messaging flows are touched

If the feature is UI-only or minor, E2E may be optional but must be justified.

Reference: `testing-workflow.md`.

### 7.5 Proof Before Done
Before claiming completion, provide:
- test command run (or CI output)
- screenshots for UI/E2E where relevant
- explanation of verification performed

---

## 8. Dependency Policy (Minimal Dependencies)

### 8.1 Default: No New Libraries
Adding dependencies is forbidden unless:
- there is a strong justification
- the dependency is widely adopted and stable
- it reduces complexity more than it adds

If a new dependency is required, STOP and ask approval.

---

## 9. Code Quality Law (Clean Code)

### 9.1 Readability Wins
Code is read more than written.

- meaningful names
- small functions
- single responsibility
- minimal parameters
- no hidden side effects

### 9.2 Error Design
Prefer defining errors out of existence:
- idempotent operations
- safe no-ops
- typed domain errors

Avoid throwing exceptions for normal cases.

---

## 10. Documentation & Decisions Law

### 10.1 ADR Respect
If a decision exists in `docs/adr/`, follow it. Do not “re-decide” silently.

### 10.2 Comments are for Abstraction
Comments explain:
- why
- invariants
- constraints
- edge cases

Not what the code already says.

### 10.3 Document Outcomes
For any meaningful change, update:
- docs/decisions/DECISION_LOG.md (if present)
- or add a short note in the relevant docs section

---

## 11. War Room Invocation Law (Quality Council)

Before planning or implementing changes that affect:
- architecture boundaries
- database schema
- infra/deploy
- UX/UI flows
- testing strategy
- marketing copy / positioning

Run a **War Room Brief** (or consult the relevant war room agent).

War Rooms:
- Clarity/Strategy
- Architecture
- DBA
- Infra
- UX
- Testing
- Marketing

Reference: war room docs in repo.

---

## 12. Commit / PR Discipline (PR per Intent Unit)

### 12.1 Small Changes
Prefer <200 lines per PR when possible.

### 12.2 One Intent Per PR
Each PR should represent one coherent outcome.

No “misc fixes” mixed into feature work.

---

## 13. Stop Conditions (When You MUST Halt)

STOP immediately if:
- the task requires touching more than 5 files
- the change requires new dependencies
- the change conflicts with an ADR
- you discover architecture ambiguity
- multi-tenant isolation could be broken
- you are uncertain where the change belongs
- tests cannot be executed or verification is unclear

When stopped, report:
- what you found
- what decision is needed
- recommended next step

---

## 14. Definition of Done (DoD)

A change is only “DONE” when:

- Outcome achieved
- Tests added/updated
- Tests executed successfully (or CI evidence)
- No terminology drift
- No multi-tenant leaks
- UX validated (when applicable)
- Documentation updated (when applicable)
- Minimal impact preserved
- Diff summarized clearly

---

## Final Reminder

**Codex is an apprentice engineer, not an architect.**  
It must operate inside the boundaries of this constitution.

If a solution requires “cleverness,” pause and ask:

> “Is there a more elegant way?”

Then implement the elegant version.

---

**Last Updated:** 2026-02-08
