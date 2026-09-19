# Planning Workflow 1 — CAO Python workflow (v1.5)

`dev_plan.py` is the local entry point for planning. Its implementation is in
`../sdlc_workflows/planning.py`; shared execution support is in `../sdlc_workflows/runtime.py`.
The installer builds a standalone CAO script from those modules. See the
[modular source and deployment guide](../README.md) before editing or deploying workflows.

## v1.4 answer-file delivery (replaces v1.3 terminal-text stabilization)

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

This section is the authoritative writeup for a security-relevant design decision — keep it in sync with `../sdlc_workflows/runtime.py`'s module documentation and `_wait_for_answer_file`, which points back here.

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

Retrieval is a deterministic Python function, never an agent — see `retrieve_sources()` in `../sdlc_workflows/planning.py`. Which adapter it dispatches to is chosen by the `source_adapter` input:

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

`ticket.id` stays the workflow's internal `ticket_id` (drives `records/<ticket_id>/`, `runtime/<ticket_id>/`); `ticket.jira_key` is the real external Jira issue key, looked up separately so the two are never conflated. A manifest can list extra Confluence pages that aren't formally linked on the Jira ticket (e.g. an incident RCA, a related design doc) — this is deliberate: it lets a human curate context Jira itself doesn't capture, reviewably, before a run.

Both adapters write an identical `retrieval.json`/`raw_dir/sources/*` shape (`source_id`, `title`, `type`, `required`, `status`, `content_digest`, `path`), so nothing downstream — context-normalizer, `validate_planning_context`, the analyst/author/reviewer steps — needs to know or care which one ran.

**Credentials are environment variables, never manifest fields**, so a per-ticket manifest can be safely committed under `records/<ticket>/` without leaking a token:

| Env var | Purpose |
|---|---|
| `JIRA_BASE_URL` | e.g. `https://your-domain.atlassian.net` |
| `JIRA_API_TOKEN` | sent as `Authorization: Bearer ...` |
| `CONFLUENCE_BASE_URL` | e.g. `https://your-domain.atlassian.net` |
| `CONFLUENCE_API_TOKEN` | sent as `Authorization: Bearer ...` |

A missing env var raises `WorkflowContractError` immediately (fail fast, never a silent skip); an HTTP failure for one source marks *that* source `UNAVAILABLE` and lets `retrieval_blockers()`/the normal required-source-missing path handle it, same as the fixture adapter does for a missing local file.

**Known, disclosed simplifications** (stdlib-only, no live Atlassian tenant available to verify against in this environment — see the module comment above `retrieve_live_sources()` in `../sdlc_workflows/planning.py`):
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
.agentic-sdlc/records/<ticket>/
    development-plan.md
    plan-review.json
    execution-manifest.json
```

The business outcome is `AWAITING_HUMAN_APPROVAL`; the workflow itself never approves the plan.
