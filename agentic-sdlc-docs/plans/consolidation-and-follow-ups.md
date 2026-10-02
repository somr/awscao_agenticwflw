# Plan: consolidate the open branches, then the follow-ups

Status: **in progress** (written 2026-10-02). The user runs the parallel-workers trial on the production project
(their item 1); everything else below is done here, in this order. Progress notes are at the end.

## 1. Starting point

| Branch | Contains | State |
|---|---|---|
| `main` (`917051b`) | profile consistency, 30-minute answer wait, future-versions notes | local, 2 commits not pushed |
| `feature/parallel-hybrid-workers` (`479193f`) | parallel waves in worktrees, live-verified; main merged in | local |
| `feature/project-config` (`1334def`) | `agentic-sdlc-project.json`; the user's source remediation workflow (built on parallel steps 1-3, `8bc6985`) | local; not live-tested |

Installed in CAO: production `sdlc_deliver`, `sdlc_dev_plan`, `source_review` from `main`; `sdlc_deliver_parallel`
from the parallel branch; supervisor and implementer profiles from the parallel branch. Not installed:
`source_remediate`, `sdlc_source_fix_reviewer`.

## 2. Steps

1. **Consolidate.** Merge `feature/parallel-hybrid-workers` into `feature/project-config`. Expected conflicts:
   `hybrid.py` (project-file loader vs wave executor), `delivery.py` (extracted verification/remediation modules
   vs parallel input and commits), `worktrees.py`, tests (`test_hybrid.py` fixture must copy the project file),
   and docs. Both test modes must pass.
2. **Reinstall from the consolidated branch**, only when no run is active: all changed profiles (including the
   user's `remediator` change and the new `source-fix-reviewer`), then `sdlc_dev_plan`, `sdlc_deliver`,
   `source_review` (upgrade steps) and `source_remediate`. Remove `sdlc_deliver_parallel`. Check installed
   copies against the repository.
3. **Live checks in throwaway clones** (nothing published, nothing pushed):
   - write boundary from the project file: a worker may write a non-`app` root, not `app/`, not the project file;
   - full parallel Delivery on PAY-DEMO-001 with the project file (verification from it, waves, merges);
   - Source remediation on a local fixture review (first live run of that workflow).
4. **Merge into `main`**, after the checks pass.
5. **Parked gaps:** give Planning the configured source roots so plans stay inside them and the reviewer flags
   tasks outside them; make the retry commands in `delivery.md` root-neutral. Live planning check.
6. **Future work:** activity-based answer waiting (live spike first, then build and check); the "Planning
   Workflow 1" prompt text (shares the planning reinstall and live run of step 5).
7. **The user's documents:** update `hardening-plan.md` scope for parallel workers and per-run worktrees; review
   the edits made to `future-versions.md` and record resolved entries.
8. **Cleanup:** remove the throwaway clones and leftover scratch files.

Not in scope unless asked: pushing to GitHub.

## 3. Progress notes
- 2026-10-02, step 1 done: `feature/parallel-hybrid-workers` merged into `feature/project-config` (`3ebeb88`); conflicts in `hybrid.py` (imports, supervisor prompt now names the configured roots), `test_hybrid.py` (fixture copies the project file), four docs and `future-versions.md`. 285 tests pass in both modes.
- 2026-10-02, step 2 done: all 15 profiles and `sdlc_deliver`, `source_review`, `source_remediate` installed from `3ebeb88`; all match the repository. `sdlc_deliver_parallel` left installed for the user's production trial.
- 2026-10-02, step 3 done: write boundary from the project file, parallel Delivery and the first live Source remediation run all passed. Record: [consolidation-live.md](../verification/consolidation-live.md).
