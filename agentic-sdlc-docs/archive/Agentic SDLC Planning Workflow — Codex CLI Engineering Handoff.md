# Agentic SDLC Planning Workflow — Engineering Handoff

> Historical handoff. The implementation and operating instructions here are superseded; use the maintained documents linked from [docs/README.md](../README.md).

## 1. Objective

Continue development of an agentic software-development lifecycle prototype built on **AWS Labs CLI Agent Orchestrator (CAO)**.

The immediate scope is **Planning Workflow 1**. It starts from Jira/Confluence-style source material plus a repository baseline and produces a reviewed Development Plan that must subsequently be approved by a human.

The overall SDLC target is:

```text
WORKFLOW 1 — PLAN

Jira + Confluence + Repository
        ↓
Context Retrieval
        ↓
Context Normalization
        ↓
Deterministic Context Validation
        ↓
Repository / Requirements Analysis
        ↓
Development Plan Authoring
        ↓
Independent Plan Review
        ↓
HUMAN PLAN APPROVAL
        ↓
Approved Development Plan


WORKFLOW 2 — DELIVER

Approved Development Plan
        ↓
Implementation
        ↓
Developer Verification
        ↓
Create PR
        ↓
Agentic PR Review + Remediation
        ↓
Human Review Brief
        ↓
HUMAN PR REVIEW / APPROVAL
        ↓
Approved PR
        ↓
QA workflow (out of scope)
```

Only **Workflow 1 — PLAN** is currently implemented.

The runtime provider for the planning agents is currently **Claude Code**. This handoff is intended for a coding agent operating through **Codex CLI**. Do **not** interpret that as a request to switch the workflow runtime provider from Claude Code to Codex unless explicitly deciding to do so later.

Current machine versions involved in the reproduced issue:

```text
CAO:         2.5.0
Claude Code: 2.1.270
```

Development repository:

```text
~/AIProjects/awscao_learning
```

CAO installed workflow:

```text
~/.aws/cli-agent-orchestrator/workflows/dev_plan.py
```

The repository version is the source of truth. The installed CAO copy is only the execution installation.

---

# 2. Architectural Principles

The intended architecture is:

```text
Deterministic macro-orchestration
+
Agentic micro-reasoning
```

The workflow determines:

- stage ordering;
- routing;
- retries;
- review consequences;
- human gates;
- evidence persistence;
- input/output contracts.

Agents determine how to reason within an individual stage.

Core principle:

> Agents make judgments; orchestration enforces consequences.

Other important principles:

- Human approval is mandatory at high-value decision boundaries.
- Planning agents are read-only specialists.
- The Python workflow owns orchestration.
- Planning agents must not delegate to other agents.
- Requirements from Jira/Confluence must not be silently invented, repaired, or redefined.
- Contradictions must be surfaced.
- Missing requirements become explicit open questions.
- Deterministic validation occurs after semantic normalization.
- Runtime evidence is distinct from durable governance records.
- Resume/replay semantics must remain deterministic.
- CAO itself should remain unmodified.
- Least privilege is important; avoid expanding agent capabilities merely to work around transport/output problems.

---

# 3. Workflow 1 Agent Roles

The planning workflow uses four specialists:

```text
sdlc_context_normalizer
sdlc_planning_analyst
sdlc_plan_author
sdlc_plan_reviewer
```

Current provider:

```python
PROVIDER = "claude_code"
```

Profiles were installed in earlier versions and v1.3 did not modify them.

Expected repository profile paths:

```text
.agentic-sdlc/cao/profiles/context-normalizer.md
.agentic-sdlc/cao/profiles/planning-analyst.md
.agentic-sdlc/cao/profiles/plan-author.md
.agentic-sdlc/cao/profiles/plan-reviewer.md
```

Typical restriction pattern:

```yaml
provider: claude_code
role: reviewer
allowedTools:
  - "@builtin"
  - "fs_read"
  - "fs_list"
```

The profiles intentionally omit:

```text
@cao-mcp-server
```

because orchestration should remain owned by the deterministic Python workflow.

Do not casually add full `@cao-mcp-server` access without understanding the privilege implications described later in this document.

---

# 4. Planning Context Architecture

Raw enterprise sources should not be passed directly into every downstream agent.

The intended boundary is:

```text
Jira / Confluence retrieval
        ↓
Raw Context Package
        ↓
Context Normalizer
        ↓
Planning Context JSON
        ↓
Deterministic Validation
        ↓
Planning agents
```

Retrieval is deterministic.

Normalization is semantic.

Validation is deterministic.

The normalizer may:

- extract facts;
- normalize wording;
- identify provenance;
- identify ambiguity;
- identify contradictions;
- identify missing requirements.

It may not invent missing requirements or choose arbitrary resolutions.

Planning Context requires fields conceptually equivalent to:

```text
schema_version
ticket
sources
problem_statement
scope
acceptance_criteria
functional_requirements
non_functional_requirements
constraints
dependencies
open_questions
contradictions
retrieval_warnings
```

Example source reference:

```json
{
  "source_id": "JIRA:PAY-1427",
  "location": "acceptance criteria / AC-1"
}
```

Example requirement:

```json
{
  "id": "AC-1",
  "text": "...",
  "origin": "EXPLICIT",
  "sources": [
    {
      "source_id": "JIRA:PAY-1427",
      "location": "acceptance criteria / AC-1"
    }
  ]
}
```

Allowed requirement origin values include:

```text
EXPLICIT
NORMALIZED_FROM_SOURCE
```

Example question:

```json
{
  "id": "Q-1",
  "question": "...",
  "reason": "...",
  "blocking": true,
  "sources": []
}
```

Example contradiction:

```json
{
  "id": "C-1",
  "description": "...",
  "blocking": true,
  "sources": [
    ...
  ]
}
```

Readiness is blocked by conditions including:

- blocking open question;
- blocking contradiction;
- required source unavailable;
- blocking retrieval warning;
- no meaningful acceptance criteria / functional requirements.

---

# 5. Reviewer Contract

Planning Review statuses:

```text
PASS
CHANGES_REQUIRED
CONTEXT_RENORMALIZATION_REQUIRED
HUMAN_DECISION_REQUIRED
```

Finding dispositions:

