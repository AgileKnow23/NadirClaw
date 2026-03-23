# PROMPT_TEMPLATES.md
**Status:** ACTIVE (copy/paste library)  
**Last Updated:** 2026-02-08

> Use these templates with Codex (or Cursor agent).  
> Always attach/mention `CODEX_CONSTITUTION.md` and enforce file locks.

---

## Template 0 — Universal Header (prepend to every prompt)

**Paste this at the top of every prompt:**

- Follow `CODEX_CONSTITUTION.md` exactly.
- Do not refactor unrelated code.
- Do not rename/move files.
- Do not add dependencies.
- Edit only the approved files.
- If more files are needed, STOP and ask.
- Provide a step-by-step plan and file list BEFORE implementing.
- Provide proof (tests run / output / screenshots) before claiming done.

---

## Template 1 — Surgical Bug Fix (with regression test)

**Use when:** a bug exists and you want a minimal safe fix.

**Prompt:**
1) Outcome: Fix [bug] so that [expected behavior].  
2) Success:  
   - Repro fails before fix, passes after fix  
   - Regression test added  
3) Not in scope: refactors, renames, dependency changes  
4) Approved files:  
   - [file1]  
   - [file2]  
5) Repro steps / error logs:  
   - [paste logs]  

**Instructions:**
- Step 1: Write the failing regression test (red).
- Step 2: Make the smallest code change to pass (green).
- Step 3: Local tidy only (refactor) inside the touched module.
- Step 4: Run tests and report commands + results.

---

## Template 2 — New Feature (bounded context, small slice)

**Use when:** building a feature without blowing up the repo.

**Prompt:**
Outcome: [1 sentence]  
Success:
- [measurable criteria]
- [UX criteria if relevant]
Not in scope:
- [explicit exclusions]
Constraints:
- Follow DDD boundaries under `src/features/<context>/`
- Terminology mapping: DB `contacts` / domain `Customer` / UI “Customers”
Approved files (initial):
- [list]

**Instructions:**
- Provide plan + file list.
- Implement the smallest end-to-end vertical slice.
- Add unit tests for domain behaviors.
- If this touches a critical user flow, add E2E tests (Playwright) + screenshots.
- Stop after first slice with verification results.

---

## Template 3 — Refactor (local tidy only)

**Use when:** you must tidy an area before adding behavior.

**Prompt:**
Outcome: Improve clarity in [module] so that adding [new behavior] is safe and clean.  
Non-goals: No behavior change. No API changes. No new deps.  
Approved files:
- [list]

**Instructions:**
- Provide refactor plan in 3–7 steps.
- Each step must be independently safe.
- Run tests after each step (or at least after the final step if truly trivial).
- Summarize net diff and why it reduced complexity (change amplification/cognitive load).

---

## Template 4 — Database Migration (additive + RLS)

**Use when:** adding/modifying schema.

**Prompt:**
Outcome: Add schema support for [feature].  
Success:
- Additive migration (no destructive drops)
- RLS policies updated/created
- Backward compatible deploy steps documented
Not in scope:
- Renaming tables
- Large data backfills unless explicitly required
Approved files:
- scripts/migrations/[...]
- docs/schema-migration.md (if applicable)
- [any SQL files]

**Instructions:**
- Propose migration steps: add → backfill (if needed) → constraints/indexes → RLS.
- Call out locking/rollback risks.
- Provide verification: migration applied in test env + relevant integration tests.

---

## Template 5 — UX/Copy Pass (jargon strike)

**Use when:** reviewing UI text and flows.

**Prompt:**
Outcome: Make this flow understandable for a busy service business owner on mobile.  
Success:
- Pass Krug “don’t make me think” test
- Remove jargon
- Reduce steps/cognitive load
Approved files:
- [component files]

**Instructions:**
- List top UX issues (critical vs recommended).
- Provide a minimal change set.
- Include before/after copy samples.
- Provide screenshots if possible.

---

## Template 6 — “War Room Brief” Request (pre-plan)

**Use when:** you’re about to do anything risky.

**Prompt:**
Change/Plan: [describe in 3–6 lines]  
Ask: Run a War Room Brief with:
- Clarity/Strategy
- Architecture
- DBA (if data)
- Infra (if deploy/ops)
- UX (if user flow)
- Testing

Output required:
- GO / NEEDS WORK / NO-GO
- top risks
- top recommendations
- file-scope guidance

---

## Template 7 — PR Summary (PR per Intent Unit)

**Use when:** preparing a PR description.

**Prompt:**
Generate a PR description with:
- Outcome + success criteria
- What changed (bullets)
- Tests run (commands + results)
- Screenshots (if UI/E2E)
- Risks + mitigations
- Follow-ups (if any)
