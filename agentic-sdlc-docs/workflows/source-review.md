# Source review workflow (`source_review`)

Source review reviews an existing GitHub pull request and produces actionable feedback for a development agent
and a human reviewer. It is independent of Planning and Delivery: no Jira ticket, approved plan, CI result or
delivery manifest is needed.

It reads source. It does not fix code, run tests, assess requirements compliance or deployment readiness,
approve a pull request or merge it. Publishing to GitHub is a separate, explicit command; by default the
workflow writes local artifacts only.

## At a glance

| | |
|---|---|
| Registered name | `source_review` |
| Agents | Context Mapper, Correctness Reviewer, Security Reviewer, Finding Validator, Feedback Author (five profiles) |
| Done by Python | Pinning the PR, exporting the source, the routing gate, rendering the artifacts, publication |
| Requires | A GitHub PR URL, or a local committed repository in fixture mode |
| Human gate | Publishing is an explicit command; a person decides the `HUMAN_REQUIRED` findings. Nothing approves or merges the PR. |
| Results | `.agentic-sdlc/runtime/source-review/<run-id>/` |
| Write boundary | Each agent may write only the answer area of the run's isolated workspace, through a hook generated for that workspace |

## How it works

```mermaid
flowchart TD
    A["Pin PR base and head,<br/>export the source"] --> B[Context Mapper]
    B --> C[Correctness Reviewer]
    C --> D[Security Reviewer]
    D --> E[Finding Validator]
    E --> F{"Routing gate<br/>(Python)"}
    F --> G[Feedback Author]
    G --> H["code-review.json<br/>and comments.md"]
    H --> I["AUTO_FIX queue"]
    H --> J["HUMAN_REQUIRED queue"]
```

1. **Context Mapper.** Accounts for every changed file, separates source and test files from out-of-scope material,
   and identifies callers and sensitive boundaries.
2. **Correctness Reviewer.** Traces changed behavior, edge cases, compatibility and test-source defects. A finding
   needs a concrete, reachable failure scenario.
3. **Security Reviewer.** Independently examines authorization, sensitive data, transactions, concurrency and
   resource lifetime.
4. **Finding Validator.** Re-reads the source, checks guards and base and head behavior, rejects speculation,
   merges findings that share a root cause and reassesses fix eligibility. Every candidate gets an explicit
   acceptance, rejection or duplicate decision. It also merges coverage gaps that other agents reported in different
   words.
5. **Routing gate.** Python routes each validated finding, ignoring any route an agent supplied.
6. **Feedback Author.** Explains the accepted findings for developers. It cannot change routing or severity;
   Python renders the canonical evidence and routing.

The specialist agents have separate contexts and are told not to consult each other's findings. They run one
after another, which keeps CAO step ordering predictable and avoids contention on a shared server; independence
does not need simultaneous execution. Parallel scheduling is not supported.

### Routing policy

A finding is `AUTO_FIX` only if every condition holds. Otherwise it is `HUMAN_REQUIRED`, with explicit reasons.

```mermaid
flowchart TD
    F["Validated finding<br/>(source evidence shows a defect the PR introduced or worsened)"] --> A1{"Severity LOW or MEDIUM?"}
    A1 -- no --> H[HUMAN_REQUIRED]
    A1 -- yes --> A2{"Confidence at least 0.90?"}
    A2 -- no --> H
    A2 -- yes --> A3{"Intended behavior clear,<br/>fix local and bounded,<br/>a deterministic check exists?"}
    A3 -- no --> H
    A3 -- yes --> A4{"Protected boundary<br/>involved?"}
    A4 -- yes --> H
    A4 -- no --> AF[AUTO_FIX]
```

Protected categories include architecture, public API and event contracts, schema changes, authentication and
authorization, cryptography and secrets, data loss, concurrency and transactions, critical business rules,
infrastructure topology and significant dependency changes. The mapper's file-level protected classification is
conservative: it forces human handling even when a reviewer claims a local fix is eligible.

Unsupported speculation is rejected, not turned into a human defect. Coverage uncertainties are recorded
separately. A confirmed defect whose fix needs judgment stays a finding and goes to a human. Model confidence is an
estimate, not a calibrated probability: the gate enforces validated evidence fields and does not prove correctness.

| Finding | Route | Why |
|---|---|---|
| Off-by-one slice violates an explicit function contract | `AUTO_FIX` | Local change and a focused regression test |
| Missing owner check in account access | `HUMAN_REQUIRED` | Authorization boundary, whatever the patch size |
| Callers disagree about idempotency | `HUMAN_REQUIRED` | Intended behavior needs a decision |
| Suspicion contradicted by an existing guard | Rejected | No substantiated defect |

