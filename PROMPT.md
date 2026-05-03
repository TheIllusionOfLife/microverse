# Ralph-loop Build Prompt — Microverse Battery

This file is read every ralph iteration. The full plan lives at
`~/.claude/plans/use-gemma4-e4b-via-ollama-staged-umbrella.md` — consult it
when in doubt about *why* something is the way it is.

## Mission
Build a long-running multi-agent simulation ("Microverse Battery") that runs
autonomously for weeks on local hardware at zero marginal cost, using
**only** `gemma4:e4b` via local Ollama. Inhabitants live in-world; a single
out-of-world Harvester ferries the best artifacts to `harvest/inbox/`.

## Iteration loop (do this every iteration)

1. **Read `TODO.md`.** Find the FIRST unchecked `[ ]` item, top to bottom.
   That is your task this iteration.
2. **Execute the task.** TDD slice when applicable:
   - Write a failing test, commit (`test: red — <slice>`).
   - Implement, commit (`feat: green — <slice>`).
   - Refactor if needed, commit (`refactor: <slice>`).
   - Slices may be combined into one commit when small (≤ ~30 lines).
3. **Verify.** Run all of:
   - `uv run ruff check`
   - `uv run ruff format --check`
   - `uv run pytest -q`
   If any fail, do NOT tick the item. Fix and repeat step 2.
4. **Run the task's acceptance command.** Each TODO item lists an
   `**Acceptance**:` shell command and `**Expected**:` output snippet.
   If actual output matches, paste it under `**Evidence**:` with timestamp
   and tick `[ ] → [x]`. If not, do NOT tick.
5. **Phase boundary?** When all items in the current `## Phase ...` section
   are ticked, do the **Phase Boundary Protocol** (below).
6. **Project boundary?** When Phase 4b is merged AND its 24h soak rung is
   recorded as PASS in TODO.md, emit:

   ```
   <promise>PROJECT_COMPLETE</promise>
   ```

   Otherwise, end this iteration normally — ralph will re-feed the prompt.

## Phase Boundary Protocol (autonomous)

1. Final verification: `uv run ruff check && uv run ruff format --check && uv run pytest -q`. Any failure → keep iterating in this phase, do NOT cross the boundary.
2. `git push -u origin <current-branch>`.
3. Open PR via the `pr-create` skill. Title: `Phase <N>: <slug>`. Body: links to TODO.md phase header + summary of acceptance commands run.
4. Run TWO AI reviewers on the PR diff:
   - Invoke `coderabbit:coderabbit-review` and capture findings.
   - Spawn `external-cli-orchestrator` subagent: have it run `codex exec` on the PR diff with a severity-tagged punch list prompt.
5. Triage:
   - **CRITICAL / HIGH** → fix in follow-up commits to the same PR. Do NOT open a new PR.
   - **MEDIUM / LOW** → fix unless cost > value. Justify skip in a one-line PR comment.
6. Re-run reviewers after fixes. Loop until both report no CRITICAL/HIGH.
7. Merge: `gh pr merge --squash --delete-branch`. Then `git checkout main && git pull`.
8. In TODO.md, record under the phase header:
   ```
   **MERGED**: <commit-sha> at <ISO8601 timestamp>
   ```
9. Start the next phase: `git checkout -b feat/phase-<next>-<slug>` (slugs in TODO.md).

## Hard rules

- Branch discipline: **never push to main**, always feature branch.
- Single PR open at a time: previous phase's PR must be merged before next phase's branch is created.
- Single model: only `gemma4:e4b` via Ollama. No other models pulled, no API calls.
- Thinking off: callers of `microverse.llm.ollama_client.chat` must never see thinking tokens. Phase 0 verifies the mechanism (`think=False` per official Ollama API + `strip_thinking()` defense in depth).
- TDD: every test you write must first fail, then pass. Git history must show the red state.
- Lying to exit (emitting `<promise>PROJECT_COMPLETE</promise>` when it isn't true) is forbidden.
- `git rebase -i`, `git push --force`, `git reset --hard`, `--no-verify`: forbidden unless explicitly requested by the user.
- Skip hooks: forbidden.

## Tools you should reach for

- `uv` for Python deps + venv (`uv add`, `uv run`).
- `ruff` for lint + format (`uv run ruff check`, `uv run ruff format`).
- `pytest` for tests (`uv run pytest -q`).
- `gh` for PR ops.
- `pr-create` skill for opening PRs.
- `coderabbit:coderabbit-review` skill for review.
- `external-cli-orchestrator` subagent for codex review.
- Ollama HTTP at `http://localhost:11434` (already running as backgrounded task `b6w2mwmvt`).
