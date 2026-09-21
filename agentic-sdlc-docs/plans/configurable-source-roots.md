# Plan: configurable source roots for Delivery

Status: **implemented and live-verified 2026-09-21** (written 2026-09-20 against `main` at `abcea97`; 200 tests pass in both modes). Not committed yet. Follow-ups are listed in section 6b.
Scope: where Delivery's implementer, remediator and hybrid workers may write, commit and verify generated source. Today that is the single hardcoded directory `app/`.

## 1. Problem

Real projects put generated source elsewhere: a module in a multi-module build (`billing/src`, `billing/test`), several roots, or a different layout. `app/` is baked into the security boundary and the workflow, so the workflows cannot be pointed at such a project.

Passing a path to the agent is not enough. The write-scope hook is what confines the agents, and it hardcodes the roots, so an implementer told to write into `billing/src` would be **denied**. The setting must reach the hook, which makes this a security-relevant change.

## 2. Inventory: where `app` is hardcoded (at `abcea97`)

| Area | Location | What |
|---|---|---|
| Hook (the boundary) | `.claude/hooks/restrict-write-scope.py` `WIDENED_WRITE_ROOTS` (lines ~69-72) | `{"sdlc_implementer": ["app"], "sdlc_remediator": ["app"]}`; also docstring line ~12 |
| Delivery git pathspecs | `.agentic-sdlc/cao/sdlc_workflows/delivery.py` lines 202, 205 (`_implement_and_commit`), 214, 220, 222 (`_hybrid_and_commit`), 642 (hybrid preflight in `main`), 828, 831 (remediation) | 5 x `git status --porcelain -- app`, 3 x `git add -- app`, plus their error messages ("left no changes under app/", "requires a clean app/ tree") |
| Agent prompts | `delivery.py` line ~170 (`build_implementer_prompt`), line ~285 (repair prompt); the remediator prompt names no path | "against the application source under app/", "Fix the implementation under app/" |
| Verification, single mode | `delivery.py` `VERIFICATION_COMMANDS` (line ~231) | `compileall app`, `unittest discover -t app -s app/tests` |
| Verification, hybrid mode | `.agentic-sdlc/cao/specialists.json` `verification` | Already configurable per suite; the defaults point at `app/...` |
| Profiles | `implementer.md` lines 20, 27, 28, 36, 51; `remediator.md` lines 19, 29, 44; `pr-reviewer.md` line 61 (example path only) | "under `app/`", "Writes are restricted to `app/**`", example JSON paths |
| Docs | `agentic-sdlc-docs/workflows/hybrid-delivery.md` lines ~24, 80, 102 (Scenario 1 step 4 says to edit `WIDENED_WRITE_ROOTS`; Scenario 3 says to update the hook), `architecture.md` line ~70, `reference/agent-profiles.md` line ~10 | |
| Tests | 8 test files assume `app` (12 lines in `test_workflow_integration.py`, 11 in `test_deliver.py`, 8 in `test_restrict_write_scope.py`, and a few in the planning tests) | |

Not affected: planning, the contracts, policies and templates (no `app` mentions), `_compute_delivery_diff` (diffs the whole branch), the hybrid dispatch code itself.

## 3. Decisions (proposed 2026-09-20; confirm or change before starting)