The normative rules are in [the policy](../policies/source-review.md). The thresholds are fixed by
that policy and are not configurable.

## Before you run it

1. A running CAO server, the `cao` CLI and a configured `claude_code` provider.
2. Git and the GitHub CLI (`gh`) on the CAO server host. In GitHub mode `gh` needs read access to the PR, and Git
   needs HTTPS read access to its repository (`gh auth setup-git` is one way). The workflow never changes credentials.
3. The repository root and any fixture paths must be reachable **on the server host**.
4. Install the workflow and its five profiles:

   ```bash
   bash .agentic-sdlc/cao/workflows/install_source_review.sh "$PWD"
   ```

   The installer builds the workflow and its shared modules into one standalone artifact, validates it, and adds
   only `~/.aws/cli-agent-orchestrator/workflows/source_review.py` and the profiles `sdlc_source_mapper`,
   `sdlc_source_correctness`, `sdlc_source_security`, `sdlc_source_validator` and `sdlc_source_feedback`. It does not
   touch the other workflows, their profiles or the repository hook.

   It **refuses to overwrite** an installed `source_review` or any of the five profiles, so an ordinary reinstall
   cannot replace definitions another run is using. Installation is not transactional across the profile installs:
   if it fails part way, inspect the new names before retrying and do not delete unrelated profiles. To replace an
   installed version, follow [Upgrading](#upgrading).

CAO's profile validator may warn that `fs_write` is unrecognized. The Claude Code mapping supports it, and the
answer-file delivery relies on it. Do not silence the warning by granting `execute_bash`, `*` or broader tools.

### Upgrading

Because the installer never replaces an installed definition, an upgrade removes the old one first. CAO keeps each
installed profile as `agent-context/<name>.md`. `cao profile remove` only manages the local agent store
(`agent-store/`), so it cannot remove these profiles. Delete the files instead. Other workflows need not stop.

1. Wait until no `source_review` run is active (`cao workflow runs`).
2. Keep the installed workflow for rollback. The profiles can also be reinstalled from the repository.
3. Remove the installed workflow and the five profiles, then reinstall:

   ```bash
   CAO_HOME=~/.aws/cli-agent-orchestrator
   ROLLBACK=$(mktemp -d)
   cp -p "$CAO_HOME/workflows/source_review.py" "$CAO_HOME"/agent-context/sdlc_source_*.md "$ROLLBACK"/
   rm "$CAO_HOME/workflows/source_review.py"
   rm "$CAO_HOME"/agent-context/sdlc_source_{mapper,correctness,security,validator,feedback}.md
   bash .agentic-sdlc/cao/workflows/install_source_review.sh "$PWD"
   ```

4. [Check that CAO matches the repository](../build-and-install.md#check-that-cao-matches-the-repository).

To roll back, remove the new files the same way and copy the saved ones back from `$ROLLBACK`. The paths are CAO's
defaults; `CAO_WORKFLOW_DIR` moves the workflows.

## Inputs

| Input | Required | Meaning |
|---|---|---|
| `repository_root` | yes | An existing directory where the run's evidence is stored. Its working tree is not the review source. |
| `pr_url` | GitHub mode | A GitHub.com pull request URL. |
| `source_repository` | fixture mode | A local committed repository. |
| `base_sha`, `head_sha` | fixture mode | Exact 40-character commit SHAs. |

Choose GitHub mode **or** fixture mode; mixing them is rejected. GitHub Enterprise, SHA-256 Git repositories,
configurable routing thresholds and automatic fixing are not supported.

## Run

Use a fresh run ID for every invocation.

```bash
cao workflow run source_review --run-id source-review-pr42-1 \
  --input repository_root="$PWD" \
  --input pr_url=https://github.com/OWNER/REPO/pull/42
```

Add `--detach` to submit without waiting, and follow only your run with `cao workflow status <run-id>`,
`cao workflow events <run-id> --follow` and `cao workflow result <run-id>`.

The review never touches your checkout:

```mermaid
flowchart LR
    PR["GitHub PR<br/>(may be from a fork)"] --> OS["Private bare object store<br/>for this run"]
    OS --> MB["Merge base to<br/>the pinned head"]
    MB --> EX["Export base and head files<br/>as plain data"]
    EX --> WS["Isolated run workspace"]
```

The PR is fetched into a private bare object store, the merge base is compared with the pinned head, and both trees
are exported as plain data. The developer repository is not checked out or fetched into, and uncommitted edits are
ignored. When the run completes, the base tip and head are checked again.

## Results

Artifacts are isolated by run ID and ignored by Git:

```text
.agentic-sdlc/runtime/source-review/<run-id>/
├── code-review.json       canonical, routed feedback
├── comments.md            concise review with commit-specific source links
├── publication.json       GitHub review receipt, only after publishing
├── failure.json           only if orchestration failed
├── objects.git/           this run's private Git objects
└── workspace/
    ├── source/base/ and source/head/
    ├── diff.patch and snapshot.json
    ├── mapping.json, candidates.json, reported-gaps.json, adjudication.json, routed-findings.json
    └── .agentic-sdlc/runtime/<role>/    each agent's answer, raw output and stabilization log
```

Archive the whole run directory if you need durable audit retention. There is no shared per-ticket record and no
global "latest review".

`code-review.json` contains:

- `schema_version`, `policy_version` and `run_id`;
- `snapshot`: the PR identity, the base, head and merge-base SHAs and the changed paths;
- `status`: `REVIEWED` or `STALE`. Neither means the PR is approved;
- `coverage_status`: `COMPLETE` or `INCOMPLETE`, with explicit `coverage_gaps`. These are the snapshot's own gaps
  plus the agents' gaps after the Finding Validator merges reworded duplicates. Python checks that every reported
  gap is kept, merged or matched to a snapshot gap, so none is lost;
- `findings`, each with location, severity, confidence, trigger, evidence, consequence, fix direction,
  verification method, eligibility fields and routing reasons;
- `queues.AUTO_FIX` and `queues.HUMAN_REQUIRED`: finding IDs;
- `comments_sha256`, which binds the publication text to the artifact.

Each finding carries a `stable_id` and `reviewed_head_sha`. The ID hashes the PR identity, path, symbol, category
and failure scenario, but not the head or line numbers, so it survives moved lines. A reworded scenario or a renamed
file or symbol can get a new ID, and there is no semantic reconciliation across runs, so an ID missing from a later
review does not prove that its defect was fixed.

## When a run stops early

| Outcome | Meaning | What to do |
|---|---|---|
| `status: STALE` | The base tip or head moved, or the PR closed, before completion. | Do not act on it; start a fresh run. |
| `coverage_status: INCOMPLETE` | Some files could not be reviewed (binary or non-UTF-8 files, control files, symlinks, submodules, files over 2 MB, or more than 100 MB per tree). | Assess the `coverage_gaps` before treating the review as complete. An empty findings list can still be `INCOMPLETE`. |
| The run fails and `failure.json` exists | Orchestration failed, for example incomplete agent execution or a diff over 2 MB (which fails instead of being truncated). | Keep the evidence and use a fresh run ID. |
| A reused run ID is refused | Each run creates its directory exclusively; reuse and resume are deliberately refused. | Use a fresh ID. |

`COMPLETE` means no gaps were reported within a source-only review; it is not proof of correctness.

## Human decisions

A development agent should treat the artifact this way:

```mermaid
flowchart TD
    R["code-review.json"] --> C{"status REVIEWED and<br/>checkout matches snapshot.head_sha?"}
    C -- no --> X["Do not use it:<br/>start a fresh run"]
    C -- yes --> Q{Queue}
    Q -- AUTO_FIX --> F["Bounded fix, verified<br/>in the development workflow"]
    Q -- HUMAN_REQUIRED --> HU["A person decides"]
    F --> E{"Verified and<br/>still in scope?"}
    E -- no --> HU
    E -- yes --> CM["Commit, then run a new review<br/>against the new PR head"]
```

1. Require `status == REVIEWED`, assess the coverage gaps, and confirm the checkout matches `snapshot.head_sha`.
   Never consume a stale or failed result automatically.
2. Take the findings in `queues.AUTO_FIX`, using their canonical evidence and verification method.
3. Attempt only the bounded fix and verify it in the development workflow.
4. Escalate to a person if the scope grows, the intended behavior is unclear, verification fails or the same finding
   persists. `HUMAN_REQUIRED` findings are never auto-fixed.
5. Commit and start a **new** review run against the new head, tracking repeated findings by stable ID and by comparison.

`HUMAN_REQUIRED` corresponds to Delivery's `DEVELOPER_REQUIRED` route, but the JSON contracts differ, so this
artifact cannot be passed straight to Delivery's remediator. An adapter must select findings, keep the SHA binding
and map the fields.

### Publishing to GitHub

Publication is a separate command and defaults to a preview. It posts one `COMMENT` review that contains the finding
comments with commit-specific source links. These are **not** inline diff threads, so findings on deleted code stay
linkable without inventing an anchor.

```bash
RUN_DIR=.agentic-sdlc/runtime/source-review/source-review-pr42-1

# Preview the exact request; no GitHub write.
python3 .agentic-sdlc/scripts/publish_source_review.py "$RUN_DIR"

# Explicitly post the reviewed feedback.
python3 .agentic-sdlc/scripts/publish_source_review.py "$RUN_DIR" --publish
```

```mermaid
flowchart LR
    RUN["Reviewed run directory"] --> PRE["Preview<br/>no GitHub write"]
    PRE --> POST["--publish"]
    POST --> CHK["Check the comments hash<br/>and the open PR's base and head"]
    CHK --> REVW["One COMMENT review<br/>at the exact commit"]
    REVW --> REC["publication.json receipt"]
```

Publishing needs GitHub pull-request write permission. It verifies the comments hash, rechecks that the PR is open
at the same base and head, and posts with an exact `commit_id` and `event=COMMENT`. It never approves or requests
changes. It lists all review pages and reuses an earlier review that carries the same base and head marker, so a
retry does not duplicate. A local exclusive lock stops concurrent publication of the same run; after a crash, inspect
GitHub before removing `publication.lock`. The GitHub API has no transactional post-if-unchanged operation, so
serialize publication across run directories for the same PR. A push can race the final check, but the posted review
stays bound to its original commit. See [GitHub's create-review API](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request).

## Configuration

Source review has no configuration file. What you choose is the mode and the inputs above, and the routing policy is
fixed. To try it without a real PR, use fixture mode.

### Local fixture

The fixture contains a clearly bounded off-by-one regression and a missing authorization check. The script creates
its own repository and never modifies an existing one:

```bash
python3 agentic-sdlc-local-inputs/source-review/create_fixture.py /tmp/my-source-review-fixture

cao workflow run source_review --run-id source-review-fixture-1 \
  --input repository_root="$PWD" \
  --input source_repository=/tmp/my-source-review-fixture \
  --input base_sha=BASE_SHA_FROM_FIXTURE \
  --input head_sha=HEAD_SHA_FROM_FIXTURE
```

Expect the slice defect to be `AUTO_FIX` and the missing ownership check to be `HUMAN_REQUIRED`. Agent wording,
candidate counts and duplicate handling can differ. Fixture artifacts cannot be published to GitHub.

## Safety boundaries

- PR source never supplies active hooks or agent settings. Agent-control files are excluded, and symlinks and
  submodules are not materialized. Reviewer profiles get no shell, network or subagent tools. A generated, trusted hook
  allows writes only inside the run workspace's answer tree; see [Agent answers and write scope](../reference/write-scope-hook.md).
- The hook is a write guard, not an operating-system read sandbox. The review agents run under the configured provider
  account and inherit its global configuration. Use a dedicated provider account or container if you must isolate
  a hostile repository.
- Both snapshots are UTF-8 text exports; whatever cannot be exported becomes a coverage gap.
- Incomplete agent execution never becomes an empty review. A JSON or contract defect gets one repair attempt;
  an execution failure writes `failure.json` and fails the CAO run.
- Only this run's step terminals are cleaned up. The workflow never restarts the CAO server, cleans up shared
  terminals or modifies a developer checkout.

## Tests

The suite covers routing overrides, contracts, validation accounting, deduplication, source export from real Git
commits, preservation of a dirty checkout, write-guard symlink escapes, the five-stage pipeline, coverage
propagation, stale or tampered publication, publication retries and failed-run evidence:

```bash
python3 -m unittest discover -s tests -v
bash -n .agentic-sdlc/cao/workflows/install_source_review.sh
```

The hook tests need local socket access. The live fixture and a live test PR with planted defects exercise the CAO,
provider and GitHub integration, including publication. They do not replace the regression suite or measure review
accuracy across real PRs. The [live verification record](../verification/source-review-live.md) has results and limits.

## See also

[Delivery](delivery.md) · [Agent profiles](../reference/agent-profiles.md) ·
[Source-review contract](../contracts/source-review-workflow.md) · [Source-review policy](../policies/source-review.md)
