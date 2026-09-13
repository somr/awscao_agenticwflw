# Agentic SDLC — Planning Workflow 1 implementation

This package consolidates the contract/profile work from Steps 1–2 and adds the first executable AWS Labs CAO Python workflow.

## Apply to your application repository

From the root of the target repository:

```bash
unzip agentic-sdlc-planning-workflow-v1.zip -d /tmp/agentic-sdlc-v1
cp -R /tmp/agentic-sdlc-v1/agentic-sdlc-planning-workflow-v1/.agentic-sdlc/. .agentic-sdlc/
```

Add this to the application's `.gitignore`:

```gitignore
.agentic-sdlc/runtime/
```

Do **not** Git-ignore `.agentic-sdlc/records/`; those are durable SDLC evidence.

## 1. Install/update profiles

```bash
cao profile validate .agentic-sdlc/cao/profiles/context-normalizer.md
cao profile validate .agentic-sdlc/cao/profiles/planning-analyst.md
cao profile validate .agentic-sdlc/cao/profiles/plan-author.md
cao profile validate .agentic-sdlc/cao/profiles/plan-reviewer.md

cao install .agentic-sdlc/cao/profiles/context-normalizer.md
cao install .agentic-sdlc/cao/profiles/planning-analyst.md
cao install .agentic-sdlc/cao/profiles/plan-author.md
cao install .agentic-sdlc/cao/profiles/plan-reviewer.md
```

## 2. Start CAO

Workflow authoring/validation commands are served by `cao-server`, so start it before installing the workflow:

```bash
cao-server
```

Leave it running.

## 3. Install the workflow

In another terminal, from the application repository root:

```bash
.agentic-sdlc/cao/workflows/install.sh
```

The canonical workflow remains repository-owned. The installer first performs a local Python syntax check, stages a candidate under CAO's validated workflow directory, asks the running server to validate it, and then atomically promotes it to:

```text
~/.aws/cli-agent-orchestrator/workflows/dev_plan.py
```

This ordering is intentional: current CAO rejects `workflow validate` paths outside its validated workflow directory.

## 4. Run the synthetic example

In another terminal, from the target application repository:

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

The synthetic Jira/Confluence fixture is only the **retrieval adapter**. Downstream normalization, validation, repository analysis, plan authoring and independent review are the same shapes intended for production.

## 5. Review the output

A passing run writes:

```text
.agentic-sdlc/records/PAY-DEMO-001/
├── development-plan.md
├── plan-review.json
└── execution-manifest.json
```

Temporary evidence is under:

```text
.agentic-sdlc/runtime/PAY-DEMO-001/<run-id>/
```

If source material is incomplete/contradictory, or review requires a human decision, the workflow ends with business outcome `AWAITING_HUMAN_CLARIFICATION` instead of producing an approvable plan.

## 6. Human approval

After reading the reviewed Development Plan:

```bash
python .agentic-sdlc/scripts/approve_plan.py \
  --repository-root "$(pwd)" \
  --ticket-id PAY-DEMO-001 \
  --decision APPROVED \
  --reference "human planning review"
```

The helper recomputes the plan hash and refuses approval if the plan changed after agent review.

To reject it:

```bash
python .agentic-sdlc/scripts/approve_plan.py \
  --repository-root "$(pwd)" \
  --ticket-id PAY-DEMO-001 \
  --decision REJECTED
```

## Local verification performed on this package

- Python syntax compilation for the workflow and approval helper.
- 8 deterministic unit tests covering context validation/readiness, provenance validation, review routing consistency, fixture retrieval and plan-hash approval protection.

A live `cao workflow validate` was not run in the build environment because CAO is not installed there; run the provided installer in your vanilla CAO installation before executing the workflow.
