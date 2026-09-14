# Planning Workflow 1 — CAO Python workflow (v1.3)

`dev_plan.py` orchestrates the planning phase using deterministic macro-orchestration and read-only Claude Code specialists.

## v1.3 completion stabilization

CAO 2.5.0 can transiently report a Claude Code worker as `COMPLETED` while the interactive TUI is still working. The workflow therefore calls every agent step with:

```python
teardown=False
```

and applies a workflow-owned stabilization boundary before accepting the returned text.

The boundary:

1. leaves the CAO-created terminal alive after the first `COMPLETED`;
2. waits 5 seconds;
3. polls the public CAO terminal and output APIs;
4. rejects `[NO RESPONSE ...]`, `[PARTIAL RESPONSE ...]`, live spinner/TUI output, and interactive-wait/error states;
5. requires the extracted candidate answer to be identical for consecutive stable polls;
6. writes stabilization evidence;
7. explicitly exits and deletes the terminal.

No CAO source or installed package is modified.

## Inputs

- `ticket_id` — Jira-style ticket identifier, e.g. `PAY-DEMO-001`.
- `repository_root` — absolute path to the repository under analysis.
- `source_dir` — directory containing the mocked Jira/Confluence package.
- `baseline_sha` — Git commit SHA that the human is asking the workflow to plan against.
- `base_branch` — defaults to `main`.
- `max_review_rounds` — defaults to 3; valid range 1–10.

`baseline_sha` remains an explicit workflow input so CAO replay cannot silently shift the repository planning baseline.

## Install

Start `cao-server` first:

```bash
cao-server
```

Then, from the application repository root:

```bash
.agentic-sdlc/cao/workflows/install.sh
```

The installer stages the Python workflow inside CAO's permitted workflow directory before server-side validation because CAO intentionally rejects validation paths outside that directory.

Ensure the four profiles are installed:

```bash
cao profile show sdlc_context_normalizer
cao profile show sdlc_planning_analyst
cao profile show sdlc_plan_author
cao profile show sdlc_plan_reviewer
```

## Run

Use a new run ID after each workflow-source change:

```bash
RUN_ID=plan-PAY-DEMO-001-7
BASELINE_SHA=$(git rev-parse --verify HEAD)

cao workflow run dev_plan \
  --run-id "$RUN_ID" \
  --input ticket_id=PAY-DEMO-001 \
  --input repository_root="$(pwd)" \
  --input source_dir="$(pwd)/.agentic-sdlc/examples/PAY-DEMO-001" \
  --input baseline_sha="$BASELINE_SHA" \
  --input base_branch=main \
  --input max_review_rounds=3
```

Useful run commands:

```bash
cao workflow status "$RUN_ID"
cao workflow events "$RUN_ID" --follow
cao workflow result "$RUN_ID"
cao workflow cancel "$RUN_ID"
```

## Machine-readable boundaries

Context Normalizer and Plan Reviewer must return strict JSON. v1.3 distinguishes two failure classes:

```text
incomplete/live agent execution
    → IncompleteAgentExecutionError
    → no JSON repair

stable completed response + invalid JSON/shape
    → one bounded contract-repair turn
    → strict deterministic validation again
```

This prevents CAO/TUI lifecycle defects from being misdiagnosed as model serialization defects.

## Evidence

Detailed evidence remains Git-ignored under:

```text
.agentic-sdlc/runtime/<ticket>/<run-id>/
```

Agent-output directories contain `.initial.txt`, `.final.txt`, `.stabilization.json`, and machine-output `.raw.txt` files where applicable.

A successful planning run publishes:

```text
.agentic-sdlc/records/<ticket>/
    development-plan.md
    plan-review.json
    execution-manifest.json
```

The business outcome is `AWAITING_HUMAN_APPROVAL`; the workflow itself never approves the plan.
