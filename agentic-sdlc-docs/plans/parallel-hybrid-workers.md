# Plan: run independent hybrid workers in parallel

Status: **draft, not started** (written 2026-10-01; branch `feature/parallel-hybrid-workers` from `main` at `ee6658a`, after the profile-consistency merge). Decisions D3, D5, D6 and D7 were updated the same day with your answers; no questions are open.
Scope: Delivery in hybrid mode. Workers whose tasks do not depend on each other run at the same time, each in its own Git worktree, and Python merges their results in a fixed order. Single mode, the supervisor's read-only role, the integration pass, verification, review and remediation stay as they are.

## 1. Problem

A production run on a large project showed that Delivery implements tasks one at a time, even though the approved plan groups them into waves that can run in parallel. The waves are ignored at three points:

| Where | What forces one-at-a-time |
|---|---|
| `.agentic-sdlc/cao/profiles/code-supervisor.md` and the dispatch prompt in `hybrid.py` (`run_hybrid`) | Both tell the supervisor "Python dispatches sequentially", so it has no reason to keep tasks independent. |
| `hybrid.py` `validate_dispatch` | Accepts `depends_on`, but nothing reads the graph. It only checks that dependencies name earlier tasks. |
| `hybrid.py` `run_hybrid` loop | Runs `for task in dispatch["tasks"]` one by one in the single delivery checkout, and gives every worker all earlier results. |

The plan's waves (for example "Wave 1: T1 and T3 in parallel" in `agentic-sdlc-records/PAY-DEMO-001/development-plan.md`) are prose in the plan. Python never reads them. The plan's `Depends on` column reaches dispatch only through the supervisor's `depends_on`.

Running workers sequentially was deliberate. All workers share one checkout, and the write-scope hook confines a **profile** to the source roots, not a **task** to its files. Two workers writing concurrently in one tree could overwrite each other, and Python could not tell which worker changed which file. `future-versions.md` ("Won't for now: parallel workers") lists what parallelism needs first: isolated workspaces, task ownership, deterministic integration and concurrency controls. This plan supplies those four.

## 2. Facts checked before planning (2026-10-01)

- **CAO supports concurrent steps.** The installed `cao_workflow.step` documentation and CAO's `cao-workflow` skill (rule R1) allow fan-out with a `ThreadPoolExecutor`, provided every concurrent step has an explicit, stable `step_id`. CAO recommends `max_workers=2` for `claude_code` ("4 starved the heaviest lens") and a tunable input.
- **A worker can run in another directory.** `step(..., working_directory=...)` accepts any existing directory that is not a blocked system path (`/tmp` itself is blocked; directories inside the repository are fine). The Claude Code provider launches with `--dangerously-skip-permissions` and accepts the workspace-trust dialog automatically, so a new worktree should not stop at a prompt. This still needs a live check (step 1).
- **The hook follows the worker's working directory.** `.claude/settings.json` runs `python3 .claude/hooks/restrict-write-scope.py` with a relative path, so a worker started in a worktree runs **the worktree's copy** of the hook. The hook resolves every root (deny roots, `.agentic-sdlc/runtime`, source roots from `specialists.json`) against `os.getcwd()` with `realpath`. For a worker in a worktree this means:
  - it may write to the worktree's source roots and `<worktree>/.agentic-sdlc/runtime`;
  - writes to the main checkout's `app/` or `.agentic-sdlc/runtime` are **denied**, because they are outside the worktree;
  - the hook and registry in the worktree come from the delivery branch's commit, not the main checkout's working tree.
- **`.agentic-sdlc/runtime/` is ignored by Git**, so a worktree placed there is invisible to `git status -- <source roots>` in the main checkout.
- Git is 2.34.1: `git worktree add/remove/prune` and `cherry-pick` are available.
- Baseline (`deliver-profiles-1`, 2026-10-01, PAY-DEMO-001, sequential): the supervisor returned 7 tasks whose `depends_on` graph gives exactly the waves `[T1,T3] [T2,T6] [T4,T5] [T7]`. The 7 workers ran one after another from 22:31:14 to 22:38:40 (about 7.5 minutes, 44-95 s each), then integration took about 1 minute.
- Current tests: `tests/test_hybrid.py` pins the sequential order (`dispatch-v1`, `worker-1`, ...), and "a worker failure stops dependents and integration".

