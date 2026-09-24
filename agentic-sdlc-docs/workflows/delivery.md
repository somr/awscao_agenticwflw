# Delivery workflow (`sdlc_deliver`)

Delivery implements a **human-approved** Development Plan on its own branch, verifies the result with trusted
commands, has an independent agent review the diff, fixes what is safe to fix automatically (at most three
rounds), and stops with a Human Review Brief. It never pushes and never creates a real pull request: the "PR" is
a set of local artifacts, and a human records the final decision.

## At a glance

| | |
|---|---|
| Registered name | `sdlc_deliver` |
| Agents | Code Supervisor (hybrid mode only), Implementer (also the workers and the integrator), PR Reviewer, Remediator |
| Done by Python | Plan checks, branch and commits, verification, review routing, the remediation loop, PR artifacts |
| Requires | A plan approved with `approve_plan.py` (see [Planning](planning.md)) |
| Human gate | Recording the decision on the delivered branch with `record_pr_approval.py` |
| Results | `agentic-sdlc-records/<ticket>/` and the Git branch `sdlc/<ticket>` |
| Write boundary | Implementer and remediator: the configured source roots. Supervisor and PR reviewer: their answer file only. |

## How it works

```mermaid
flowchart TD
    A["Check the plan is approved,<br/>its hash and its baseline"] --> B["Create or resume<br/>branch sdlc/ticket"]
    B --> M{Implementation mode}
    M -- hybrid --> SUP[Code Supervisor assigns tasks]
    SUP --> WK["Workers implement,<br/>one after another"]
    WK --> INT[Integration pass]
    M -- single --> IMP[One Implementer]
    INT --> VER
    IMP --> VER["Verify with the<br/>registry commands"]
    VER --> OK{Passed?}
    OK -- "no, first time" --> REP[One repair turn]
    REP --> VER
    OK -- "no, after repair" --> BL[BLOCKED]
    OK -- yes --> PR["Write the PR artifacts<br/>(local only)"]
    PR --> REV[Independent PR Reviewer]
    REV --> F{"Auto-fixable findings<br/>and rounds left?"}
    F -- yes --> REM[Remediator]
    REM -- "changed nothing" --> ESC["Escalate those findings<br/>to the human"]
    ESC --> BRIEF
    REM -- changed code --> V2[Verify again]
    V2 -- failed --> BL
    V2 -- passed --> REV
    F -- no --> BRIEF[Human Review Brief]
    BRIEF --> H[Human decision]
```

1. **Check.** Python re-verifies that the plan is approved, that its hash matches, and that the approved baseline
   commit is still an ancestor of the base branch.
2. **Branch.** Work happens on `sdlc/<ticket>`, created from the current tip of the base branch.
3. **Implement.** In hybrid mode a read-only supervisor splits the plan into ordered assignments for registered
   workers, Python dispatches them one after another, and an integration pass reconciles the result; in single mode
   one implementer does everything. See [Specialists and skills](hybrid-delivery.md). Python commits the change.
4. **Verify.** Python runs the registry's commands. One failure gets one repair turn; a second failure ends the run `BLOCKED`.
5. **PR artifacts.** Title, body and diff are written locally. Nothing is pushed.
6. **Review.** A separate read-only agent reviews the diff and classifies the findings. Python applies the routing
   policy, so an agent's own classification is never trusted: high-impact and protected-category findings always go
   to a human, and only findings that meet the auto-fix conditions are remediated.
7. **Remediate.** The remediator fixes exactly the auto-fixable findings, Python verifies again, and the new head is
   reviewed afresh. This repeats at most three times. If the remediator changes no file under the source roots, no
   code change can resolve those findings (for example, the finding asks for verification evidence that only a
   command could produce). Python then marks them `DEVELOPER_REQUIRED`, adds the remediator's reasons to them, and
   goes straight to the brief. The verified head is unchanged, so it is not reviewed again.
8. **Brief.** Python renders the Human Review Brief from the manifest and the reviews.

## Before you run it

1. **Approve a plan.** Run [Planning](planning.md) until it reports `AWAITING_HUMAN_APPROVAL`, read
   `agentic-sdlc-records/<ticket>/development-plan.md`, then record your decision with `approve_plan.py`.