| # | Decision | Rationale |
|---|---|---|
| D1 | Config lives in the existing trusted `.agentic-sdlc/cao/specialists.json`, as two new optional top-level keys | Keeps the file count down. Agents already cannot edit `.agentic-sdlc/cao` (hook deny root), so it has the same trust as the registry. |
| D2 | Schema: `source_roots` (list of relative directories) and `write_profiles` (profile name -> `null` for all source roots, or a non-empty subset list) | Write `app` once; still allows a specialist worker limited to its own module. A new worker profile becomes config-only, with no hook edit. |
| D3 | Backward compatible: when the keys or the file are absent the defaults equal today's behaviour (`["app"]`; `sdlc_implementer` and `sdlc_remediator` -> all roots) | Existing repos and the sample app keep working unchanged. |
| D4 | Fail closed: a present but invalid config means **no widening** in the hook (deny with a clear reason) and a `WorkflowContractError` before any agent starts in Delivery | A typo must never widen access. |
| D5 | The hook keeps its own small copy of the validator (stdlib only, standalone), with a **parity test** that runs both validators over the same corpus | The hook must stay a standalone script; the bundle cannot be imported by it. |
| D6 | Single mode reads verification from the registry's `application` suite instead of the hardcoded constant | One place defines verification, so `source_roots` and the commands cannot disagree. Confirm this changes `_run_verification`'s default and the tests that pin it. |
| D7 | No per-run input in v1. A run may not widen the configured roots | The hook is a separate process per tool call and cannot see workflow inputs, and a model-written plan must not set the boundary. Narrowing per run is a later option. |
| D8 | Forbidden roots: `.`, anything equal to, inside or containing `.git`, `.claude`, `.agentic-sdlc`, `agentic-sdlc-records`, `agentic-sdlc-docs`, `agentic-sdlc-local-inputs` | The SDLC's own folders are not generated source. The existing hook deny list still wins first. |
| D9 | `write_profiles` may not list the SDLC's read-only profiles (`sdlc_code_supervisor`, `sdlc_pr_reviewer`, the four planning profiles, `sdlc_source_*`) | A typo in the config must not give the supervisor or a reviewer write access. |

## 4. Design

### 4.1 Config and validation (single source of truth: one function, mirrored in the hook)

```json
{
  "version": 1,
  "source_roots": ["billing/src", "billing/test", "shared/lib"],
  "write_profiles": {"sdlc_implementer": null, "sdlc_remediator": null, "sdlc_java_persistence": ["billing/src"]},
  "workers": {...}, "skills": {...}, "verification": {...}
}
```

Rules (all enforced by the validator):

- `source_roots`: non-empty list, at most 16, each a non-empty string. Relative POSIX paths only: no absolute paths, no `..`, no glob characters (`* ? [`), no backslashes; normalized (no trailing slash, no `./`), unique, and none may overlap a forbidden root (D8).
- `write_profiles`: non-empty object; values `null` or a non-empty list whose entries are each equal to or inside a `source_roots` entry (R1); no read-only profile (D9).
- Cross-check in the registry loader: every `workers[*].profile` must appear in the effective `write_profiles`, otherwise the worker could never write. This is a load error.
- At use time (hook and Delivery preflight), each root's `realpath` must stay inside the repository's `realpath`. A symlinked root that leaves the repo is rejected. Roots that do not exist yet are allowed (a worker may create a new module).

### 4.2 Hook

- Read `.agentic-sdlc/cao/specialists.json` relative to the worker's working directory only when it is deciding on widening (after the deny roots and the runtime root, and after the CAO profile lookup).
- File absent -> defaults (D3). File present but unreadable, malformed or failing validation -> no widening and a deny reason that says the config is invalid. Never fall back to defaults on an invalid file.
- `widened(P) = source_roots` when `write_profiles[P]` is `null`, else that list. Deny roots keep precedence. The deny message lists the effective roots.

### 4.3 Delivery

- New small module `sdlc_workflows/source_config.py` (bundled): `validate_source_config(raw)` and `load_source_config(repo)`; `load_specialists` calls it for the cross-check.
- New helpers in `delivery.py`: `_source_roots(repo)` (validated, realpath-checked) and `_existing_roots(repo, roots)`. Replace the 8 pathspec sites and their messages. Verified in a `git` 2.34 spike (2026-09-20): `git status --porcelain -- a nonexistent` works, but **`git add -- a nonexistent` fails** with exit 128 ("pathspec did not match any files"), so `add` must receive only the roots that exist. A new, untracked module directory adds correctly, and a root with tracked files but no changes next to a changed root is fine.
- Preflight in `main()` in both modes, before the READY manifest: load and validate the config. Failure -> `WorkflowContractError`, no agent runs.
- Prompts (`build_implementer_prompt`, the repair prompt, the remediator prompt) gain a line: "Source roots (write only under these): ...". The hybrid supervisor and workers inherit it through the base prompt.
- Single mode uses the registry `application` suite (D6) and the constant is removed.

