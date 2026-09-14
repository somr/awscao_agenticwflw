# Agentic SDLC — Planning Workflow 1 v1.3

## What changed in v1.3

v1.3 leaves AWS Labs CAO completely untouched and adds a workflow-local workaround for a Claude Code completion race observed with CAO 2.5.0 and Claude Code 2.1.270.

CAO's `/terminals/run-step` path can transiently settle a Claude worker as `COMPLETED` while the Claude TUI is still processing. In the failing runs, the workflow received live TUI content such as:

```text
Reading 1 file…
✻ Architecting… (2s · ↓ 67 tokens · thinking with high effort)
```

instead of the final agent answer.

v1.3 changes every planning-agent call to this lifecycle:

```text
CAO step(teardown=False)
        ↓
CAO first reports completion
        ↓
keep terminal alive
        ↓
5-second initial settle
        ↓
poll public CAO terminal/output APIs
        ↓
reject live TUI / no-response / partial-response states
        ↓
require the final extracted answer to remain stable
        ↓
accept output
        ↓
graceful exit + explicit terminal cleanup
```

The workaround is implemented only in `.agentic-sdlc/cao/workflows/dev_plan.py`; no CAO package file is patched.

Machine-readable steps also changed slightly:

- live/incomplete agent execution is classified as `IncompleteAgentExecutionError` and **never** enters JSON repair;
- genuine completed JSON/contract defects get at most **one** repair turn instead of two;
- stabilization evidence is retained under each run's `agent-output/` directories.

If upgrading from v1.2, the agent profiles do not need to be reinstalled. Reinstall only the workflow and use a **new run ID**.

## Apply to your application repository

From the root of the target repository:

```bash
unzip agentic-sdlc-planning-workflow-v1.3.zip -d /tmp/agentic-sdlc-v1.3
cp -R /tmp/agentic-sdlc-v1.3/agentic-sdlc-planning-workflow-v1.3/.agentic-sdlc/. .agentic-sdlc/
```

Add this to the application's `.gitignore`:

```gitignore
.agentic-sdlc/runtime/
```

Do **not** Git-ignore `.agentic-sdlc/records/`; those are durable SDLC evidence.

## 1. Profiles

If you already installed the v1.2 profiles, leave them as-is. For a clean installation:

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

Workflow validation is server-backed, so start `cao-server` first:

```bash
cao-server
```

v1.3 does not require a CAO source patch or a particular `CAO_PYTE_STATUS` value. If you currently run with `CAO_PYTE_STATUS=false` for diagnostics, you can leave it that way for the first v1.3 run.

## 3. Install the workflow

From the application repository root in another terminal:

```bash
.agentic-sdlc/cao/workflows/install.sh
```

The repository copy remains canonical. The installer syntax-checks it locally, stages it inside CAO's permitted workflow directory, validates it through the running server, and atomically installs it as:

```text
~/.aws/cli-agent-orchestrator/workflows/dev_plan.py
```

## 4. Run the synthetic example

Use a fresh run ID after changing workflow source:

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

The fixture is only the retrieval adapter. Normalization, deterministic validation, repository analysis, plan authoring and independent review are production-shaped.

## 5. Stabilization evidence

Each live CAO step now retains evidence such as:

```text
.agentic-sdlc/runtime/PAY-DEMO-001/<run-id>/context/normalized/agent-output/
    context-normalize-v1.initial.txt
    context-normalize-v1.final.txt
    context-normalize-v1.stabilization.json
    context-normalize-v1.raw.txt

.agentic-sdlc/runtime/PAY-DEMO-001/<run-id>/analysis/agent-output/
.agentic-sdlc/runtime/PAY-DEMO-001/<run-id>/planning/agent-output/
```

`stabilization.json` records CAO status, extracted-output length/hash, whether live TUI activity was present, and whether the candidate was incomplete. This gives us evidence if the upstream race occurs again.

## 6. Planning result

A passing planning run writes:

```text
.agentic-sdlc/records/PAY-DEMO-001/
├── development-plan.md
├── plan-review.json
└── execution-manifest.json
```

Temporary run evidence remains under:

```text
.agentic-sdlc/runtime/PAY-DEMO-001/<run-id>/
```

If source material is incomplete/contradictory, or review requires a human decision, the workflow ends with business outcome `AWAITING_HUMAN_CLARIFICATION`.

## 7. Human approval

After reading the reviewed Development Plan:

```bash
python .agentic-sdlc/scripts/approve_plan.py \
  --repository-root "$(pwd)" \
  --ticket-id PAY-DEMO-001 \
  --decision APPROVED \
  --reference "human planning review"
```

The helper recomputes the plan SHA-256 and refuses approval if the reviewed plan changed.

## Local verification performed on v1.3

- Python syntax compilation of the workflow and approval helper.
- `bash -n` validation of the workflow installer.
- 15 deterministic unit tests passing.
- Regression coverage for the observed `Reading 1 file… / ✻ Architecting…` premature-completion case.
- Regression coverage ensuring incomplete agent execution never triggers JSON repair.
- Regression coverage for bounded one-turn JSON repair and contract validation.

A live `cao workflow validate` cannot be run in this build environment because CAO is not installed here. The provided installer performs that validation against your running CAO 2.5.0 server before promotion.