```text
PLAN_CHANGE_REQUIRED
CONTEXT_RENORMALIZATION_REQUIRED
HUMAN_DECISION_REQUIRED
ADVISORY
```

Impact vocabulary:

```text
LOW
MEDIUM
HIGH
```

Routing:

```text
PASS
  → human plan approval

CHANGES_REQUIRED
  → Plan Author

CONTEXT_RENORMALIZATION_REQUIRED
  → Context Normalizer
  → deterministic validation
  → Planning Analyst
  → Plan Author

HUMAN_DECISION_REQUIRED
  → stop automated flow
  → human clarification
```

The orchestrator validates consistency.

For example, an agent must not be allowed to return:

```text
status = PASS
```

while also returning a blocking finding.

---

# 6. Human Approval Contract

A reviewed plan and its approval are separate artifacts.

Expected durable artifacts:

```text
development-plan.md
plan-review.json
execution-manifest.json
plan-approval-record.json
```

Approval binds:

- Jira/ticket ID;
- exact SHA-256 of Development Plan;
- repository baseline SHA;
- approver;
- timestamp;
- APPROVED / REJECTED;
- optional human reference.

Helper:

```text
.agentic-sdlc/scripts/approve_plan.py
```

Usage:

```bash
python .agentic-sdlc/scripts/approve_plan.py \
  --repository-root "$(pwd)" \
  --ticket-id PAY-DEMO-001 \
  --decision APPROVED \
  --reference "human planning review"
```

The helper recomputes the plan hash and must refuse approval if the reviewed plan was modified afterward.

The workflow must not overwrite an already approved plan for the same ticket.

---

# 7. Evidence Model

Durable records:

```text
.agentic-sdlc/records/<ticket>/
```

Runtime/debug evidence:

```text
.agentic-sdlc/runtime/<ticket>/<run-id>/
```

Runtime is Git-ignored:

```gitignore
.agentic-sdlc/runtime/
```

Durable records must not be Git-ignored.

The intended general repository layout is:

```text
.agentic-sdlc/
├── cao/
│   ├── profiles/
│   │   ├── context-normalizer.md
│   │   ├── planning-analyst.md
│   │   ├── plan-author.md
│   │   └── plan-reviewer.md
│   └── workflows/
│       ├── dev_plan.py
│       └── install.sh
├── contracts/
├── policies/
├── templates/
├── examples/
│   └── PAY-DEMO-001/
│       ├── context.json
│       ├── jira.md
│       └── confluence/
│           ├── feature-spec.md
│           └── architecture.md
├── scripts/
│   └── approve_plan.py
├── records/
│   └── <ticket>/
└── runtime/
    └── <ticket>/
        └── <run-id>/
```

Inspect the checkout rather than assuming every filename above still exists exactly as shown. `dev_plan.py` is currently authoritative for contract/template paths.

---

# 8. Synthetic Fixture

Current test ticket:

```text
PAY-DEMO-001
```

Problem:

> Make payment callbacks idempotent.

Known Jira acceptance criteria include:

- duplicate provider event must not duplicate fulfilment;
- external HTTP response behavior must remain backwards compatible;
- duplicates must be observable in logs.

Confluence context includes:

- provider event ID is stable;
- a completed event must not be processed twice;
- an event that failed before completion may retry;
- external HTTP response behavior remains unchanged;
- major components include:
  - CallbackController
  - PaymentService
  - PaymentRepository
  - FulfilmentService
- PostgreSQL is the system of record;
- do not introduce a distributed lock service solely for callback idempotency.

The fixture is a fake retrieval adapter only. The rest of the workflow is intended to be production-shaped.

Production retrieval should later replace the fixture with Jira and Confluence APIs while preserving the same Raw Context contract.

---

# 9. Workflow Script Inputs

Current `dev_plan.py` inputs:

```python
INPUTS = {
    "ticket_id": {
        "type": "string",
        "required": True,
    },
    "repository_root": {
        "type": "path",
        "required": True,
    },
    "source_dir": {
        "type": "path",
        "required": True,
    },
    "baseline_sha": {
        "type": "string",
        "required": True,
    },
    "base_branch": {
        "type": "string",
        "required": False,
        "default": "main",
    },
    "max_review_rounds": {
        "type": "int",
        "required": False,
        "default": 3,
    },
}
```

`baseline_sha` is deliberately passed as an explicit workflow input.

Do not dynamically resolve HEAD inside the workflow.

Reason:

CAO journals/replays workflow inputs. Dynamically resolving Git HEAD during a resumed run could silently change the planning baseline.

The baseline may be SHA-1 or SHA-256. Do not enforce an assumed hash length such as exactly 40 characters.

---

# 10. Current dev_plan.py Important Constants

Current v1.3 includes:

```python
PROVIDER = "claude_code"

CONTEXT_NORMALIZER = "sdlc_context_normalizer"
PLANNING_ANALYST = "sdlc_planning_analyst"
PLAN_AUTHOR = "sdlc_plan_author"
PLAN_REVIEWER = "sdlc_plan_reviewer"

STEP_TIMEOUT_SECONDS = 1800

CAO_HTTP_TIMEOUT_SECONDS = 30.0

COMPLETION_INITIAL_SETTLE_SECONDS = 5.0
COMPLETION_POLL_SECONDS = 3.0
COMPLETION_MAX_POLLS = 100
COMPLETION_STABLE_POLLS = 2
```

The effective stabilization budget is approximately:

```text
5 + (100 × 3) = 305 seconds
```

Current activity detection:

```python
LIVE_TUI_ACTIVITY_RE = re.compile(
    r"^[ \t]*(?:[✶✢✽✻✳·*][ \t]+\w*ing\b[^\n]*…|"
    r"(?:Reading|Searching|Analyzing|Inspecting)\b[^\n]*…)",
    re.MULTILINE | re.IGNORECASE,
)
```

Completion summaries:

```python
COMPLETION_SUMMARY_RE = re.compile(
    r"^[ \t]*[✶✢✽✻✳][^\n…]*\bfor\s+\d+(?:\.\d+)?\s*s\b",
    re.IGNORECASE,
)
```

---

# 11. CAO Step Execution Design in v1.3

Previous versions used:

```python
handle = step(
    PROVIDER,
    agent,
    prompt,
    recovery="idempotent",
    step_id=step_id,
    timeout=STEP_TIMEOUT_SECONDS,
    working_directory=str(repo),
)
```