### 4.4 Profiles and docs

- `implementer.md`, `remediator.md`: replace "under `app/`" with "under the source roots named in your task prompt (the write-scope hook enforces them)"; make the example JSON paths generic. `pr-reviewer.md`: example path only.
- Docs: rewrite hybrid-delivery Scenario 1 step 4 (config, not the hook) and the Scenario 3 sentence about write roots; update `architecture.md` and `reference/agent-profiles.md` ("the configured source roots, default `app/`"); add a multi-module example and a short "pointing the workflows at your project" section to `hybrid-delivery.md`.
- The hook docstring and `planning.md`'s hook section: describe the config and the fail-closed rule (the repo expects security decisions to be written down beside the code).

## 5. Files to change

| File | Change |
|---|---|
| `.claude/hooks/restrict-write-scope.py` | Replace `WIDENED_WRITE_ROOTS` with a config reader and validator (D4, D5, D8, D9); update docstring |
| `.agentic-sdlc/cao/sdlc_workflows/source_config.py` | New: validator and loader |
| `.agentic-sdlc/cao/sdlc_workflows/hybrid.py` | `load_specialists`: cross-check worker profiles against `write_profiles` |
| `.agentic-sdlc/cao/sdlc_workflows/delivery.py` | Pathspec helpers, 8 sites, messages, prompts, preflight, single-mode verification (D6) |
| `.agentic-sdlc/cao/specialists.json` | Add explicit `source_roots` and `write_profiles` for the sample app (documented defaults) |
| `.agentic-sdlc/cao/profiles/{implementer,remediator,pr-reviewer}.md` | Generic wording (reinstall afterwards) |
| `agentic-sdlc-docs/workflows/hybrid-delivery.md`, `architecture.md`, `reference/agent-profiles.md`, `workflows/planning.md` | See 4.4 |
| `tests/test_source_config.py` (new), `test_restrict_write_scope.py`, `test_deliver.py`, `test_hybrid.py`, `test_workflow_integration.py`, `test_workflow_packaging.py` | See section 6 |

## 6. Milestones (each ends green in both test modes, then a live check where noted)

Baseline: 167 tests pass in both modes (`python3 -m unittest discover -s tests` and with `SDLC_TEST_SOURCE=1`).

| Milestone | Work | Verification |
|---|---|---|
| M0 confirm | Confirm D1-D9 (especially D6 and D8). The git spike is already done (4.3). | Done 2026-09-21: the user said "resume the job" without changing anything, so D1-D9 stand as written (D6 and D8 were not discussed individually; both are reversible while nothing is committed). One refinement, R1: a `write_profiles` entry may be **equal to or inside** a source root (for example `billing/src` inside `billing`), not only equal to one. |
| M1 validator | `source_config.py`, the hook's mirrored validator, defaults. No behaviour change yet. | `tests/test_source_config.py`: a corpus of valid and invalid configs (absolute, `..`, glob, backslash, `.`, duplicates, overlaps with each forbidden root, non-string entries, too many roots, unknown profile lists, read-only profile listed, subset not in `source_roots`); the same corpus through the hook's copy (parity test); bundle-packaging test still passes. |
| M2 hook | The hook reads the config and applies D4. Add a `cwd` parameter to `run_hook` in the tests (today it always runs in the repo root) so temp repos with their own config can be used. | Hook tests: multi-root allow; outside roots denied; per-profile narrowing; unknown profile; config absent -> defaults; malformed or invalid -> denied (fail closed); symlinked root escaping the repo not widened; deny roots still win; supervisor and reviewer still cannot write; `.agentic-sdlc/cao/specialists.json` is unwritable for every profile. Mutation-check each guard. |
| M3 delivery | Helpers, the 8 sites, prompts, preflight, single-mode verification (D6). | Real temp-git tests: two roots, one not yet existing, a new untracked module, commits contain only files under the roots, changes outside the roots neither block the clean-tree check nor get committed, invalid config stops the run before any agent, single mode uses the registry suite; one integration scenario with custom roots. |
| M4 profiles and docs | Section 4.4; the worker-profile cross-check test. | Link check; grep for leftover `app/` outside the sample app and the plans. |
| M5 live | Reinstall (profiles first: `implementer`, `remediator`, `pr-reviewer`, then `install_deliver.sh`), verify installed == repo. Then the three live checks below. | See below. |

