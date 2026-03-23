#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# codex-review.sh — Codex pre-commit code review + auto-fix
# ─────────────────────────────────────────────────────────────────────────────
# Called by .git/hooks/pre-commit on staged .py files.
# 1. Reads git diff --cached (staged changes) + full staged file contents
# 2. Sends to `codex exec --json` with a review+refactor prompt
# 3. If Codex returns fixes: writes the fixed files back, re-stages them
# 4. Commit proceeds automatically with the cleaned-up code
#
# Bypass: SKIP_CODEX_REVIEW=1 git commit  OR  git commit --no-verify
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# Skip if disabled
if [ "${SKIP_CODEX_REVIEW:-0}" = "1" ]; then
    echo "[codex-review] Skipped (SKIP_CODEX_REVIEW=1)"
    exit 0
fi

# Check if codex CLI is available
if ! command -v codex &>/dev/null && ! command -v codex.cmd &>/dev/null; then
    echo "[codex-review] Codex CLI not found — skipping review"
    exit 0
fi

# Resolve codex command (Windows needs .cmd extension sometimes)
CODEX_CMD="codex"
if command -v codex.cmd &>/dev/null && ! command -v codex &>/dev/null; then
    CODEX_CMD="codex.cmd"
fi

# Get staged Python files
STAGED_PY_FILES=$(git diff --cached --name-only --diff-filter=ACM -- '*.py')

if [ -z "$STAGED_PY_FILES" ]; then
    echo "[codex-review] No staged .py files — skipping review"
    exit 0
fi

echo "[codex-review] Reviewing staged Python files:"
echo "$STAGED_PY_FILES" | sed 's/^/  - /'

# Get the diff (truncated to ~8000 chars to avoid token overflow)
DIFF=$(git diff --cached -- '*.py' | head -c 8000)

if [ -z "$DIFF" ]; then
    echo "[codex-review] No diff content — skipping"
    exit 0
fi

# Read CODEX_CONSTITUTION principles (if available at repo root)
CONSTITUTION=""
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")
if [ -f "$REPO_ROOT/CODEX_CONSTITUTION.md" ]; then
    # Take first 2000 chars of the constitution for context
    CONSTITUTION=$(head -c 2000 "$REPO_ROOT/CODEX_CONSTITUTION.md")
fi

# Build the review prompt
REVIEW_PROMPT="You are a senior code reviewer enforcing project code quality standards.

Review the following staged git diff and fix any issues. Return ONLY the corrected Python code for each file that needs changes. If a file is fine, do not include it.

For each file that needs fixes, output in this exact format:
--- FILE: <filepath> ---
<complete corrected file content>
--- END FILE ---

If no files need changes, output exactly: NO_CHANGES_NEEDED

Code quality rules to enforce:
- Clean, readable code with meaningful names
- Single responsibility, small functions
- No hidden side effects
- Proper error handling (but don't over-engineer)
- No security vulnerabilities (injection, XSS, etc.)
- Consistent style with the rest of the codebase
${CONSTITUTION:+
Project constitution principles (excerpt):
$CONSTITUTION}

DIFF:
$DIFF"

echo "[codex-review] Sending to Codex for review..."

# Call Codex CLI
CODEX_OUTPUT=$($CODEX_CMD exec --json -q "$REVIEW_PROMPT" 2>/dev/null || echo "CODEX_ERROR")

if [ "$CODEX_OUTPUT" = "CODEX_ERROR" ]; then
    echo "[codex-review] Codex CLI failed — allowing commit to proceed"
    exit 0
fi

# Parse JSON output (extract the text content)
# codex exec --json returns {"output": "..."}
REVIEW_TEXT=$(echo "$CODEX_OUTPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Handle various codex output formats
    if isinstance(data, dict):
        print(data.get('output', data.get('text', data.get('content', ''))))
    elif isinstance(data, str):
        print(data)
    else:
        print(str(data))
except:
    print(sys.stdin.read() if hasattr(sys.stdin, 'read') else '')
" 2>/dev/null || echo "$CODEX_OUTPUT")

# Check if no changes needed
if echo "$REVIEW_TEXT" | grep -q "NO_CHANGES_NEEDED"; then
    echo "[codex-review] No issues found — commit proceeding"
    exit 0
fi

# Check if there are file fixes to apply
if ! echo "$REVIEW_TEXT" | grep -q "^--- FILE:"; then
    echo "[codex-review] No actionable fixes — commit proceeding"
    exit 0
fi

# Apply fixes
echo "[codex-review] Applying Codex fixes..."
FIXED_COUNT=0

# Extract and write each fixed file
while IFS= read -r filepath; do
    # Extract content between --- FILE: <path> --- and --- END FILE ---
    FILE_CONTENT=$(echo "$REVIEW_TEXT" | sed -n "/^--- FILE: $filepath ---$/,/^--- END FILE ---$/p" | sed '1d;$d')

    if [ -n "$FILE_CONTENT" ] && [ -f "$filepath" ]; then
        echo "  [fix] $filepath"
        echo "$FILE_CONTENT" > "$filepath"
        git add "$filepath"
        FIXED_COUNT=$((FIXED_COUNT + 1))
    fi
done < <(echo "$REVIEW_TEXT" | grep "^--- FILE:" | sed 's/^--- FILE: //;s/ ---$//')

if [ "$FIXED_COUNT" -gt 0 ]; then
    echo "[codex-review] Applied $FIXED_COUNT fix(es) and re-staged"
else
    echo "[codex-review] No fixes applied — commit proceeding"
fi

exit 0