CAO sometimes declared a Claude worker complete while Claude was still producing its answer.

v1.3 changed every planning call to:

```python
handle = step(
    PROVIDER,
    agent,
    prompt,
    recovery="idempotent",
    step_id=step_id,
    timeout=STEP_TIMEOUT_SECONDS,
    working_directory=str(repo),
    teardown=False,
)
```

Then the workflow explicitly stabilizes the output and cleans the terminal.

Important current functions:

```text
_stabilize_live_step_output(...)
_run_stabilized_step(...)
_run_json_contract_step(...)
_cleanup_step_terminal(...)
_cao_terminal_snapshot(...)
_looks_incomplete_agent_output(...)
_has_live_tui_activity(...)
```

The lifecycle is:

```text
step(teardown=False)
        ↓
CAO returns StepHandle
        ↓
keep terminal alive
        ↓
wait 5 seconds
        ↓
poll:
    GET /terminals/{id}
    GET /terminals/{id}/output?mode=last
    GET /terminals/{id}/output?mode=full
        ↓
reject incomplete/live output
        ↓
require stable extracted output
        ↓
accept
        ↓
POST /terminals/{id}/exit
DELETE /terminals/{id}
```

This workaround must remain local to the project. Do not patch the CAO installation.

---

# 12. Why v1.3 Was Introduced

Earlier failures showed CAO returning incomplete Claude output.

## Run `plan-PAY-DEMO-001-5`

The JSON file stopped exactly after:

```json
"status": "RETRIEVED",
```

The Python error was:

```text
json.decoder.JSONDecodeError:
Expecting property name enclosed in double quotes:
line 12 column 23 (char 244)
```

The bytes immediately surrounding char 244 were:

```text
'e": "JIRA",\n"title": "Make payment callbacks idempotent",\n"status": "RETRIEVED",\n'
```

This was not malformed semantic JSON generated by Claude.

It was a truncated response.

Three attempts were made:

```text
context-normalize-v1
context-normalize-v1-repair-1
context-normalize-v1-repair-2
```

The first and third output were identical; the second was shorter.

The JSON repair loop was therefore attempting to repair missing transport/output bytes.

That was the wrong abstraction.

---

# 13. CAO Status Warning Observed

Server logs showed:

```text
step on terminal <id> shows no pickup 8.0s after send
(idle, never working) — re-delivering prompt
```

followed by:

```text
Delivery to <id> not accepted and provider is not probe-capable;
skipping full re-send to avoid a duplicate task
```

This comes from CAO's defensive prompt-delivery logic.

Important CAO behavior discovered:

- prompt pickup grace is 8 seconds;
- Claude Code does not provide a direct status probe;
- CAO avoids blindly re-sending a prompt because the worker might have already accepted it;
- it may therefore keep waiting until the step timeout.

This warning was initially suspected as the cause of output loss.

It turned out to be related to the same broader status/TUI-detection problem but not the complete explanation.

---

# 14. Probe Tests Already Performed

A headless probe was used to separate normal Claude operation from workflow behavior.

Initial command omitted `--headless`.

That only created the Claude session and attached to it.

This is expected from current CAO behavior:

```text
cao launch <message>
without --headless
    → create session
    → attach tmux
    → message is not automatically dispatched

cao launch <message> --headless
    → create session
    → wait ready
    → dispatch message
    → wait for completion
    → return response
```

Corrected probe:

```bash
cd ~/AIProjects/awscao_learning

cao launch \
  'Read .agentic-sdlc/examples/PAY-DEMO-001/jira.md and return ONLY a JSON object containing the ticket id, summary, and an array called acceptance_criteria. Make the JSON at least 20 lines long.' \
  --agents sdlc_context_normalizer \
  --provider claude_code \
  --session-name json-probe-2 \
  --working-directory "$(pwd)" \
  --headless \
  --auto-approve
```

When the server had been started with:

```bash
CAO_PYTE_STATUS=false cao-server
```

the probe started and completed normally and did not emit the 8-second no-pickup warning.

This established that Claude itself was functioning.

---

# 15. Run `plan-PAY-DEMO-001-6`

Despite disabling pyte status detection, the workflow still failed.

All three CAO steps were marked completed:

```text
context-normalize-v1: completed
context-normalize-v1-repair-1: completed
context-normalize-v1-repair-2: completed
```

But the workflow got:

```text
JSONDecodeError:
Expecting value: line 1 column 1 (char 0)
```

One returned payload looked like:

```text
Reading 1 file…

✻ Architecting… (2s · ↓ 67 tokens · thinking with high effort)

tmux focus-events off · add 'set -g focus-events on' to ~/.tmux.conf and reattach for focus tracking
```

This demonstrated that the workflow was receiving a **live Claude TUI frame** as the completed step result.

Therefore merely setting:

```text
CAO_PYTE_STATUS=false
```

did not solve the underlying workflow lifecycle problem.

---

# 16. Version / Provider Verification Already Performed

Installed versions:

```bash
cao --version
```

Output:

```text
cao, version 2.5.0
```

Claude:

```bash
claude --version
```

Output:

```text
2.1.270 (Claude Code)
```

The installed CAO provider contains current/newer TUI handling:

```bash
grep -n "_EFFORT_FOOTER_TAIL" \
  ~/.local/share/uv/tools/cli-agent-orchestrator/lib/python3.14/site-packages/cli_agent_orchestrator/providers/claude_code.py
```

Result included:

```text
73:_EFFORT_FOOTER_TAIL = (
103:...
111:EFFORT_FOOTER_LINE_PATTERN = ...
```

Also:

```bash
grep -n "NEW_TUI_BOX_SPINNER_PATTERN" \
  ~/.local/share/uv/tools/cli-agent-orchestrator/lib/python3.14/site-packages/cli_agent_orchestrator/providers/claude_code.py
```

Result included:

```text
254:NEW_TUI_BOX_SPINNER_PATTERN = ...
1151:...
1262:...
```

Therefore this is not simply an old CAO installation missing the recent Claude TUI fixes.

Do not waste time reinstalling the same CAO 2.5.0 package expecting this issue to disappear.

---

# 17. CAO Source Behavior Relevant to the Failure

CAO's Claude provider expects final responses to be identifiable through response markers such as:

```text
⏺
●
```

Its extraction logic uses `EXTRACTION_RESPONSE_PATTERN`.

Its last-message extraction ultimately searches terminal history for the response marker.

If no marker can be found, CAO's output layer eventually falls back to a message such as:

```text
[NO RESPONSE - agent completed without producing a text response (... lines in buffer)]
```

plus terminal content.

CAO's own E2E handoff tests contain explicit stabilization behavior:

```text
wait for COMPLETED
sleep ~5 seconds
re-check status
if no longer COMPLETED:
    continue waiting
```

This was one reason v1.3 introduced local stabilization.

However, the Python workflow `/terminals/run-step` lifecycle still returns on the first completion event and normally tears down the terminal.

---

# 18. v1.3 Design Decisions

v1.3 deliberately leaves CAO unchanged.

It added:

```text
step(..., teardown=False)
```

for all four planning roles.

It also introduced:

```python
class IncompleteAgentExecutionError(WorkflowContractError):
    ...
```

Incomplete/live execution does not enter the JSON repair loop.

JSON repair is now limited to one repair attempt:

```python
max_repairs: int = 1
```

The intended distinction is:

```text
live / incomplete provider execution
        ↓
execution failure
        ↓
DO NOT JSON-repair


completed agent response
but invalid JSON / invalid shape
        ↓
one bounded JSON contract repair
```

This separation is correct and should be retained.

---

# 19. v1.3 Runtime Evidence

For machine-readable stages:

```text
.agentic-sdlc/runtime/<ticket>/<run-id>/
  context/normalized/agent-output/
```

Files may include:

```text
context-normalize-v1.initial.txt
context-normalize-v1.final.txt
context-normalize-v1.raw.txt
context-normalize-v1.stabilization.json
context-normalize-v1.timeout-last.txt
context-normalize-v1.timeout-full.txt
context-normalize-v1.cleanup-warning.txt
```

Equivalent `agent-output` folders also exist for analysis/planning stages.

The diagnostics are intentionally retained.

---

# 20. Run `plan-PAY-DEMO-001-7` — Most Important Diagnostic

This run used v1.3.

The CAO server was started normally:

```bash
cao-server
```

i.e. default:

```text
CAO_PYTE_STATUS=true
```

The run ultimately failed with:

```text
IncompleteAgentExecutionError:
context-normalize-v1 did not produce a stable final response
after 305s of completion stabilization
(last CAO status: completed)
```

CAO step state:

```text
context-normalize-v1: completed (attempts=1)
```

The failure was in our workflow stabilization layer, not in CAO's journaled step state.

---

# 21. Exact v1.3 Stabilization Findings

`context-normalize-v1.stabilization.json` contained exactly 100 polls.

Status distribution:

```text
completed: 100 / 100
```

Live-TUI detection:

```text
live_tui_activity = false: 100 / 100
```

Incomplete-output classification:

```text
poll 1:
  incomplete_output = false
  last_output_length = 1104

poll 2:
  incomplete_output = true
  last_output_length = 2092

poll 8 onward:
  incomplete_output = true
  last_output_length = 2919
```

From poll 8/9 onward the output SHA was stable:

```text
e6dcaf71b4e36189b48f2a66f6f088ec83433c41ef11ae2545fdd31d0ea800fa
```

and length remained:

```text
2919
```

through poll 100.

So the terminal was not continuing to change for most of the five-minute stabilization period.

---

# 22. Critical Finding from timeout-last.txt

`context-normalize-v1.timeout-last.txt` started with:

```text
[NO RESPONSE - agent completed without producing a text response (50 lines in buffer)]
```

But immediately following that message was a large tail section of the actual normalized JSON.

The JSON included valid semantic content such as:

```json
{
  "id": "OQ-1",
  "question": "What is the required behaviour when a duplicate callback for the same provider event ID arrives while the original processing attempt is still in flight (neither completed nor yet failed)?",
  "blocking": true
}
```

and other open questions / source references.

The tail eventually closed the JSON:

```text
"contradictions": [],
"retrieval_warnings": []
}
```

Then the Claude TUI displayed:

```text
✻ Brewed for 1m 21s · done 21:41
```

This is decisive.

**Claude actually completed the normalization successfully.**

CAO failed to extract the final answer because its expected response marker was no longer available.

---

# 23. Root Cause Established by Run 7

There are at least two independent CAO/Claude TUI problems.

## Problem A — Premature completion

Earlier runs showed:

```text
Claude still working:
Reading ...
Architecting ...
        ↓
CAO reports COMPLETED
        ↓
run-step extracts partial/live output
```

v1.3 successfully protected against immediate teardown by using:

```text
teardown=False
```

## Problem B — Final response extraction failure

Run 7 showed:

```text
Claude genuinely finishes
        ↓
complete JSON exists in terminal history / repaint
        ↓
Claude TUI no longer exposes response marker in form CAO expects
        ↓
CAO mode=last cannot extract final answer
        ↓
CAO returns:
[NO RESPONSE ...]
+
tail of rendered terminal
```

v1.3 cannot recover from this because it still depends on:

```text
GET /terminals/{id}/output?mode=last
```

for the actual output contract.

---

# 24. Why timeout-full.txt Must Not Be Regex-Cleaned

`timeout-full.txt` contains the raw Claude TUI redraw stream.

It is not simply plain text decorated with ANSI colours.

It contains cursor movements and in-place repaint sequences.

Naively removing ANSI/control sequences can corrupt the semantic text.

During investigation, examples conceptually equivalent to this appeared:

```text
"blocking": true
```

turning into malformed fragments such as:

```text
"blocking": rue
```

or otherwise losing characters / duplicating lines.

Therefore do **not** implement:

```text
raw terminal stream
→ regex strip ANSI
→ search for JSON
→ json.loads()
```

as the production fix.

That is unsafe.

It risks silently mutating requirements data.

A full terminal emulator could reconstruct a rendered screen, but a rendered screen still cannot reliably recover arbitrarily long content that has already scrolled/repainted away.

Terminal scraping is the wrong contract boundary.

---

# 25. Recommended v1.4 Direction

The next version should remove terminal text extraction from the agent-output contract.

Target:

```text
Agent reasoning / interactive TUI
        ↓
structured side channel
        ↓
workflow receives typed output
```

