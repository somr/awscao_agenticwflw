# Planning Workflow 1 — CAO Python workflow

`dev_plan.py` is the executable orchestration for the planning phase.

It intentionally uses a **local fixture retrieval adapter** in this first version. The retrieval boundary is deterministic; everything after retrieval is already production-shaped.

## Inputs

- `ticket_id` — Jira-style ticket identifier, e.g. `PAY-DEMO-001`.
- `repository_root` — absolute path to the repository under analysis.
- `source_dir` — directory containing the mocked Jira/Confluence package.
- `baseline_sha` — Git commit SHA that the human is asking the workflow to plan against.
- `base_branch` — defaults to `main`.
- `max_review_rounds` — defaults to 3; valid range 1–10.

`baseline_sha` is an explicit workflow input rather than being discovered during execution. CAO journals inputs and replays them on resume, so this prevents a repository change between the original run and a resume from silently changing the approved planning baseline.

## Install

Start `cao-server` first; workflow validation is server-backed:

```bash
cao-server
```

Then, from the application repository root in another terminal:

```bash
.agentic-sdlc/cao/workflows/install.sh
```

The installer does **not** ask CAO to validate the repository-local path directly. It stages the file inside CAO's permitted workflow directory first, because the server intentionally rejects workflow-spec paths that escape that directory.

Ensure the four profiles are already installed:

```bash
cao profile show sdlc_context_normalizer
cao profile show sdlc_planning_analyst
cao profile show sdlc_plan_author
cao profile show sdlc_plan_reviewer
```

## Run the synthetic example

Start `cao-server` in another terminal, then from the repository you want the agents to inspect:

```bash
RUN_ID=plan-PAY-DEMO-001-1
BASELINE_SHA=$(git rev-parse HEAD)

cao workflow run dev_plan \
  --run-id "$RUN_ID" \
  --input ticket_id=PAY-DEMO-001 \
  --input repository_root="$(pwd)" \
  --input source_dir="$(pwd)/.agentic-sdlc/examples/PAY-DEMO-001" \
  --input baseline_sha="$BASELINE_SHA" \
  --input base_branch=main \
  --input max_review_rounds=3
```

Use an explicit run ID so status/cancel/resume operations remain easy:

```bash
cao workflow status plan-PAY-DEMO-001-1
cao workflow events plan-PAY-DEMO-001-1 --follow
cao workflow cancel plan-PAY-DEMO-001-1
cao workflow resume plan-PAY-DEMO-001-1
```

## Result

A successful planning run finishes with the **business outcome** `AWAITING_HUMAN_APPROVAL` and writes:

```text
.agentic-sdlc/records/<ticket>/
    development-plan.md
    plan-review.json
    execution-manifest.json
```

Detailed run evidence remains Git-ignored under:

```text
.agentic-sdlc/runtime/<ticket>/<run-id>/
```

The human then records a decision:

```bash
python .agentic-sdlc/scripts/approve_plan.py \
  --repository-root "$(pwd)" \
  --ticket-id PAY-DEMO-001 \
  --decision APPROVED \
  --reference "reviewed by team lead"
```

The helper verifies that the plan's SHA-256 still matches the independently reviewed plan before recording approval.

## Important distinction: two approval layers

CAO itself can optionally require approval of a **workflow execution plan** using `CAO_WORKFLOW_REQUIRE_APPROVAL=1`. That protects execution of changed CAO workflow source/inputs. It is separate from the **Development Plan human approval** implemented here, which protects the transition from Planning Workflow 1 into Delivery Workflow 2.
