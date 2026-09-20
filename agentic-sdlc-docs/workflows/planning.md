# Planning Workflow 1 — CAO Python workflow (current v1.5)

`dev_plan.py` is the local entry point for planning. Its implementation is in
`../../.agentic-sdlc/cao/sdlc_workflows/planning.py`; shared execution support is in `../../.agentic-sdlc/cao/sdlc_workflows/runtime.py`.
The installer builds a standalone CAO script from those modules. See the
[modular source and deployment guide](../build-and-install.md) before editing or deploying workflows.

## Historical transport change in v1.4

CAO 2.5.0 can transiently report a Claude Code worker as `COMPLETED` while the interactive TUI is still working, so every agent step still runs with:

```python
teardown=False
```

and the workflow still waits and polls before accepting a step's result. What changed in v1.4 is *what* it waits for.

**v1.3 (abandoned) tried to parse the terminal's own screen text** (CAO's `mode=last`/`mode=full` output) to decide when an answer was final. Three independent failures made that fundamentally unreliable, discovered by testing live against a running `cao-server`:

1. Claude Code's post-completion "suggested next prompt" ghost-text feature left stray text in the idle input box that CAO's `mode=last` misread as an unanswered new turn.
2. CAO's `mode=last` turned out to flag **any** idle prompt — ghost text or not — as `"[NO RESPONSE - ...]"`, immediately after a real, complete answer. Reproduced even with the ghost-text feature disabled (`promptSuggestionEnabled: false`).
3. `mode=full` is not rendered text — it is the raw PTY byte stream. Claude Code's TUI redraws the screen using cursor-addressing escape codes (e.g. `\x1b[19G` to jump to column 19), so stripping escape codes while keeping byte order produces garbled, reordered text (observed live: `"source_id"` came back as `"ource_id"`). Correctly reconstructing the screen would require a real terminal emulator, which this stdlib-only script does not have.

**v1.4 has the agent write its answer to a file instead**, and the workflow polls for that file's existence and content stability, checking only the CAO-reported terminal *status* (not its screen text) for `error`/`waiting_user_answer`:

1. leaves the CAO-created terminal alive after the first `COMPLETED`;
2. waits 5 seconds, then polls `GET /terminals/{id}` for status and the answer file on disk every 3 seconds;
3. rejects terminal `error` and `waiting_user_answer` states outright — headless agents must never block on a prompt;
4. requires the answer file's content to be identical for two consecutive polls before accepting it;
5. writes stabilization evidence (`<step_id>.stabilization.json`);
6. explicitly exits and deletes the terminal.

No CAO source or installed package is modified.

## Answer file delivery & the write-scope hook

This section is the authoritative writeup for a security-relevant design decision — keep it in sync with `../../.agentic-sdlc/cao/sdlc_workflows/runtime.py`'s module documentation and `_wait_for_answer_file`, which points back here.

**Why an agent writes at all.** These profiles were originally strictly read-only (`allowedTools: ["@builtin", "fs_read", "fs_list"]`). Delivering an answer through a file requires granting `fs_write`, which CAO maps to Claude Code's native `Edit`, `Write` and `NotebookEdit` tools as a whole category (`cli_agent_orchestrator/utils/tool_mapping.py`) — there is no CAO-level mechanism to scope `fs_write` to a single path.

**Why `permissions.allow`/`deny` path rules don't help here.** Claude Code does support path-scoped rules like `"Write(.agentic-sdlc/runtime/**)"` in `permissions.allow`. They don't apply to these workers: CAO always launches its `claude_code` provider terminals with `--dangerously-skip-permissions` (`cli_agent_orchestrator/providers/claude_code.py`), which bypasses the permission-prompt/rule-check layer entirely, not just interactive confirmation dialogs.

**The actual enforcement mechanism: a `PreToolUse` hook.** Hooks are a separate layer from permission prompts and are **not** skipped by `--dangerously-skip-permissions`. This repository's `.claude/settings.json` declares a `PreToolUse` hook on `Write|Edit|NotebookEdit` that denies any write attempt outside `.agentic-sdlc/runtime/**` — the only place `dev_plan.py` ever instructs an agent to write. Each step's prompt is built by `_run_json_contract_step` to include `_answer_file_delivery_instructions(answer_path)`, where `answer_path` is always `<evidence_dir>/<step_id>.answer.json` under that same runtime tree.