CAO already has a concept called:

```text
workflow_return
```

which sends structured workflow output independently from terminal text.

CAO workflow step environments already carry workflow identity such as:

```text
CAO_WORKFLOW_RUN_ID
CAO_WORKFLOW_STEP_ID
```

and the script-tier runner can adopt structured output produced via `workflow_return`.

Investigate this mechanism first.

---

# 26. Least-Privilege Problem with workflow_return

The original architecture intentionally omitted:

```text
@cao-mcp-server
```

from planning specialists.

Current CAO access to its MCP server is largely granted at server level via:

```yaml
allowedTools:
  - "@cao-mcp-server"
```

That exposes more than merely:

```text
workflow_return
```

It may also make orchestration capabilities reachable, e.g. depending on configuration:

```text
handoff
assign
send_message
workflow_run
workflow_resume
workflow_cancel
...
```

Some capabilities such as discovery have additional separate gates, but `@cao-mcp-server` is still broader than desired.

This violates the original least-privilege intention:

> planning specialists should reason and return data, but must not own orchestration.

Do not simply add `@cao-mcp-server` to every planning agent and consider the issue solved without documenting this security tradeoff.

---

# 27. Preferred v1.4 Architecture

Preferred solution:

Create a **return-only structured-output channel**.

Conceptually:

```text
Planning Agent
     |
     | submit_output(...)
     v
Return-only MCP server / controlled output bridge
     |
     | typed structured payload
     v
Deterministic Python workflow
```

The agent should be allowed to:

```text
read repository/source material
return its typed result
```

but not:

```text
create agents
handoff
assign
send arbitrary messages
run workflows
cancel workflows
control other terminals
```

Possible solution directions to investigate:

### Option A — small project-owned MCP server

Expose one tool only, conceptually:

```text
submit_workflow_output
```

The server should:

1. identify the current workflow run and step safely;
2. accept a JSON object / structured payload;
3. post/store it through CAO's supported workflow-output API;
4. expose no orchestration functions.

Before implementing, verify how dynamic identity reaches a custom MCP subprocess.

CAO's Claude provider explicitly injects:

```text
CAO_TERMINAL_ID
```

into configured MCP server environments.

Investigate whether:

```text
CAO_WORKFLOW_RUN_ID
CAO_WORKFLOW_STEP_ID
```

also reach custom MCP servers automatically.

Do not assume they do.

If they do not, design a safe mapping from:

```text
CAO_TERMINAL_ID
→ workflow run ID
→ workflow step ID
```

using public CAO interfaces or explicit workflow registration.

Do not read internal CAO SQLite tables directly unless there is no supported alternative and the tradeoff is explicitly accepted.

### Option B — temporary workflow_return prototype

For investigation only, grant the built-in CAO MCP server to one specialist and prove that:

```text
Claude
→ workflow_return
→ structured step output
→ Python workflow receives correct data
```

If this works, it validates the transport architecture.

Do not necessarily retain this privilege expansion in the final design.

### Option C — controlled output file

Potential fallback:

Give the specialist a capability to write only one predetermined output artifact.

Example:

```text
runtime/.../context-normalized-output.json
```

However, CAO's ordinary `fs_write` permissions may not provide path-level isolation.

Do not grant broad repository write access merely to solve output transport.

A custom output-only MCP tool is preferable.

---

# 28. Structured Outputs Should Apply to All Four Roles

Do not only fix Context Normalizer and Plan Reviewer.

The CAO provider/TUI problem is provider-wide.

Planning Analyst and Plan Author could suffer the same extraction failure later.

Use structured envelopes even for Markdown outputs.

Example contracts:

## Context Normalizer

Return:

```json
{
  "context": {
    "...": "Planning Context object"
  }
}
```

or directly the Planning Context object if the transport supports arbitrary objects.

Validate against the existing Planning Context schema.

## Planning Analyst

Return:

```json
{
  "analysis_markdown": "..."
}
```

## Plan Author

Return:

```json
{
  "plan_markdown": "..."
}
```

## Plan Reviewer

Return structured review JSON:

```json
{
  "status": "PASS",
  "findings": []
}
```

This completely decouples semantic results from terminal rendering.

---

# 29. Existing JSON Contract Logic

Current parser:

```python
def _parse_json_output(text: str, label: str) -> Any:
    candidate = text.strip()

    if candidate.startswith("```"):
        ...

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise WorkflowContractError(
            f"{label} did not return valid JSON: {exc}"
        ) from exc
```

Current JSON execution wrapper:

```python
def _run_json_contract_step(
    *,
    agent: str,
    prompt: str,
    label: str,
    step_id: str,
    repo: Path,
    evidence_dir: Path,
    validator=None,
    max_repairs: int = 1,
) -> Any:
    ...
```

It currently:

1. runs `_run_stabilized_step`;
2. writes raw output;
3. parses JSON;
4. runs deterministic validator;
5. if contract parsing/shape fails:
   - performs at most one repair turn.

This architecture can mostly remain, but the input should eventually be the structured-output payload instead of terminal text.

Once structured output is trustworthy, the syntax-repair step may no longer be necessary for object-returning tools because serialization is performed by the tool protocol rather than by the model.

Still preserve deterministic schema validation.

---

# 30. Repair Policy Decision

Earlier version v1.2 allowed two repairs:

```text
primary
repair-1
repair-2
```

This was too expensive and masked transport failures.

v1.3 changed it to:

```text
primary
at most repair-1
```

Keep the principle:

```text
serialization / contract defect
→ bounded automated repair

business ambiguity
→ human clarification