### Live checks (in a throwaway clone under `$HOME`, not `/tmp`, with no remote)

Reuse the pattern from `agentic-sdlc-docs/verification/hybrid-delivery-live.md`: clone, remove the remote, restore the originally approved PAY-DEMO-001 plan records from `agentic-sdlc-local-inputs/PAY-DEMO-001/records/` into `agentic-sdlc-records/PAY-DEMO-001/`, commit that in the clone, run with `--wait --json`. The approved plan names files under `app/`, so keep the `app` layout and set the roots to **two narrower roots**, which proves multi-root behaviour without changing the hash-bound plan.

1. **Positive:** `source_roots: ["app/payment_service", "app/tests"]`; run hybrid Delivery. Expect `AWAITING_HUMAN_REVIEW`; the commit touches only files under those roots; `app/README.md` untouched.
2. **Negative (proves live enforcement):** `source_roots: ["app/tests"]` only, so the plan's production-code edits are outside the roots. Expect the hook to deny the writes and Delivery to end `BLOCKED` (implementation failure or "left no changes"), not a silent success.
3. **Hook against a real terminal:** pipe-test the hook with a real CAO terminal id for `sdlc_implementer` (reuse the Stage 8 probe pattern in the memory notes) against a custom config: one allowed path, one denied.

Finally delete the clone (after recording results in a verification doc), as with the hybrid run.

### 6a. Progress notes (2026-09-21)

- **M1 done.** `sdlc_workflows/source_config.py` (validator, loader, symlink-safe `resolve_source_roots`); mirrored validator in the hook; registry cross-check in `hybrid.load_specialists`. `tests/test_source_config.py` runs one corpus (valid and 47 invalid configs) through the modular package, the deployed bundle and the hook's copy; plus loader and resolver tests. Six mutations were each caught.
- **M2 done.** The hook no longer has `WIDENED_WRITE_ROOTS`: it reads the registry only when deciding on widening, fails closed on anything invalid (never the defaults), and treats a root that escapes the repo as invalidating the whole config. `run_hook` in the tests gained a `cwd` parameter. The wide matrix runs in-process against the hook's `main()` (`run_hook_in_process`, profile lookup stubbed) because a subprocess per check took the file from 6 s to 55 s; the core behaviours still run as real subprocesses. Five mutations were each caught.
- **M3 done.** `delivery.py` has no hardcoded `app` left: helpers `_source_roots` (validates config and real paths), `_existing_roots`, `_source_changes` (empty pathspec returns "" because it would otherwise list the whole repo), `_stage_source_changes`, `_roots_line`; 8 pathspec sites, 3 prompts, the preflight in `main()` (both modes, before the READY manifest, which now records `source_roots`), and `_implement_and_commit` validates before the agent runs. D6 done: `VERIFICATION_COMMANDS` is removed, `_run_verification` requires its commands, single mode uses the registry's `application` suite (and refuses to start without it). `specialists.json` now states `source_roots` and `write_profiles` explicitly. New: 8 delivery tests on real temp git repos, 3 integration tests, and the existing hybrid flow now runs with a second, non-existent root so repair and remediation exercise every `git add` site. Seven mutations were each caught.
- **M4 done.** `implementer.md` and `remediator.md` now say "the source roots named in your task prompt (project-configured; default `app/**`)" and tell the agent to report a needed change outside the roots as a deviation; `pr-reviewer.md` and all example JSON paths are generic. Docs: new section "Pointing the workflows at your project" in `hybrid-delivery.md` (config keys, rules, multi-module example, fail-closed behaviour, the plan-names-outside-roots caveat, upgrade note), Scenario 1 steps 4-5 and Scenario 3 step 5 rewritten (no hook edit for a new specialist), `architecture.md`, `reference/agent-profiles.md`, the delivery contract, and the hook section of `planning.md` (trust rule, parity test). A stale statement that single mode keeps "fixed Python checks" was corrected.
- **M5 done.** Reinstalled the three profiles and the bundle, then three live checks (record: `agentic-sdlc-docs/verification/configurable-source-roots-live.md`): (1) hybrid Delivery with roots `app/payment_service` + `app/tests` ended `AWAITING_HUMAN_REVIEW`, two commits (implementation and a real remediation round) touching only files under the roots; (2) with roots `app/tests` only the agents stayed inside the roots and reported the production tasks as deviations, production code was untouched, and the run ended `failed` at the verification-repair step; (3) the hook run against a real CAO terminal id (created through `POST /sessions`, deleted afterwards) allowed and denied exactly as designed, picked up a registry edit without a restart, and granted nothing on a malformed registry. Deviation from the plan: check 2 did not exercise the hook (the agents complied), so check 3 was made the proof of enforcement; the terminal was created through CAO's API rather than `cao launch`.
- **Refinements to the plan:** R1 (profile roots may be inside a source root); `:` is also rejected in roots because it starts git pathspec magic (`:(top)`, `:!x`); `write_profiles` may not be an empty object.