**Why this matters.** All four profiles process *untrusted external content* — the Jira/Confluence source material in `context/raw/sources/` — and a prompt-injection payload hidden in that content could try to instruct an agent to write or overwrite an arbitrary file. `fs_write` alone would not stop that (CAO's tool-category grant plus `--dangerously-skip-permissions` gives no path restriction). The hook is what actually prevents it, independent of anything the agent is tricked into attempting.

**Tested.** `tests/test_restrict_write_scope.py` invokes the hook script as a real subprocess (controlled `CAO_TERMINAL_ID` env and stdin, exactly as Claude Code fires it) and covers: non-CAO sessions are never restricted, in-scope/out-of-scope decisions, path-traversal attempts, an absolute path outside the repo, both `file_path` and `notebook_path` input shapes, and a cross-check that every real answer-path shape `dev_plan.py` builds actually passes the hook. Run it whenever either the hook or `dev_plan.py`'s evidence-path layout changes.

**If you change any part of this:**
- Removing `fs_write` from a profile requires reverting that step's prompt/stabilization back to a terminal-text-based approach (not recommended — see the v1.3 failure history above) or another delivery mechanism.
- Narrowing or removing the `PreToolUse` hook in `.claude/settings.json` reopens the write-scope gap described above. Keep an equivalent restriction in place.
- Do not rely on `permissions.allow`/`deny` rules as a substitute — they are bypassed for these workers.

## Source adapters: `local_fixture` vs `jira_confluence_live`

Retrieval is a deterministic Python function, never an agent — see `retrieve_sources()` in `../../.agentic-sdlc/cao/sdlc_workflows/planning.py`. Which adapter it dispatches to is chosen by the `source_adapter` input:

- **`local_fixture` (default)** — `retrieve_fixture_sources()` copies files named in `source_dir/context.json` verbatim. Deterministic, network-free; this is what every test and this repo's PAY-DEMO-001 example use.
- **`jira_confluence_live`** — `retrieve_live_sources()` reads the *same* `context.json` shape but each entry names a remote id instead of a local file, and fetches it over real HTTP:

```json
{
  "schema_version": "1.0",
  "ticket": { "id": "PAY-DEMO-001", "source_id": "JIRA:PAY-1234", "title": "...", "jira_key": "PAY-1234", "required": true },
  "confluence": [
    { "source_id": "CONF:123456789", "title": "Feature Spec", "page_id": "123456789", "required": true }
  ]
}
```

`ticket.id` stays the workflow's internal `ticket_id` (drives `agentic-sdlc-records/<ticket_id>/`, `.agentic-sdlc/runtime/<ticket_id>/`); `ticket.jira_key` is the real external Jira issue key, looked up separately so the two are never conflated. A manifest can list extra Confluence pages that aren't formally linked on the Jira ticket (e.g. an incident RCA, a related design doc) — this is deliberate: it lets a human curate context Jira itself doesn't capture, reviewably, before a run.

Both adapters write an identical `retrieval.json`/`raw_dir/sources/*` shape (`source_id`, `title`, `type`, `required`, `status`, `content_digest`, `path`), so nothing downstream — context-normalizer, `validate_planning_context`, the analyst/author/reviewer steps — needs to know or care which one ran.

**Credentials are environment variables, never manifest fields**, so a per-ticket manifest can be safely committed under `agentic-sdlc-records/<ticket>/` without leaking a token:

| Env var | Purpose |
|---|---|
| `JIRA_BASE_URL` | e.g. `https://your-domain.atlassian.net` |
| `JIRA_API_TOKEN` | sent as `Authorization: Bearer ...` |
| `CONFLUENCE_BASE_URL` | e.g. `https://your-domain.atlassian.net` |
| `CONFLUENCE_API_TOKEN` | sent as `Authorization: Bearer ...` |

A missing env var raises `WorkflowContractError` immediately (fail fast, never a silent skip); an HTTP failure for one source marks *that* source `UNAVAILABLE` and lets `retrieval_blockers()`/the normal required-source-missing path handle it, same as the fixture adapter does for a missing local file.

**Known, disclosed simplifications** (stdlib-only, no live Atlassian tenant available to verify against in this environment — see the module comment above `retrieve_live_sources()` in `../../.agentic-sdlc/cao/sdlc_workflows/planning.py`):
- Jira v3's ADF description format is flattened to plain text by `_adf_to_text()` (paragraphs/headings/list items joined with blank lines); this is not a full ADF renderer — tables, panels and inline formatting collapse to whatever plain text they carry.
- Confluence storage-format XHTML is stripped to plain text by `_confluence_storage_to_text()` (stdlib `html.parser`, no markdown conversion) — structure is lost, content is kept.
- Covered by `tests/test_dev_plan.py`'s `RetrieveLiveSourcesTest`/`AdfToTextTest`/`ConfluenceStorageToTextTest` against a fake local HTTP server (same technique as `tests/test_restrict_write_scope.py`'s `_FakeTerminalServer`), not against a real Jira/Confluence tenant. Treat the live HTTP calls themselves as unverified against production Atlassian until they have been.