transport / execution defect
→ execution failure, not repair
```

Do not increase the repair count to work around CAO output problems.

---

# 31. Important CAO Replay Semantics

Python script workflow steps use stable step IDs.

Example:

```text
context-normalize-v1
context-normalize-v1-repair-1
planning-analysis-v1
plan-author-r1
plan-review-r1-c1
```

Stable IDs are important for resume/replay.

`StepHandle` includes:

```text
step_id
terminal_id
output
status
replayed
```

If:

```python
handle.replayed is True
```

then:

```text
handle.terminal_id
```

refers to the terminal from the original execution and that terminal no longer exists.

Do not poll a replayed terminal.

Current v1.3 accounts for this.

Also:

If workflow source/prompt execution-affecting definitions change, do not blindly resume an older failed run.

Prefer a new run ID because CAO may detect replay divergence.

---

# 32. Recovery / Resume Commands Already Investigated

Failed run:

```bash
cao workflow result <run-id>
```

Resume:

```bash
cao workflow resume <run-id>
```

Explicit step decision examples:

```bash
cao workflow resume <run-id> --decide <step-id>=rerun
```

or:

```bash
cao workflow resume <run-id> --decide <step-id>=skip
```

Inputs are replayed verbatim.

For major workflow changes, prefer a fresh run.

---

# 33. Workflow Installation Constraint

CAO validates workflow paths and refuses specs outside its allowed workflow directory.

Previous error:

```text
workflow spec path
'/home/dad/AIProjects/awscao_learning/.agentic-sdlc/cao/workflows/dev_plan.py'
escapes its validated directory
```

Therefore source-of-truth and execution installation are separate:

```text
Git repository
.agentic-sdlc/cao/workflows/dev_plan.py

        ↓ install

~/.aws/cli-agent-orchestrator/workflows/dev_plan.py
```

`install.sh` should:

1. syntax-check repository candidate;
2. stage it inside CAO workflow directory;
3. validate staged candidate through CAO;
4. backup old target;
5. atomically promote candidate;
6. revalidate installed target.

Do not redesign this back into directly running the repository path.

---

# 34. Starting CAO

Normal:

```bash
cao-server
```

Diagnostic fallback previously tested:

```bash
CAO_PYTE_STATUS=false cao-server
```

Do not make `CAO_PYTE_STATUS=false` an architectural dependency unless future evidence requires it.

v1.3 run 7 intentionally used normal/default pyte status and demonstrated that local stabilization still cannot fix missing final output extraction.

The next solution should avoid dependence on terminal status/extraction mode entirely.

---

# 35. Standard Test Run Command

From repository root:

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

Use a new run ID after modifying execution-affecting workflow code.

Example next run:

```text
plan-PAY-DEMO-001-8
```

or later.

---

# 36. Useful Terminal Inspection Commands

Terminal metadata:

```bash
TERMINAL=<terminal-id>

curl -s \
  "http://127.0.0.1:9889/terminals/$TERMINAL" \
  | jq .
```

Full output:

```bash
curl -s \
  "http://127.0.0.1:9889/terminals/$TERMINAL/output?mode=full" \
  | jq -r '.output'
```

Last output:

```bash
curl -s \
  "http://127.0.0.1:9889/terminals/$TERMINAL/output?mode=last" \
  | jq -r '.output'
```

tmux session/window metadata can also be obtained through CAO.

Previous manual inspection approach:

```bash
META=$(curl -s "http://127.0.0.1:9889/terminals/$TERMINAL")

SESSION=$(echo "$META" | jq -r '.tmux_session')
WINDOW=$(echo "$META" | jq -r '.tmux_window')

tmux capture-pane \
  -p \
  -t "$SESSION:$WINDOW" \
  -S -100
```

or:

```bash
tmux attach-session -t "$SESSION"
```

Exact metadata field names may differ in the current API response; inspect with `jq .` first.

---

# 37. Important Current Failure Artifacts

For run 7:

```text
.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-7/
```

Important diagnostic files:

```text
context/normalized/agent-output/
  context-normalize-v1.initial.txt
  context-normalize-v1.stabilization.json
  context-normalize-v1.timeout-last.txt
  context-normalize-v1.timeout-full.txt
```

`timeout-full.txt` must be treated as raw TUI evidence, not canonical semantic output.

`timeout-last.txt` demonstrates CAO's marker-extraction failure.

If reproducing the problem, retain equivalent artifacts.

---

# 38. Runtime Output Detected in Run 7

The actual normalized context raised several useful questions, showing that Claude's semantic work was successful.

Examples included:

```text
OQ-1:
What is the required behaviour when a duplicate callback for the
same provider event ID arrives while the original processing attempt
is still in flight?
```

Reason:

```text
Existing source material specifies:
- already completed → no second fulfilment
- failed before completion → retry allowed

but does not specify concurrent duplicate handling.
```

Another question:

```text
What HTTP response should a duplicate/already-processed callback receive?
```

Another:

```text
Is there a defined retention period for provider event IDs used for duplicate detection?
```

The final payload also showed:

```json
"contradictions": [],
"retrieval_warnings": []
```

This confirms that the semantic normalizer itself was functioning correctly.

The problem is transport/extraction.

---

# 39. Planning / Delivery Boundary Decisions Already Made

Do not move implementation into Workflow 1.

Workflow 1 ends at a human-approved Development Plan.

Workflow 2 will later consume the exact approved plan and verify its SHA-256.

Workflow 2 design already agreed conceptually:

```text
approved plan
→ implementation
→ developer verification
→ PR
→ independent PR review
→ bounded remediation
→ human review brief
→ human PR approval
```

PR findings are intended to contain:

```text
id
location
category
impact
confidence
failure scenario
consequence
acceptance criterion
remediation direction
auto-fix eligibility
reason
```

Impacts:

```text
LOW
MEDIUM
HIGH
```

High impact changes always require developer/human ownership.

Examples of HIGH:

- architecture;
- public APIs;
- database/schema migration;
- auth/authz;
- IAM/security boundary;
- crypto;
- concurrency semantics;
- financial/business-critical logic;
- infrastructure topology;
- major dependency;
- requirement reinterpretation;
- material deviation from approved plan.

This is future work and should not distract from fixing Workflow 1 output transport.

---

# 40. Current Constraints

## Must not patch CAO

Do not edit:

```text
~/.local/share/uv/tools/cli-agent-orchestrator/...
```

or CAO package source as the solution.

The project workaround/fix must remain in the application repository.

## Keep repository copy canonical

Do not hand-edit only:

```text
~/.aws/cli-agent-orchestrator/workflows/dev_plan.py
```

Change:

```text
.agentic-sdlc/cao/workflows/dev_plan.py
```

then install it.

## Preserve least privilege

Planning specialists should not gain orchestration powers unless justified.

## Preserve deterministic replay

Avoid runtime nondeterminism in execution-affecting values.

CAO workflow lint warns about modules such as:

```text
random
time
datetime
uuid
```

v1.3 uses:

```python
threading.Event().wait(seconds)
```

instead of importing `time`.

## Preserve explicit provenance

Do not allow model-generated requirements without source references.

## Never silently resolve contradictory requirements

Escalate instead.

## Do not treat raw terminal TUI text as canonical output

This is now established by evidence.

---

# 41. Unresolved Issues

## 41.1 Main unresolved issue

How should read-only planning agents reliably return structured results without relying on Claude terminal output extraction?

Preferred answer:

```text
structured side channel
```

rather than terminal scraping.

## 41.2 Custom MCP identity propagation

If implementing a project-owned return-only MCP server, determine how it identifies:

```text
workflow run ID
workflow step ID
```

The custom MCP process is expected to receive:

```text
CAO_TERMINAL_ID
```

because CAO injects it for configured MCP servers.

Verify whether it also receives:

```text
CAO_WORKFLOW_RUN_ID
CAO_WORKFLOW_STEP_ID
CAO_WORKFLOW_GENERATION
```

Do not assume.

## 41.3 Public API for structured step output

Investigate CAO 2.5.0's supported endpoint for workflow step output.

Current CAO tests/source indicate an endpoint conceptually like:

```text
/workflows/runs/{run_id}/steps/{step_id}/output
```

and `workflow_return` already uses CAO's structured-output store.

Use the supported public API / documented contract rather than importing `cli_agent_orchestrator` internals into workflow code.

## 41.4 Output schema on script-tier `step()`

Determine whether script-tier:

```python
cao_workflow.step(...)
```

can directly supply an `output_schema` option that integrates with `workflow_return`.

The shim forwards extra `**opts` into `/terminals/run-step`, but verify the current `RunStepRequest` schema and script-runner adoption behavior before relying on this.

## 41.5 Tool privilege granularity

Determine whether CAO 2.5.0 offers any supported way to expose only:

```text
workflow_return
```

without all normal `@cao-mcp-server` orchestration tools.

Current investigation suggests no sufficiently narrow server-level selector, but verify before writing a custom server.

---

# 42. Recommended Next Tasks

Perform these in order.

## Task 1 — Inspect current checkout before editing

Run:

```bash
cd ~/AIProjects/awscao_learning