2. **Install the profiles, then the workflow**, in this order (the default mode needs `sdlc_code_supervisor`):

   ```bash
   for profile in code-supervisor implementer pr-reviewer remediator; do
     cao profile validate ".agentic-sdlc/cao/profiles/$profile.md"
     cao install ".agentic-sdlc/cao/profiles/$profile.md"
   done
   bash .agentic-sdlc/cao/workflows/install_deliver.sh "$PWD"
   ```

3. **Have the runtime pieces in the repository you run against:** a running `cao-server`, the write-scope hook
   (`.claude/settings.json` and `.claude/hooks/restrict-write-scope.py`), and the registry
   `.agentic-sdlc/cao/specialists.json`. The hook runs with the `python3` on your `PATH` and uses only the standard library.
4. **Start from a clean tree.** The base branch must exist. In hybrid mode the source roots must have no uncommitted
   changes and the Git index must be empty.
5. **Configure it for your project** if your source is not under `app/` (see [Configuration](#configuration)).

## Inputs

| Input | Required | Meaning |
|---|---|---|
| `ticket_id` | yes | The ticket whose approved plan is implemented. Records live under `agentic-sdlc-records/<ticket_id>/`. |
| `repository_root` | yes | The repository to change. CAO requires an existing directory and refuses system paths such as `/tmp`. |
| `base_branch` | no, default `main` | The delivery branch starts from the current tip of this branch, and the plan's approved baseline must still be its ancestor. |
| `implementation_mode` | no, default `hybrid` | `hybrid`: a supervisor assigns work to registered workers. `single`: one implementer does all the work and verification uses the registry's `application` suite. |

## Run

Use a fresh run ID every time. `--wait --json` prints the workflow's final output, which the default follow mode does not.

```bash
cao workflow run sdlc_deliver --wait --json --run-id deliver-PAY-DEMO-001-1 \
  --input ticket_id=PAY-DEMO-001 --input repository_root="$PWD" --input base_branch=main
```

If the branch `sdlc/<ticket_id>` does not exist it is created from the tip of the base branch; if it exists it is
checked out and continued (delete it with `git branch -D sdlc/<ticket_id>` to start again). **The working tree is
left on the delivery branch**, so return with `git checkout <base_branch>` before doing other work.

## Results

Under `agentic-sdlc-records/<ticket_id>/`:

```text
├── delivery-manifest.json   state, mode, source roots, verification results, review rounds, remediation history, PR head SHA
├── pr-title.txt, pr-body.md, pr-diff.patch      the local "pull request"
├── pr-review-r<N>.json      each independent review round
├── human-review-brief.md    what to read before deciding
├── pr-approval-record.json  after the human decision
└── approval-history/        earlier decisions on superseded heads
```

Detailed evidence (agent answers, dispatch, verification logs) stays under
`.agentic-sdlc/runtime/<ticket_id>/<run-id>/`, which Git ignores. The manifest state moves like this:

```mermaid
stateDiagram-v2
    [*] --> READY
    READY --> IMPLEMENTED
    READY --> BLOCKED
    IMPLEMENTED --> VERIFIED
    IMPLEMENTED --> BLOCKED
    VERIFIED --> PR_CREATED
    PR_CREATED --> AWAITING_HUMAN_REVIEW
    PR_CREATED --> BLOCKED
    AWAITING_HUMAN_REVIEW --> HUMAN_APPROVED
    AWAITING_HUMAN_REVIEW --> REJECTED
```

## When a run stops early

| Outcome | Meaning | What to do |
|---|---|---|
| `BLOCKED`, `hybrid_implementation_failed: ...` | An implementation step broke its contract. Partial edits stay in the working tree. | See [After a `BLOCKED` run](#after-a-blocked-run). |
| `BLOCKED`, `verification_failed_after_one_repair_attempt` | Verification still failed after one repair turn. | See [After a `BLOCKED` run](#after-a-blocked-run). |
| `BLOCKED`, `verification_failed_after_remediation` | A remediation round broke verification. | See [After a `BLOCKED` run](#after-a-blocked-run). |
| Run state `failed` | A check stopped the run: the plan is not approved or changed after review, the baseline is no longer an ancestor, the source-root configuration is invalid, the source roots are dirty, or a single-mode implementation or repair step changed nothing inside the source roots. | `cao workflow result <run-id> --json` carries the traceback in its `warnings` field. |
| `AWAITING_HUMAN_REVIEW` with `has_developer_required_findings` | The review found items only a human may decide, such as high-impact or protected categories. | Read the brief. See [Human decisions](#human-decisions). |
| `AWAITING_HUMAN_REVIEW` with `escalated_findings` | The reviewer marked these findings auto-fixable, but the remediator changed nothing for them. The brief shows each one with the remediator's reason. | Read the brief; each escalated finding is a developer finding. See [Human decisions](#human-decisions). |
| `convergence_limit_reached` | Automatic remediation used all three rounds and auto-fixable findings remain. | Read the brief; treat the remaining findings as developer findings. See [Human decisions](#human-decisions). |

### After a `BLOCKED` run

No command moves a delivery out of `BLOCKED`, and `record_pr_approval.py` refuses it. You leave `BLOCKED` by fixing
the cause and starting a new Delivery run, which writes a new manifest. The failed run's manifest, its runtime
evidence and its commits on `sdlc/<ticket>` stay in place until you move them.

**1. Find the cause.** Read `reason` in the run output and the evidence behind it:

```bash
cao workflow result <run-id> --json
ls .agentic-sdlc/runtime/<ticket>/<run-id>/implementation/agent-output/   # agents' answers
ls .agentic-sdlc/runtime/<ticket>/<run-id>/verification/                  # verification logs
```

**2. Fix it where it belongs.**

| `reason` | Usual cause | Fix |
|---|---|---|
| `hybrid_implementation_failed: ...` | An agent broke its contract, for example invalid output or a write outside the source roots. Its partial edits stay uncommitted in the working tree. | Usually none: retry. If the same step fails again, check the plan and the registry. |
| `verification_failed_after_one_repair_attempt` | The code still fails the verification commands after one repair turn. | Commands wrong (missing toolchain, bad command): fix `.agentic-sdlc/cao/specialists.json` and commit it on the base branch. Plan wrong: plan again (step 3b). Code wrong: retry. |
| `verification_failed_after_remediation` | A remediation round broke verification. | Read the review finding it was fixing; usually retry. |

**3a. Retry with the same approved plan.** From the repository root:

```bash
git restore --staged --worktree -- app              # only after hybrid_implementation_failed: drop partial edits
git clean -fd -- app                                #   and the new files they created
git checkout main                                   # Delivery leaves you on sdlc/<ticket>
git branch -m sdlc/<ticket> archive/sdlc-<ticket>-<run-id>
cd agentic-sdlc-records/<ticket> && rm -f delivery-manifest.json human-review-brief.md pr-* && cd -
cao workflow run sdlc_deliver --wait --json --run-id <new-run-id> \
  --input ticket_id=<ticket> --input repository_root="$PWD" --input base_branch=main
```

- Use your configured source roots in place of `app`. Hybrid mode needs them clean and the Git index empty.
- Renaming the branch, rather than deleting it, keeps the failed attempt for comparison. A run on an existing
  `sdlc/<ticket>` does not resume where the previous run stopped: it implements the plan again on top of what the
  branch holds, and on an already implemented branch the workers have nothing to change, so the run ends `BLOCKED`
  with `hybrid_implementation_failed`.
- The new run overwrites most PR artifacts, but a stale `pr-review-r<N>.json` or `human-review-brief.md` from the
  failed run would survive if you did not remove them. The plan files (`development-plan.md`, `plan-review.json`,
  `execution-manifest.json`, `plan-approval-record.json`) stay.

**3b. Change the plan instead.** Planning refuses to overwrite an approved plan, so move the ticket's records aside
first. Committed records stay in Git history.

```bash
git checkout main
git rm -r -q agentic-sdlc-records/<ticket>     # the committed plan records
rm -rf agentic-sdlc-records/<ticket>           # the failed run's untracked delivery records
git commit -m "Supersede the <ticket> plan"
```

Then run [Planning](planning.md#run), approve the new plan with `approve_plan.py`, commit the new records, and continue
with step 3a from the branch rename.

**4. Check the new outcome.** `AWAITING_HUMAN_REVIEW` continues in [Human decisions](#human-decisions); `BLOCKED`
again means back to step 1. Every retry implements the whole plan again, so a failure that keeps coming back needs a
change to the plan or the registry, not another retry. A fix you make by hand on a `BLOCKED` branch cannot be reviewed
or approved by the workflow.

A plan that names files outside the configured source roots cannot be implemented. The agents are told to report
those tasks as deviations instead of writing them, and the hook denies the write anyway.

## Human decisions

When a run ends `AWAITING_HUMAN_REVIEW`, the automated part is finished and the delivery waits for you. The branch
`sdlc/<ticket>` is checked out and holds the reviewed code; the records under `agentic-sdlc-records/<ticket>/` are
not committed yet.

**1. Read the brief.** `agentic-sdlc-records/<ticket>/human-review-brief.md` lists the verification results, what
was remediated automatically, and every finding that needs you under "Human attention required". The run output
tells you which kind you have:

| Output field | Meaning |
|---|---|
| `has_developer_required_findings: false` | Nothing is left for a human to decide; review the change as you would any PR. |
| `has_developer_required_findings: true` | Findings only a human may decide, such as high-impact or protected categories. |
| `escalated_findings` | Findings the reviewer marked auto-fixable, but the remediator changed no source file for them, for example because they need a command to be run. Each shows the remediator's reason. |
| `convergence_limit_reached: true` | Three remediation rounds ran and auto-fixable findings remain. Read them as developer findings. |

**2. Review the change itself**, not only the brief:

```bash
git diff main...sdlc/<ticket>
git log --oneline main..sdlc/<ticket>
```

Run the verification commands again if you want your own evidence; they are listed in `delivery-manifest.json`
under `verification`.

**3. Decide each finding.** Accept it as it stands, plan a follow-up change, or reject the delivery. Do not commit a
fix to `sdlc/<ticket>` before recording the decision: the workflow cannot review a hand-made change, and
`record_pr_approval.py` refuses once the branch head has moved.

**4. Record the decision** on the reviewed head:

```bash
python3 .agentic-sdlc/scripts/record_pr_approval.py --repository-root "$PWD" --ticket-id <ticket> \
  --decision APPROVED --approved-by "<your name>" --reference "<PR link, or how each finding was settled>"
```

Use `--decision REJECTED` to reject. The script checks that the state is `AWAITING_HUMAN_REVIEW` and that the
branch is still at the reviewed head, writes `pr-approval-record.json`, and sets the state to `HUMAN_APPROVED` or
`REJECTED`. A decision on an exact head is immutable; an earlier decision on a different head is archived under
`approval-history/`.

**5. Finish outside the workflow.**

- **Approved:** keep the records with the code. Commit them on the base branch, then merge the delivery branch, or
  push it and open a real pull request:

  ```bash
  git checkout main
  git add agentic-sdlc-records/<ticket>
  git commit -m "Record the <ticket> delivery and its approval"
  git merge --no-ff sdlc/<ticket>
  ```

  Make the follow-up fixes you accepted in step 3 as new work.
- **Rejected:** commit the records on the base branch first if you want to keep the rejection, because the retry
  steps remove them. Then decide why, as for a `BLOCKED` run: a plan problem means planning again (step 3b of
  [After a `BLOCKED` run](#after-a-blocked-run)), a code problem means a new Delivery run on a fresh branch (step 3a).

## Configuration

Delivery is configured by one trusted file, `.agentic-sdlc/cao/specialists.json`, plus two run inputs
(`base_branch` and `implementation_mode`).

```mermaid
flowchart LR
    R[".agentic-sdlc/cao/<br/>specialists.json"] --> P["Delivery preflight<br/>validates before any agent"]
    R --> H["Write-scope hook<br/>checks every write"]
    R --> S["Code Supervisor<br/>sees workers and skills"]
    R --> V["Verification<br/>runs the suites"]
```

| What | Where | Default |
|---|---|---|
| Where generated source is written, committed and diffed | `source_roots` | `["app"]` |
| Which profiles may write there | `write_profiles` | implementer and remediator, all roots |
| Commands that verify a change | `verification` | the sample app's `app` commands |
| Workers, skills and their suites | `workers`, `skills` | one `developer` worker |
| Implementation mode | input `implementation_mode` | `hybrid` |
| Base branch | input `base_branch` | `main` |

The file is read from the repository when a run starts, so edits take effect on the next run. Agents cannot edit it.
An invalid file stops Delivery before any agent runs, and the hook grants nothing while it is invalid; it never
falls back to `app/`. If the file or the keys are absent the defaults apply.

### Registry reference

```json
{
  "version": 1,
  "source_roots": ["app"],
  "write_profiles": {"sdlc_implementer": null, "sdlc_remediator": null},
  "workers": {
    "developer": {
      "profile": "sdlc_implementer",
      "description": "When the supervisor should choose this worker.",
      "skills": ["sdlc-angularjs-ui"],
      "verification": ["application"]
    }
  },
  "skills": {
    "sdlc-angularjs-ui": {
      "path": "skills/sdlc-angularjs-ui/SKILL.md",
      "description": "When the supervisor should require this skill.",
      "verification": ["angularjs"]
    }
  },
  "verification": {
    "application": [["python3", "-m", "compileall", "-q", "app"],
                    ["python3", "-m", "unittest", "discover", "-t", "app", "-s", "app/tests", "-v"]],
    "angularjs": [["npm", "--prefix", "app/ui", "run", "verify"]]
  }
}
```

| Key | Required | Rules |
|---|---|---|
| `version` | yes | Must be `1`. |
| `source_roots` | no, default `["app"]` | See [Source roots](#source-roots). |
| `write_profiles` | no, default implementer and remediator | Which agent profiles may write under the roots. |
| `workers` | yes, not empty | Each worker needs `profile` (an agent profile listed in `write_profiles`), `description` (the supervisor uses it to route work), `skills` (registered skill names; may be empty) and `verification` (a non-empty list of registered suite names). |
| `skills` | yes, not empty | Each skill needs `path` (a file below `.agentic-sdlc/cao`, normally `skills/<name>/SKILL.md`), `description`, and `verification` (a non-empty list of suites that run whenever the skill is selected). |
| `verification` | yes, not empty | Suite name to a non-empty list of commands. A command is a non-empty list of non-empty strings: the program and its arguments, with no shell. |

Each verification pass runs every selected command once from the repository root, with a 300-second timeout,
removing duplicates across suites. A missing executable or a timeout is a failure. In hybrid mode the commands are
the union of the selected workers' and skills' suites; in single mode they are the `application` suite. Changes to
profiles or workflow code, unlike registry and skill edits, need a reinstall.

### Source roots

Generated source is written, committed, verified and diffed under the **source roots**. To point the workflows at
a multi-module project:

```json
{
  "version": 1,
  "source_roots": ["billing/src", "billing/test", "shared/lib"],
  "write_profiles": {"sdlc_implementer": null, "sdlc_remediator": null},
  "verification": {"application": [["mvn", "-q", "-pl", "billing", "verify"]]},
  "workers": {...}, "skills": {...}
}
```

- **`source_roots`**: 1 to 16 relative directories. A root that does not exist yet is fine, because a worker may
  create a new module. Not allowed: absolute paths, `..`, `.`, trailing or doubled slashes, glob characters, `:`,
  and the SDLC's own folders (`.git`, `.claude`, `.agentic-sdlc`, `agentic-sdlc-records`, `agentic-sdlc-docs`,
  `agentic-sdlc-local-inputs`). A root that is a symlink leaving the repository, or pointing into one of those
  folders, is refused.
- **`write_profiles`**: `null` gives a profile every source root; a list limits it to those directories, each equal
  to or inside a source root, for example `"sdlc_java_persistence": ["billing/src"]`. The supervisor, the PR reviewer
  and the planning and source-review profiles can never be listed.
- **`verification`** is not derived from the roots. Point it at your project's own build and test commands.

The delivery manifest records the roots each run used. A plan that names files outside the roots cannot be
implemented, so check the plan's task list against the roots before delivering.

## Safety boundaries

- The write-scope hook confines every agent, and the roots come from the trusted registry; see
  [Agent answers and write scope](../reference/write-scope-hook.md). The supervisor and the reviewer can never write source.
- Verification commands are trusted configuration that Python runs without a shell. Treat a change to them as an
  executable-code change.
- The implementer never runs tests, builds or Git; Python does, so the evidence is not an agent's claim.
- The review is independent and read-only, and its routing is recomputed by Python.
- Remediation is bounded to three rounds and always followed by verification and a fresh review.
- Approval is a human act, bound to the plan's SHA-256 and to the exact reviewed head commit.

## Tests

`tests/test_deliver.py`, `tests/test_hybrid.py`, `tests/test_source_config.py`, `tests/test_restrict_write_scope.py`,
`tests/test_record_pr_approval.py` and the end-to-end flows in `tests/test_workflow_integration.py`. See
[build and install](../build-and-install.md#tests) for how to run them.

## See also

[Specialists and skills](hybrid-delivery.md) · [Planning](planning.md) · [Agent profiles](../reference/agent-profiles.md) ·
[Delivery contract](../../.agentic-sdlc/contracts/delivery-workflow.md) ·
[PR review policy](../../.agentic-sdlc/policies/pr-review.md)