## Inputs

- `ticket_id` — Jira-style ticket identifier, e.g. `PAY-DEMO-001`.
- `repository_root` — absolute path to the repository under analysis.
- `source_dir` — directory containing the Jira/Confluence source package (shape depends on `source_adapter`, see above).
- `baseline_sha` — Git commit SHA that the human is asking the workflow to plan against.
- `base_branch` — defaults to `main`.
- `source_adapter` — `local_fixture` (default) or `jira_confluence_live`; see above.
- `max_review_rounds` — defaults to 3; valid range 1–10. It is a cap: the loop stops at the first `PASS`.
- `resume_from` — optional candidate directory for a warm start; see "Warm start" below.
- `guidance_file` — optional path (absolute or relative to `repository_root`) to developer guidance; see "Developer guidance" below.

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

The installer bundles the modules into a self-contained Python workflow and stages it inside CAO's permitted workflow directory before server-side validation because CAO intentionally rejects validation paths outside that directory. It installs under the name `sdlc_dev_plan`, not the source file's own name (`dev_plan.py`) — CAO's workflow/profile registry is one directory shared machine-wide across every project (`~/.aws/cli-agent-orchestrator/`), so an unprefixed, generically-named workflow could silently collide with another project's own install there. Every profile already used this same `sdlc_` prefix; workflows now do too. Run it as `cao workflow run sdlc_dev_plan`, not `dev_plan`.

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

cao workflow run sdlc_dev_plan \
  --run-id "$RUN_ID" \
  --input ticket_id=PAY-DEMO-001 \
  --input repository_root="$(pwd)" \
  --input source_dir="$(pwd)/agentic-sdlc-local-inputs/PAY-DEMO-001" \
  --input baseline_sha="$BASELINE_SHA" \
  --input base_branch=main \
  --input max_review_rounds=3
```

Use `--wait --json` to see the workflow's final output (including the blockers of a run that needs a human). The default follow mode prints only the run id and state, and CAO does not retain the output afterwards.

Useful run commands:

```bash
cao workflow status "$RUN_ID"
cao workflow events "$RUN_ID" --follow
cao workflow result "$RUN_ID"
cao workflow cancel "$RUN_ID"
```

## Machine-readable boundaries

Context Normalizer and Plan Reviewer must return strict JSON. The workflow distinguishes two failure classes:

```text
terminal error/waiting_user_answer, or the answer file never
appears/stabilizes within the timeout
    → IncompleteAgentExecutionError
    → no JSON repair

stable answer file content + invalid JSON/shape
    → one bounded contract-repair turn
    → strict deterministic validation again
```

This prevents CAO/TUI lifecycle defects from being misdiagnosed as model serialization defects.

## Evidence

Detailed evidence remains Git-ignored under:

```text
.agentic-sdlc/runtime/<ticket>/<run-id>/
```

Agent-output directories contain `.answer.json` (the file the agent itself wrote), `.stabilization.json` (the poll log), and `.raw.txt` (the accepted answer content, as consumed by JSON-contract parsing) for each step attempt.

A successful planning run publishes:

```text
agentic-sdlc-records/<ticket>/
    development-plan.md
    plan-review.json
    execution-manifest.json
    plan-guidance.md          only when guidance_file was supplied
```

The business outcome is `AWAITING_HUMAN_APPROVAL`; the workflow itself never approves the plan. Only a plan whose independent review returned `PASS` is published. `approve_plan.py` verifies the plan hash and, when present, the guidance digest, and records both in the approval.

## Reviewer history

The reviewer is not stateless across rounds. From the second review of a plan onward it receives the earlier reviews (`r1`, `r2`, ...) and must account, in `prior_findings`, for every blocking finding of the most recent previous review, using refs `r<index>:<finding id>` (ids such as `PLAN-001` repeat across reviews). Statuses are `RESOLVED`, `RESOLVED_BY_GUIDANCE` (only with developer guidance), `UNRESOLVED` and `NOT_APPLICABLE`. The workflow checks this deterministically: every listed ref exactly once, no unknown refs, and `PASS` is refused while any prior finding is `UNRESOLVED`. A response that fails the check gets the usual single repair turn. The reviewer stays independent: it re-verifies each finding and may raise new evidence-based ones.

## When planning does not converge

If the independent review does not return `PASS`, nothing approvable is published. The run still ends `completed` in CAO, with outcome `AWAITING_HUMAN_CLARIFICATION`, and leaves a durable snapshot:

```text
agentic-sdlc-records/<ticket>/candidates/<run-id>/
    candidate-manifest.json   state NOT_CONVERGED, stop cause, digests of every file, sources_sha256, guidance_sha256
    human-needed.json         blockers, the blocking findings (id, impact, section, required action), next steps
    candidate-plan.md         last plan (absent when no plan was written yet)
    reviews/<k>-review-r<N>-c<V>.json   k = position in the review history; matches the refs r<k>:<id>
    planning-context.json, planning-analysis.md, sources.json
    guidance.md               the guidance used, if any
