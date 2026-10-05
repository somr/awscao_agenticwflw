# Plan: per-suite verification timeouts and configurable repair turns

Status: **planned 2026-10-05, not started**. Decisions in section 3 to confirm before step 1. Progress notes are at
the end.

## 1. Why

The production trial (Scala, Maven, 28 plan tasks) showed two limits of Delivery's verification:

- **Every verification command has a fixed 300-second timeout** (`VERIFICATION_TIMEOUT_SECONDS` in
  `verification.py`). A Maven build of a large project can take longer, and a timeout counts as a failure, which
  looks the same as a real build error.
- **There is exactly one repair turn.** After the first failed verification the implementer gets one turn
  (`implement-v1-repair-1`); if verification still fails the run ends `BLOCKED`. A build that fails in layers (a
  dependency problem hides a compile error, which hides a test failure) needs more than one turn, now that the repair
  turn sees each failed command's output.

## 2. Facts checked in the code (2026-10-05, `main` `93b2008`)

| Fact | Where | Consequence |
|---|---|---|
| One module-level timeout for every command | `verification.py` (`VERIFICATION_TIMEOUT_SECONDS = 300`), used by Delivery (`delivery.py`) and Source remediation (`source_remediation.py`) | The change lives in the shared runner; both workflows get it. |
| Suites are validated as lists of argument lists | `hybrid.load_specialists` (Delivery) and `source_remediation.remediation_configuration` (Source remediation, `application` suite only) | Both validators must accept the new form. |
| Hybrid verification is the deduplicated union of commands | `hybrid.verification_plan` | A command in two suites needs one timeout (rule in D2). |
| The project file allows only `version`, `source_roots`, `write_profiles`, `verification` | `source_config.validate_project_file` **and the hook's copy** in `.claude/hooks/restrict-write-scope.py` | A new top-level key makes the hook treat the file as invalid, which denies every source write, unless the hook changes in the same commit. The hook does not inspect the contents of `verification`. |
| The repair turn is hard-coded | `delivery.py` main, `implement-v1-repair-1` / `verify-v1-repair-1`; reason `verification_failed_after_one_repair_attempt` | The loop, step ids, reason and docs change. Resume already maps every verification reason to the `verification` resume point. |
| CAO input defaults must be literals | `INPUTS` (a packaging test checks it) | A new input needs a literal default. |
| **CAO kills a workflow script after 8,700 s wall-clock** (about 2 h 25 min) | CAO 2.5 `constants.WORKFLOW_SCRIPT_TIMEOUT`, a constant, not configurable | Longer timeouts and more repair turns make this easier to hit. A killed run writes no `BLOCKED` manifest and cannot be resumed. A long plan (many waves) can already come close. |

## 3. Decisions to confirm