git status --short

find .agentic-sdlc -maxdepth 4 -type f | sort
```

Read at minimum:

```text
.agentic-sdlc/cao/workflows/dev_plan.py
.agentic-sdlc/cao/workflows/install.sh
.agentic-sdlc/cao/profiles/context-normalizer.md
.agentic-sdlc/cao/profiles/planning-analyst.md
.agentic-sdlc/cao/profiles/plan-author.md
.agentic-sdlc/cao/profiles/plan-reviewer.md
.agentic-sdlc/scripts/approve_plan.py
contracts/schemas referenced by dev_plan.py
tests for the workflow
```

Do not rewrite architecture before understanding existing tests.

## Task 2 — Add run-7 regression evidence as deterministic fixtures

Create test fixtures modelling:

1. premature `COMPLETED` with partial JSON;
2. `mode=last` returning:

```text
[NO RESPONSE ...]
```

despite terminal completion;
3. a completion summary such as:

```text
✻ Brewed for 1m 21s · done
```

4. raw TUI redraw output that must not be regex-parsed as semantic JSON.

The tests should ensure future code never silently treats corrupted terminal text as authoritative.

## Task 3 — Prototype structured output using built-in workflow_return

Before implementing a custom server, make a small isolated proof.

Goal:

```text
one Claude worker
→ calls workflow_return
→ Python workflow receives structured object
```

Do not modify the full planning flow yet.

If broad `@cao-mcp-server` access is needed, use it only for this prototype and document that it is intentionally temporary.

## Task 4 — Inspect exact structured-output APIs

Trace CAO 2.5.0 implementations for:

```text
workflow_return
step_output_store
script_runner structured-output adoption
RunStepRequest
output_schema
```

Relevant CAO source areas discovered during prior investigation include:

```text
src/cli_agent_orchestrator/mcp_server/server.py
src/cli_agent_orchestrator/services/script_runner.py
src/cli_agent_orchestrator/services/workflow_service.py
src/cli_agent_orchestrator/services/agent_step.py
src/cao_workflow/__init__.py
```

Do not import these modules into project workflow code; inspect them to understand the supported boundary.

## Task 5 — Design return-only bridge

If built-in privilege granularity is insufficient, implement the smallest possible project-owned MCP server.

Desired capability:

```text
submit_workflow_output(payload)
```

No:

```text
handoff
assign
send_message
workflow_run
terminal control
shell execution
filesystem write
```

The bridge should validate identity and post structured data to CAO.

## Task 6 — Move all four planning roles to structured return

Do not leave Plan Analyst/Plan Author on terminal extraction.

Use structured envelopes:

```json
{"analysis_markdown": "..."}
```

```json
{"plan_markdown": "..."}
```

plus machine-readable context/review schemas.

## Task 7 — Remove terminal parsing from success path

Keep terminal captures only as diagnostics.

Success must not depend on:

```text
mode=last
response marker
tmux scrollback
ANSI stripping
spinner regex
```

## Task 8 — Retain bounded execution timeout

The underlying agent still needs a timeout.

Do not replace the 30-minute CAO step timeout with an unbounded wait.

If structured return is used, the structured result should be accepted independently from terminal rendering but still subject to the workflow execution timeout.

## Task 9 — Run fresh workflow ID

After changing execution semantics, do not resume run 7.

Use a fresh ID such as:

```text
plan-PAY-DEMO-001-8
```

or later.

---

# 43. Acceptance Criteria for v1.4

The next version should satisfy all of these.

1. CAO 2.5.0 remains unmodified.

2. Claude Code 2.1.270 may remain installed.

3. The normalizer can run longer than one minute without terminal repaint causing result loss.

4. The workflow does not depend on `⏺` / `●` response markers.

5. Raw TUI output is diagnostic only.

6. A successful normalizer returns a complete Planning Context object.

7. Planning Context still passes deterministic provenance/schema validation.

8. Agent output transport cannot silently alter semantic content.

9. Incomplete execution does not trigger JSON repair.

10. Semantic business blockers still result in:

```text
AWAITING_HUMAN_CLARIFICATION
```

rather than automatic repair.

11. Plan Analyst output is complete even if its Markdown is longer than the visible terminal.

12. Plan Author output is complete even if its Markdown is longer than the visible terminal.

13. Plan Reviewer output is structured and validated.

14. Planning agents remain read-only with respect to production source.

15. Planning agents must not gain broad orchestration authority as an accidental side effect of fixing result transport.

16. Runtime evidence remains available for diagnosis.

17. Durable plan/review/manifest records retain their existing governance semantics.

18. Existing approval hashing behavior remains unchanged.

19. Existing deterministic unit tests continue passing.

20. Add regression tests specifically covering the run-5, run-6, and run-7 failure classes.

---

# 44. Tests Previously Reported Passing

v1.2 had 10 deterministic tests covering areas such as:

- approval binds exact plan hash;
- modified plan refused;
- blocking question stops readiness;
- missing source blocker;
- malformed JSON → repair success;
- validator shape failure → repair success;
- fenced JSON tolerance;
- review status consistency;
- unknown provenance rejection;
- valid context ready.

v1.3 reported 15 tests passing and added coverage for:

- premature `Reading 1 file… / ✻ Architecting…`;
- incomplete agent execution does not trigger JSON repair;
- one-turn JSON repair;
- stabilization behavior.

Run the actual repository suite and inspect test names rather than relying solely on this historical count.

---

# 45. Workflow Installer Commands

Profiles, if needed:

```bash
cao profile validate .agentic-sdlc/cao/profiles/context-normalizer.md
cao profile validate .agentic-sdlc/cao/profiles/planning-analyst.md
cao profile validate .agentic-sdlc/cao/profiles/plan-author.md
cao profile validate .agentic-sdlc/cao/profiles/plan-reviewer.md
```

Installation:

```bash
cao install .agentic-sdlc/cao/profiles/context-normalizer.md
cao install .agentic-sdlc/cao/profiles/planning-analyst.md
cao install .agentic-sdlc/cao/profiles/plan-author.md
cao install .agentic-sdlc/cao/profiles/plan-reviewer.md
```

Workflow:

```bash
.agentic-sdlc/cao/workflows/install.sh
```

Server must be running for CAO workflow validation.

---

# 46. Errors Encountered — Quick Reference

## Workflow path escape

```text
workflow spec path
'/home/dad/AIProjects/awscao_learning/.agentic-sdlc/cao/workflows/dev_plan.py'
escapes its validated directory
```

Resolution:

Install repository workflow into CAO's workflow directory before validating/running.

---

## Empty baseline SHA

An early run was launched with an empty `baseline_sha`.

Resolution:

```bash
BASELINE_SHA=$(git rev-parse --verify HEAD)
```

Pass explicitly as workflow input.

---

## Claude session could not launch

Early CAO attempts failed because tmux was improperly installed.

Manual Claude launch also failed.

Resolution:

Install/fix tmux.

---

## JSON truncation

```text
JSONDecodeError:
Expecting property name enclosed in double quotes
line 12 column 23 (char 244)
```

Output stopped after:

```json
"status": "RETRIEVED",
```

Root cause:

Incomplete CAO/Claude response extraction, not semantic JSON generation.

---

## Live TUI returned as final result

```text
Reading 1 file…

