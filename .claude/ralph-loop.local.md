---
active: true
iteration: 2
session_id: 
max_iterations: 600
completion_promise: "PROJECT_COMPLETE"
started_at: "2026-05-03T05:23:23Z"
---

Read PROMPT.md and TODO.md. Execute the FIRST unchecked task in TODO.md per the rules in PROMPT.md (TDD slice, ruff+pytest, machine-checkable acceptance, atomic ticking). At phase boundaries, run the Phase Boundary Protocol from PROMPT.md (push, pr-create, coderabbit:coderabbit-review, codex review via external-cli-orchestrator subagent, fix HIGH/CRITICAL findings as follow-up commits, gh pr merge --squash --delete-branch, then start the next phase). Only emit <promise>PROJECT_COMPLETE</promise> when the three conditions in TODO.md's 'Project completion' section are all true. Never push to main. Never lie to exit.