### 6b. Follow-ups (not done)

- **`failed` versus `BLOCKED`.** A verification-repair or remediation step that changes nothing inside the roots raises and ends the run `failed`, leaving the delivery manifest at `IMPLEMENTED`. Catch it in the same way as the hybrid implementation step and end `BLOCKED` with a reason such as `plan_requires_changes_outside_source_roots`.
- **The planner does not know the roots.** Give the planning contract the roots and let the plan reviewer flag tasks that touch paths outside them, so the problem is found before Delivery.
- **A real multi-module project.** The registry's verification suites still use the sample app's `app` commands; try a project with its own build tool and a specialist profile limited to part of a root.

## 7. Risks and open questions

- **The hook is the security boundary.** Mitigations: fail closed; validator parity test; deny roots keep precedence; the config file is inside a deny root; D8 and D9 guard typos. Write the trust decision into the hook docstring and the docs.
- **Duplicated validator.** Accepted (D5); the parity test is what keeps the two honest. Any new rule must be added to both.
- **Symlinks and case-insensitive file systems.** Realpath is checked at use time; Windows and case-insensitive paths are out of scope for v1.
- **Plans that name paths outside the roots** will fail at Delivery with a confusing "left no changes". Follow-up, not in this plan: give the planner the roots in its contract and let the reviewer flag plan tasks outside them.
- **Single-mode change (D6)** touches `_run_verification`'s default and the tests that pin it.
- **Profiles and the bundle must be reinstalled**, and the installed copies compared with the repo, before any live run. The CAO registry is machine-wide.

## 8. Non-goals

Parallel worker dispatch, per-run overrides of the roots, roots taken from the model-written plan, multi-repository projects, Windows paths, renaming `app/`, `tests/` or other folders.

## 9. Resume checklist for the next session

- `git status -sb`, `git log --oneline -3`, `git branch -vv`, `git worktree list`: the user develops in parallel and changes `main` between commands, so re-check before acting.
- `main` was at `abcea97`, level with `origin/main`; 167 tests passed in both modes; the installed CAO bundles and eight profiles matched the repo after the folder rename. Re-compare (`build_workflow.py ... --output` vs the installed file; `cmp` the profiles) before relying on that.
- Untracked and left out of commits: `.gitignore.additions`, `tmux-*.log`. `sdlc/PAY-DEMO-001` is deliberately unmerged (the only copy of the approved implementation).
- Pitfalls learned: never chain `git add` with a path that may not exist (`git add a b && ...; git commit` committed the wrong things once); `git merge` has no `-F -`; CAO refuses `/tmp` as a repository root; use `cao workflow run --wait --json` to see the output; detect completion with `cao workflow status`, not `pgrep -f`; install profiles before the bundle.
- Start with M0 (confirm D1-D9), then M1.