| # | Question | Recommendation |
|---|---|---|
| D1 | Where does a timeout live? | **In the suite itself**: a suite may be an object `{"commands": [[...], ...], "timeout_seconds": 1800}` as well as today's plain list (which keeps the 300 s default). No new top-level key, so the hook needs no change; the parity test proves it still accepts the file. Alternative: a top-level `verification_timeouts` map, which needs the hook and its parity test changed in step. |
| D2 | A command that appears in two suites with different timeouts | Use the **longer** timeout. |
| D3 | Allowed range | 1 to 7,200 s per command; an invalid value stops the run before any agent (Delivery) or before the branch (Source remediation). |
| D4 | Where is the number of repair turns set? | A **workflow input** `verification_repair_turns`, default `1`, range 0 to 3, like `hybrid_max_parallel`. It is not security-relevant and may differ per run. Alternative or addition: a project-file default, which needs the hook and parity change of D1's alternative. |
| D5 | When does the repair loop stop early? | When a turn changes nothing (today's `repair_changed_nothing`), or when the failing commands and their output are the same as after the previous turn (no progress: new reason `repair_no_progress`). |
| D6 | New name for the final reason | `verification_failed_after_repair_turns`, with `repair_turns_used` in the manifest; the old name stays readable by resume. |
| D7 | Protect against CAO's 8,700 s bound | **Yes.** Python tracks the run's elapsed time. Before each verification (worst case: the sum of the command timeouts) and each repair turn, and at each wave boundary in hybrid implementation, it ends the run `BLOCKED` with `run_time_budget_exhausted` when the remaining time (minus a safety margin, proposed 10 minutes) is not enough. The resume point is the stage it stopped before, so the work is kept and `resume=true` continues it. |

## 4. Design

### 4.1 Timeouts (D1-D3)

- `verification.py`: a small parser that turns a suite (list or object) into `[{"command": [...], "timeout_seconds": N}]`,
  shared by every caller; `_run_verification` takes that list and records `timeout_seconds` per command in its
  result (so the manifest shows it). The timeout message in the log names the actual value.
- `hybrid.load_specialists` and `verification_plan` use the parser; the union keeps the longest timeout per command.
- `delivery.py` single mode and `source_remediation.remediation_configuration` use the parser for the `application`
  suite.
- `resume.py` needs nothing: verification commands are recomputed from the current registry on resume, so a raised
  timeout applies to the resumed run (the intended way to recover from a timeout).

### 4.2 Repair turns (D4-D6)

- Input `verification_repair_turns` (int, default `1`, 0 to 3).
- Loop: verify, then up to N turns of repair-and-verify. Step ids `implement-v1-repair-<k>`, labels
  `verify-v1-repair-<k>`. Each turn's prompt has the latest failed commands with their `output_tail`, and the earlier
  turns' deviations, so the agent does not repeat itself.
- Stop early per D5. Each turn's commit is recorded in `delivery_commits`; the manifest gets `repair_turns_used` and
  a list of per-turn summaries (`implementer_repair_summaries`; the existing single field stays for the first turn or
  is replaced, decided in step 2).
- `totals.repair_turns` counts every turn across resumes (resume D7).
- Remediation keeps its current rule: a remediation round that breaks verification ends `BLOCKED` without repair.

### 4.3 Run-time budget (D7)

- Record the start time in `main()`; a helper `remaining()` returns `8700 - elapsed - margin`.
- Before verification and before each repair turn: worst case = sum of the command timeouts (plus, for a repair turn,
  the 30-minute step budget). Not enough left: `BLOCKED`, `run_time_budget_exhausted`, resume point `verification`.
- In `run_hybrid`, before each wave: if fewer than the wave's step budget remains, raise; the existing handler records
  the progress, and the resume point is `implementation`.
- The 8,700 s value is CAO's, so keep it as one named constant with a comment pointing to CAO, and a test that fails
  if someone sets it higher.

## 5. Steps

Each step: tests in both modes (bundle and source), docs for what changed, then a live check in a throwaway clone with
the branch's bundle installed as a separate workflow (as for resume), so `sdlc_deliver` stays equal to `main`.

1. **Per-suite timeouts** (D1-D3). Tests: object and list forms; a `sleep` command passing with a longer timeout and
   failing at a short one; the longest-timeout rule; invalid values refused before any agent; the hook still widens
   with an object-form project file (parity test); Source remediation reads it. Live: a suite whose command sleeps
   longer than 300 s passes with `timeout_seconds` set.
2. **Configurable repair turns** (D4-D6). Tests: 0, 1 and 3 turns; early stop on no change and on no progress; step
   ids, commits and totals; resume after `verification_failed_after_repair_turns`. Live: a fixture whose verification
   fails in two layers (a compile error that hides a failing test), fixed over two turns.
3. **Run-time budget guard** (D7). Tests with a patched clock: stops before verification, before a repair turn and at
   a wave boundary, each ending `BLOCKED` with the right resume point, then resumes. Live: not needed beyond a short
   check with a temporarily lowered limit in a test bundle, if a test cannot cover it.
4. **Docs**: `delivery.md` (inputs, registry reference with the suite object form, stop reasons, the CAO time bound),
   `hybrid-delivery.md` (verification toolchain: timeout), `source-remediation.md` (configuration), README
   "Configure for your project", and a verification record.

Not in scope: changing CAO's bound, parallel verification commands, per-command environment variables, sandboxing
verification, repair after a failed remediation round.

## 6. Progress notes
