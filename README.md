> **Workflow 3 — source code review:** Review an existing GitHub PR, independently
> validate source findings, and route them to automatic fixing or human handling.
> [Installation, usage, routing and development-agent handoff](.agentic-sdlc/cao/workflows/source-review.md).

# Agentic SDLC — Planning Workflow 1 v1.4

## What changed in v1.4

v1.3 added a workflow-local workaround for a Claude Code completion race observed with CAO 2.5.0 and Claude Code 2.1.270: CAO's `/terminals/run-step` path can transiently settle a Claude worker as `COMPLETED` while the Claude TUI is still processing, so the workflow kept the terminal alive and polled CAO's terminal APIs, parsing the terminal's own screen text to decide when an answer was final.

**Live testing against a running `cao-server` showed that text-parsing approach was fundamentally unreliable** — not just for the original race, but for three separate, independently reproducible failures (Claude Code's post-completion ghost-text suggestion, CAO's `mode=last` flagging *any* idle prompt as `"[NO RESPONSE"`, and `mode=full` being an un-renderable raw cursor-addressed byte stream). v1.4 replaces terminal-text parsing entirely: **each agent now writes its final answer to a file**, and the workflow polls for that file plus the CAO-reported terminal *status* (not its screen text).

This required granting the profiles a scoped write capability (`fs_write`), which is a security-relevant change given these agents process untrusted external content (Jira/Confluence text). **The full rationale, the write-scope enforcement mechanism (a `PreToolUse` hook, not Claude Code's permission rules — which are bypassed for these workers), and what not to change without preserving it are documented in `.agentic-sdlc/cao/workflows/README.md` under "Answer file delivery & the write-scope hook." Read that section before touching profile tool permissions, `.claude/settings.json`, or the delivery/stabilization code in `dev_plan.py`.**

v1.3's lifecycle (worker kept alive, polled, terminal-text stabilized) is unchanged in shape but polls a file instead of screen text:

```text
CAO step(teardown=False)
        ↓
CAO first reports completion
        ↓
keep terminal alive
        ↓
5-second initial settle
        ↓
poll CAO terminal status (not screen text) + the agent's answer file on disk
        ↓
reject terminal error / waiting_user_answer states outright
        ↓
require the answer file's content to remain stable across 2 polls
        ↓
accept the file's content
        ↓
graceful exit + explicit terminal cleanup
```

Neither v1.3 nor v1.4 modifies any CAO source or installed package.

Machine-readable steps:

- a terminal error/waiting_user_answer state, or an answer file that never appears/stabilizes, is classified as `IncompleteAgentExecutionError` and **never** enters JSON repair;
- genuine JSON/contract defects in a stable answer get at most **one** repair turn;
- stabilization evidence is retained under each run's `agent-output/` directories.

If upgrading from v1.3, **the agent profiles must be reinstalled** (they now grant `fs_write`), and the project's `.claude/settings.json` write-scope hook must be present. Reinstall the workflow and use a **new run ID**.

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

## 5. Answer-file & stabilization evidence

Each live CAO step now retains evidence such as:

```text
.agentic-sdlc/runtime/PAY-DEMO-001/<run-id>/context/normalized/agent-output/
    context-normalize-v1.answer.json
    context-normalize-v1.stabilization.json
    context-normalize-v1.raw.txt

.agentic-sdlc/runtime/PAY-DEMO-001/<run-id>/analysis/agent-output/
.agentic-sdlc/runtime/PAY-DEMO-001/<run-id>/planning/agent-output/
```

`<step_id>.answer.json` is the file the agent itself wrote (see "Answer file delivery & the write-scope hook" in `.agentic-sdlc/cao/workflows/README.md`). `stabilization.json` records CAO terminal status and the answer file's length/hash per poll. This gives us evidence if the upstream race, or an unexpected write attempt outside the allowed path, occurs again.

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

## Local verification performed on v1.4

- Python syntax compilation of the workflow and approval helper.
- `bash -n` validation of the workflow installer.
- 29 deterministic unit tests passing, including `tests/test_restrict_write_scope.py`, which invokes the `.claude/hooks/restrict-write-scope.py` security hook as a real subprocess to verify CAO worker writes are actually confined to `.agentic-sdlc/runtime/**` (and that non-CAO sessions are never restricted).
- Regression coverage for answer-file wait/stabilize behavior (stable content, terminal error status, timeout with no file ever written).
- Regression coverage for the replayed-terminal case, both with and without an answer file already on disk.
- Regression coverage ensuring incomplete agent execution never triggers JSON repair.
- Regression coverage for bounded one-turn JSON repair, contract validation, and per-attempt answer-path/delivery-instruction wiring.
- Live end-to-end verification against a running CAO 2.5.0 / Claude Code 2.1.270 server, including reproducing and diagnosing the v1.3 terminal-text failures this version replaces.

A live `cao workflow validate` cannot be run in this build environment because CAO is not installed here. The provided installer performs that validation against your running CAO 2.5.0 server before promotion.