✻ Architecting… (2s · ↓ 67 tokens · thinking with high effort)
```

followed by JSON parser error:

```text
Expecting value: line 1 column 1 (char 0)
```

Root cause:

CAO marked the worker complete before Claude finished.

---

## v1.3 stabilization timeout

```text
IncompleteAgentExecutionError:
context-normalize-v1 did not produce a stable final response
after 305s of completion stabilization
(last CAO status: completed)
```

Root cause:

Claude actually finished, but CAO's final response-marker extraction failed.

---

## CAO final extraction fallback

```text
[NO RESPONSE - agent completed without producing a text response
(50 lines in buffer)]
```

Yet the following terminal content contained the tail of the actual normalized JSON and:

```text
✻ Brewed for 1m 21s · done
```

This is the strongest evidence that terminal-marker extraction must be removed from the workflow contract.

---

# 47. Things Not To Do

Do not:

- patch CAO package files;
- increase JSON retries to hide transport failures;
- downgrade requirements validation;
- silently accept `[NO RESPONSE ...]` as semantic output;
- reconstruct canonical requirements JSON by stripping ANSI from the raw TUI stream;
- make terminal scraping the permanent fix;
- dynamically resolve repository baseline during replay;
- allow planning agents to change production source;
- grant broad orchestration tools without documenting the privilege expansion;
- combine Workflow 1 implementation with Workflow 2 implementation before Workflow 1 is reliable;
- resume an old run after materially changing step definitions unless replay semantics are explicitly understood.

---

# 48. Suggested Codex CLI Starting Procedure

From:

```bash
cd ~/AIProjects/awscao_learning
```

first inspect:

```bash
git status --short

sed -n '1,420p' \
  .agentic-sdlc/cao/workflows/dev_plan.py

find .agentic-sdlc/cao/profiles \
  -maxdepth 1 \
  -type f \
  -print

find .agentic-sdlc \
  -maxdepth 4 \
  -type f \
  | sort
```

Then inspect tests and contracts before editing.

The first coding objective should be:

> Replace terminal-derived planning-agent output with a structured result transport, while retaining deterministic workflow orchestration and read-only/least-privilege planning agents.

The first investigation objective should be:

> Determine the narrowest supported CAO 2.5.0 mechanism for a script-tier `step()` worker to submit structured output independently from Claude Code TUI rendering.

Do not start by rewriting `dev_plan.py`.

First prove the output transport in isolation.

---

# 49. Desired End State

The intended final Planning Workflow should look conceptually like:

```text
Python Workflow
    |
    | deterministic invocation
    v
Context Normalizer
    |
    | structured return
    v
Planning Context object
    |
    | deterministic schema + provenance validation
    v
Planning Analyst
    |
    | structured envelope containing Markdown analysis
    v
Plan Author
    |
    | structured envelope containing Development Plan
    v
Plan Reviewer
    |
    | structured Review object
    v
Deterministic routing
    |
    v
Human approval
```

At no point should the semantic contract depend on what happens to be visible in:

```text
tmux
Claude Code TUI
response marker
spinner
terminal scrollback
```

That is the key architectural lesson from runs 5, 6, and 7.