## 3. Decisions (proposed; confirm or change before starting)

| # | Decision | Rationale |
|---|---|---|
| D1 | **One Git worktree per task in a wave of two or more**, created by Python from the current delivery-branch HEAD under `.agentic-sdlc/runtime/<ticket>/<run>/worktrees/<task-id>` | Isolates concurrent writers with no change to the hook. The location is ignored by Git, is already per run, and is outside every source root. |
| D2 | **Python computes the waves** from the validated `depends_on` graph (topological levels, ties broken by the supervisor's task order). The supervisor does not name waves. | The schedule is deterministic and checkable. A supervisor-supplied wave label would be one more model claim to verify. |
| D3 | **Each task declares the files it expects to change.** New required task field `owns`: 1-64 relative paths or directory prefixes, each inside a source root. It is used for scheduling (D4) and reporting, not as a hard boundary. | Lets Python keep tasks that touch the same files out of the same wave, so most merges are trivial. |
| D4 | **Overlapping ownership serializes instead of failing.** When two tasks with no dependency path between them own overlapping paths, Python adds a dependency from the earlier task to the later one and records it in `schedule.json`. | A conservative supervisor costs time, not a stopped run. |
| D5 | **Isolation comes from the worktrees, and Python merges after each wave.** Changes outside `owns` are allowed and recorded as deviations in `<task>.ownership.json` and the brief. When the wave finishes, Python merges the task commits in task order. If one conflicts, Python aborts that cherry-pick and runs the task **once more, alone, in the main checkout** on top of what has been merged. The new prompt includes its earlier patch and the conflicting files. If that rerun fails, the run is `BLOCKED`. | The worktrees already stop concurrent writers from overwriting each other, so ownership does not need to be a hard stop. A real conflict costs one sequential rerun instead of a stopped run. A merge without conflicts can still be wrong in meaning; the integration pass, verification and the independent review check that, as they do today. |
| D6 | **One commit per task (confirmed), cherry-picked in task order.** This applies in every mode: sequential waves commit directly in the main checkout. For a wave of two or more, Python commits the task's changes on a scratch branch `sdlc-work/<run>/<task-id>` in the worktree and cherry-picks it onto the delivery branch after the whole wave finishes. Commit message: `[<ticket>] <task-id>: <plan_reference>`. The integration pass then runs in the main checkout and makes one more commit if it changes anything. | Each change can be traced to a task, and the next wave starts from a commit that holds the previous waves. This replaces today's single "Implement approved plan (hybrid)" commit with several commits. Diffs, review and the PR HEAD SHA already work on the branch, so nothing downstream depends on one commit. |
| D7 | **The plan decides the width, capped at 4.** A wave runs `min(len(wave), hybrid_max_parallel)` workers at once. The input `hybrid_max_parallel` is an int, default 4, allowed 1-4. A wave of one task, which is every wave when the plan has no parallel work, runs in the main checkout exactly as today: no worktree, one worker. The value 1 forces this for the whole run. | Confirmed by you. CAO measured that 4 concurrent `claude_code` steps starved the slowest one in its own tests, so step 8 must watch for slow workers and rate limits; lowering the input is the fallback. |
| D8 | **The answer file stays inside the worktree.** For a worktree worker, the answer path is `<worktree>/.agentic-sdlc/runtime/answer/...`. Python reads it there and copies the evidence into the run's evidence directory. | The hook already allows that path, so no symlinks and no hook change are needed. |
| D9 | **The worktree must hold the same trusted files as the main checkout.** Before a wave, Python checks that `.claude/settings.json`, `.claude/hooks/restrict-write-scope.py` and `.agentic-sdlc/cao/specialists.json` in each worktree have the same SHA-256 as in the main checkout. If not, the run stops before any worker starts. | A worktree runs its own copy of the hook (section 2). Uncommitted local changes to the boundary must not silently differ between the main checkout and a worktree. |
| D10 | **A failed task lets its wave finish, then stops the run.** A failure here means the worker failed (no valid completion, timeout, terminal error), not a merge conflict (D5). The other tasks in the wave complete and are recorded. Their commits are **not** cherry-picked. No later wave or integration pass runs, and the state is `BLOCKED` with the evidence. | This matches today's rule that a failure stops dependents and integration. Merging half a wave would leave a candidate nobody planned. |
| D11 | **Workers see only the results of their dependencies** (transitively), not all earlier tasks. | Siblings in a wave cannot see each other anyway. The supervisor's interface contracts in `instructions` carry the shared design, and the integration pass reconciles the results. |

## 4. Design

### 4.1 Dispatch contract (supervisor -> Python)

```json
{"tasks": [
  {"id": "T1", "worker": "developer", "plan_reference": "T1",
   "instructions": "...interface contract...", "depends_on": [], "skills": [],
   "owns": ["app/payment_service/repository.py"]},
  {"id": "T3", "worker": "developer", "plan_reference": "T3",
   "instructions": "...", "depends_on": [], "skills": [],
   "owns": ["app/payment_service/request_handler.py", "app/README.md"]}
]}
```

`validate_dispatch` adds these checks for `owns`: a non-empty list of at most 64 strings, each a normalized relative POSIX path (no `..`, no absolute path, no glob) inside a configured source root. Reuse `source_config`'s root normalizer. The existing rule that dependencies name earlier tasks stays.

Supervisor prompt and profile: replace "Python dispatches your assignments sequentially in one checkout" with: independent tasks run concurrently in isolated copies of the repository; use the plan's `Depends on` / `Parallelizable` columns; declare `depends_on` only for real producer/consumer links; assign every file to exactly one task where possible; put shared interface contracts in both tasks' instructions.

### 4.2 Schedule (pure function, unit-tested)

`build_schedule(tasks, max_parallel) -> {"waves": [[ids...], ...], "added_dependencies": [...]}`:

1. Add D4's implicit dependencies (check prefix overlap of `owns` for each pair with no dependency path between them).
2. Topological levels. Inside a level keep the supervisor's order. Split a level into chunks of `max_parallel`.
3. Write the result to `schedule.json` next to `dispatch.json`.

### 4.3 Execution (`run_hybrid`, waves of two or more tasks)

For each wave, in the main thread:

1. `git worktree add -b sdlc-work/<run>/<id> <path> HEAD` for each task. Run the D9 hash check.
2. Fan out with `ThreadPoolExecutor(max_workers=max_parallel)`. Each unit calls `_run_json_contract_step(..., repo=<worktree>, step_id=f"worker-{task_id}")`; the stable, unique `step_id` follows CAO R1. Only the worker step runs in a thread. Git and evidence writes stay in the main thread.
3. When all units are done (D10): for each task, read its changed files (`git status --porcelain` in the worktree), compare them with `owns` (D5, record only), and if there are changes, stage the source roots and commit on the scratch branch. A task with no changes is allowed, because a plan can contain a check-only task such as PAY-DEMO-001's T7; the run as a whole must still change something. Then save `<task>.patch` and `<task>.ownership.json`.
4. If every task succeeded: cherry-pick each scratch commit onto the delivery branch in task order. For a conflict: `git cherry-pick --abort`, set the task aside, and continue with the remaining tasks. After the wave, rerun each set-aside task once, sequentially, in the main checkout (D5), committing it the same way. Record `merged`, `rerun` or `failed` per task in `worker-results.json`.
5. Add results to `worker-results.json`. In a `finally` block: `git worktree remove --force` and delete the scratch branches. Run `git worktree prune` once at the start of the run.

After the last wave: run the integration pass in the main checkout, as today. `_hybrid_and_commit` changes from "uncommitted changes exist -> one commit" to "the branch moved past its start, or the integration pass left changes -> commit those changes if any". "No commits and no changes" stays a contract error.

A wave of one task (and every wave when `hybrid_max_parallel` is 1) uses today's in-checkout worker step, followed by a per-task commit (D6). So a plan with no parallel work behaves as today, except for one commit per task instead of one commit for all tasks.

### 4.4 Runtime refactor

`_run_delivered_step` / `_run_json_contract_step` gain an optional `answer_dir`, used for D8. It defaults to `evidence_dir`, so existing callers do not change. Worker prompts built with `_roots_line` must name the worktree's roots; the paths are relative, so the line text does not change, but the prompt must name the worktree as the repository.

### 4.5 Evidence and manifest

- `implementation/agent-output/`: `dispatch.json`, `schedule.json`, per-task `*.patch`, `*.ownership.json`, `worker-results.json` (with wave number, start/end time and cherry-picked SHA), and the copied answer and stabilization files.
- `delivery-manifest.json`: `hybrid_max_parallel` and `implementation_commits` (list of SHAs). `implementation_commit_sha` stays the HEAD after implementation.
- The Human Review Brief and the PR body list the waves and task commits (one line each).

### 4.6 Parallelism inside one worker (the provider)

Claude Code has two forms of parallelism of its own:

- **Several tool calls in one turn.** The model can read or edit several files in a single step, and Claude Code runs independent reads together. This already happens and needs no new permission. A sentence in the implementer profile ("batch independent file reads and edits into one turn") may encourage it a little. It saves seconds, not minutes, and the effect is not guaranteed, so treat it as a small extra, measured in step 7.
- **Subagents (the `Task`/`Agent` tool).** Not available to our workers, and this plan keeps it that way. CAO's `tool_mapping.py` maps `Task` and `Agent` to `execute_bash`, which no SDLC profile grants, so the provider launches with `--disallowedTools Task --disallowedTools Agent`. The implementer profile and governance also forbid launching agents. Enabling subagents would give back the concurrency without the controls: they share the worker's checkout (concurrent writers again), Python cannot see which subagent changed what, there are no per-task commits or evidence, and the parent agent rather than Python would decide what runs. A subagent's writes would still pass through the hook under the same terminal, so the source-root boundary would hold, but nothing else in this plan would.

Conclusion: parallelism stays in Python (worktrees and waves). Inside a worker, at most a prompt nudge towards batched tool calls.

## 5. Files to change

| File | Change |
|---|---|
| `.agentic-sdlc/cao/sdlc_workflows/hybrid.py` | `owns` validation, `build_schedule`, wave executor, worktree lifecycle, ownership report, per-task commits, cherry-pick with conflict rerun |
| `.agentic-sdlc/cao/sdlc_workflows/runtime.py` | Optional `answer_dir` (D8) |
| `.agentic-sdlc/cao/sdlc_workflows/delivery.py` | Input `hybrid_max_parallel`, `_hybrid_and_commit` commit rule, manifest fields, brief/PR lines |
| `.agentic-sdlc/cao/profiles/code-supervisor.md` | Parallel-aware allocation and `owns` (reinstall) |
| `.agentic-sdlc/cao/profiles/implementer.md` | One sentence: a hybrid worker may run in an isolated copy of the repository and should change only the files listed in its task, reporting any other change as a deviation; optional batching nudge (4.6) |
| `.agentic-sdlc/contracts/delivery-workflow.md` | Replace "Parallel dispatch is reserved for a future implementation" |
| `agentic-sdlc-docs/workflows/hybrid-delivery.md`, `delivery.md`, `architecture.md` | Waves, worktrees, ownership, new input, evidence tree, updated diagrams (Mermaid checked with the parser) |
| `README.md` (limits), `future-versions.md` ("Won't for now: parallel workers") | Describe what is now supported and what remains (for example no file-level enforcement by the hook) |
| `tests/test_hybrid.py`, `tests/test_deliver.py` | See section 6 |

`hardening-plan.md` lists "parallel workers, per-run delivery worktrees" as out of scope. It is your document; please decide whether to change it.

## 6. Steps (each verified live before the next)

1. **Spike: one worker in a worktree.** Use a throwaway clone. With `cao launch`, start one `sdlc_implementer` with its working directory set to a worktree under `.agentic-sdlc/runtime/...`. Check that: there is no trust or permission prompt; it can write `<worktree>/app/x` and `<worktree>/.agentic-sdlc/runtime/answer.json`; it is **denied** `<main>/app/x` and `<worktree>/.agentic-sdlc/cao/...`. Save the result in `agentic-sdlc-docs/verification/`. Stop and revisit D1/D8 if any of these fail.
2. **Pure logic.** Add `owns` validation and `build_schedule` with unit tests: waves for the PAY-DEMO-001 graph come out as `[T1,T3] [T2,T6] [T4,T5] [T7]`; overlap adds a dependency; chunking by `max_parallel`; deterministic order.
3. **Git mechanics** in a real temporary repository: worktree add/remove, ownership report (inside, outside, no change allowed), per-task commits, cherry-pick in task order, a forced conflict followed by a sequential rerun, cleanup after an exception, D9 hash mismatch.
4. **Executor.** Thread fan-out with `_run_json_contract_step` mocked: concurrent step ids, width `min(wave, 4)`, D10 failure semantics (the wave completes, nothing is cherry-picked, no integration), and a regression for waves of one task (existing tests updated only for per-task commits).
5. **Runtime `answer_dir`** with tests, then wire it into `delivery.py` (input, commit rule, manifest, brief).
6. **Profiles and contract.** Reinstall the profiles and bundle (profiles first, then `install_deliver.sh`). Coordinate with the profile-consistency branch, which also edits `code-supervisor.md`.
7. **Live run, small:** PAY-DEMO-001 in a throwaway clone with the default `hybrid_max_parallel=4`. Expect 4 waves and concurrent terminals in waves 1-3. Verification, review and the brief should be sound. Record wall-clock time against hybrid run 10.
8. **Live run, large:** your production project. Compare wall-clock time with the sequential run and look for ownership stops.
9. **Docs**, written to the workflow-docs conventions, then the verification record.

## 7. Expected gain and limits

- Saving per run ≈ (number of tasks - number of waves) × average worker time, capped by `max_parallel`. For PAY-DEMO-001: 7 tasks in 4 waves, about 3 worker durations (2-3 minutes at 33-60 s per worker). On a large project with longer workers the saving grows. The supervisor, integration, verification and review stay serial.
- Claude Code rate limits and machine load cap useful parallelism. This is why the default is 2.
- Siblings in a wave work from interface contracts, not each other's code, so integration may have more to reconcile. The independent review still checks the result.
- The hook still confines a profile to the source roots, not a task to its files. Ownership is a scheduling hint and a reported deviation (D5). The worktrees are what isolate the workers.
- Resuming a failed delivery is still not supported (an existing gap). Re-runs need a new run id and the existing branch moved aside.

## 9. Progress notes

- 2026-10-01, step 1 done: worktree spike passed (a worker in a worktree writes only there, the answer file in the worktree works, no prompt). Record: [parallel-workers-spike.md](../verification/parallel-workers-spike.md).
- 2026-10-01, step 2 done: `owns` validation in `validate_dispatch` (reuses `source_config`'s path normalizer) and `build_schedule` (waves, added dependencies for overlapping `owns`, transitive ancestors for D11). Tests: PAY-DEMO-001's graph gives `[T1,T3] [T2,T6] [T4,T5] [T7]`, width capped at 4, overlap serializes, determinism. Not installed: `owns` is required, and the supervisor is only asked for it in step 6, so the CAO bundle stays at `main` until then.
- 2026-10-01, step 3 done: new module `sdlc_workflows/worktrees.py` (worktree add/remove/prune, D9 trusted-file check, changed paths incl. renames, ownership report, per-task commit, task patch, cherry-pick that aborts and reports a conflict). 8 tests in `tests/test_worktrees.py` against real temporary repositories, including a forced conflict and cleanup with uncommitted changes.
