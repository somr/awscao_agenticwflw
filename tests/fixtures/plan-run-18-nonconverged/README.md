# Fixture: non-converged planning run (plan-PAY-DEMO-001-18)

Real agent output from a live run on 2026-09-19 (workflow `sdlc_dev_plan`, baseline `a806a98`,
`max_review_rounds=3`). The independent reviewer returned `CHANGES_REQUIRED` in all three rounds
(5, 6 and 4 findings; the last four were LOW). No plan was published.

Kept as test data for the non-convergence work in `docs/plans/planning-guidance-and-warm-start.md`
(candidate snapshot, guidance, warm start). Copied from the git-ignored
`.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-18/`; agent-output evidence and the two
superseded plans (`plan-r1.md`, `plan-r2-c1.md`) were left out.

Not sanitized: `context/raw/retrieval.json` and `planning/plan-r3-c1.md` contain absolute paths from
the original machine. Tests must not depend on them.