```

Stop causes: `review_convergence_limit_reached`, `plan_review_requires_human_decision`, `renormalized_context_not_ready`, `context_not_ready`. A candidate cannot be approved or delivered: `approve_plan.py` and the delivery workflow read only `agentic-sdlc-records/<ticket>/development-plan.md`.

What the developer can do: read `human-needed.json`, then either fix the blocker at its source, raise `max_review_rounds`, or record decisions as developer guidance and start a new run (below). The same run id cannot be reused.

## Warm start

A non-converged candidate can be continued instead of re-running everything:

```bash
cao workflow run sdlc_dev_plan --wait --json --run-id plan-PAY-DEMO-001-22 \
  --input ticket_id=PAY-DEMO-001 --input repository_root="$(pwd)" \
  --input source_dir="$(pwd)/agentic-sdlc-local-inputs/PAY-DEMO-001" --input baseline_sha="$BASELINE_SHA" \
  --input resume_from="$(pwd)/agentic-sdlc-records/PAY-DEMO-001/candidates/<run-id>" \
  --input guidance_file=agentic-sdlc-records/PAY-DEMO-001/guidance.md
```

The run skips normalization, validation and repository analysis (it reuses the candidate's), starts with a revision round from the candidate's last plan and reviews, then runs the normal review loop with its own `max_review_rounds` budget. The reviewer receives the candidate's reviews as history. The published `execution-manifest.json` records `resumed_from` (candidate run id, the SHA-256 of its manifest, prior review count, prior guidance digest) and `total_review_rounds`.

It fails closed, before any agent runs, and the developer must start a cold run instead when any of these is true:

- `resume_from` is not a `NOT_CONVERGED` candidate directory of this ticket, or a recorded file is missing, unsafe or modified since it was written;
- the stop cause is not `review_convergence_limit_reached` or `plan_review_requires_human_decision` (a context that was never ready needs a cold run);
- `baseline_sha` differs from the candidate's (its repository analysis would be stale) or the freshly retrieved sources differ (its context would be stale);
- the candidate stopped for a human decision and `guidance_file` is missing or unchanged since the candidate.

For a round-limit stop, no guidance is required: it can simply continue with a new round budget.

## Developer guidance

A re-run alone gives no guarantee of a different outcome. `guidance_file` lets the developer give the planners information they lacked:

```bash
cao workflow run sdlc_dev_plan --wait --json --run-id plan-PAY-DEMO-001-21 \
  --input ticket_id=PAY-DEMO-001 --input repository_root="$(pwd)" \
  --input source_dir="$(pwd)/agentic-sdlc-local-inputs/PAY-DEMO-001" --input baseline_sha="$BASELINE_SHA" \
  --input guidance_file=agentic-sdlc-records/PAY-DEMO-001/guidance.md
```

Use [the template](../templates/developer-guidance.md). Rules:

- The file must be a non-empty UTF-8 regular file of at most 64 KiB, resolved inside `repository_root` (symlinks that leave it are refused) and outside `.agentic-sdlc/runtime/`.
- It reaches the Planning Analyst, Plan Author and Plan Reviewer, not the Context Normalizer, so normalized requirements stay source-faithful. Each run works on a frozen copy (`runtime/<ticket>/<run-id>/guidance/developer-guidance.md`).
- **Authority.** Guidance may resolve ambiguity, choose between options the sources allow, narrow scope or constrain the design. It cannot relax a source requirement or the governance policy: the reviewer reports a conflict as `HUMAN_DECISION_REQUIRED`, so the source gets corrected. The plan cites each applied item.
- **Trust.** Guidance is trusted because it is human-authored and stored where no agent can write (the write-scope hook denies `agentic-sdlc-records/`, and the runtime directory is refused as a location). Its SHA-256 is recorded in `execution-manifest.json` and in any candidate manifest, and the exact file is published as `plan-guidance.md`, so human approval covers it (governance invariant 16).
- Guidance never approves anything. A `PASS` review is required (invariant 17) and a human still runs `approve_plan.py`.
