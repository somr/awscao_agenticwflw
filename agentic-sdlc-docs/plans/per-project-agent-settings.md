# Plan: per-project provider and model per profile

Status: **steps 0-1 done, step 2 code done (2026-11-18); unblocked 2026-11-19 by the frozen workflow library (rebased onto `main` `36c164a`), step 2 done 2026-11-19; steps 3-4 next; D1-D10 agreed 2026-11-18.** Progress notes go at the end.

## 1. Why

Every agent runs with the same provider (Claude Code) and the model Claude Code picks by default. A project cannot
say "the PR reviewer uses a stronger model" or "the mapper runs on a cheap model". Installed profiles are shared by
every project on the machine, so the choice cannot live in the profiles.

The goal: a project declares, in `agentic-sdlc-project.json`, the provider and model for each profile (and a default
for all of them). Every workflow applies the settings to every agent call, records what each call actually used, and
behaves exactly as today when nothing is declared. Other providers come later, and only together with write
enforcement that has been checked for that provider. Effort is not part of this plan (D5).

## 2. Facts checked (2026-11-18, `main` `7cb9714`, CAO 2.5.4 (server and installed package; `cao --version` prints 2.5.0), Claude Code 2.1.327, codex-cli 0.170.0)

| Fact | Where | Consequence |
|---|---|---|
| All 18 profiles say `provider: claude_code`; none sets `model:` | `.agentic-sdlc/cao/profiles/*.md` | Today the model is Claude Code's default. |
| One global `PROVIDER = "claude_code"` | `runtime.py:24` | Used by the one `step()` call and by two manifests. |
| Every agent call goes through `_run_json_contract_step` → `_run_stabilized_step` → `step(PROVIDER, agent, …)` | `runtime.py`; callers in `planning.py`, `delivery.py`, `hybrid.py`, `remediation.py`, `source_review.py`, `source_followup.py`, `source_remediation.py` | One routing point covers all five workflows, including parallel calls (`run_parallel_steps`). |
| `cao_workflow.step(provider, agent, prompt, *, recovery, step_id, timeout, **opts)` passes `opts` into the run-step request body | installed `cao_workflow/__init__.py` | Extra fields reach the server without changing CAO. |
| `RunStepRequest` accepts `model`: a model for this call, applied to a freshly created terminal | `cli_agent_orchestrator/api/main.py:435` | Model can be set per call. This project never reuses terminals. |
| `model` is part of the run-step idempotency hash | `services/terminal_service.py` (~line 469-600) | A replayed step must use the same model; settings read once per run keep that true. |
| The Claude Code adapter turns the call's model into `--model <m>`; other adapters (Codex, Antigravity, …) have their own model handling | `providers/claude_code.py` ~400-450, `providers/*.py` | `model` is provider-neutral at the API; whether each adapter honours the per-call value is checked per provider. |
| Effort exists only as provider-specific settings (`claudeConfig.effort`, Codex `model_reasoning_effort`), not in the run-step API, and not every provider has it (e.g. Antigravity) | `models/agent_profile.py`, `providers/claude_code.py` | Effort is left out (D5). |
| The project file allows only `version` + `PROJECT_KEYS` (`source_roots`, `write_profiles`, `verification`, `review_knowledge`); the write-scope hook has its own copy, checked by a parity test | `source_config.py:23`, `.claude/hooks/restrict-write-scope.py:98`, `tests/test_source_config.py` | A new key must be added to both in the same commit, or the hook treats the project file as invalid and denies every source write. |
| The project file is a forbidden root, so no agent can write it | `source_config.FORBIDDEN_ROOTS` | Agents cannot change their own model. |
| Planning and Delivery manifests record one global `"provider"`; Source review and follow-up record no provider | `planning.py:1419`, `delivery.py:898` | Records need the actual settings per profile. |
| Delivery resume starts a new CAO run and reuses evidence from the earlier one | `resume.py`, `delivery.py` | A resumed run may read different settings than the first run; that must be visible (D8). |
| Non-Claude adapters: Codex defaults to `--yolo`, Antigravity to `--dangerously-skip-permissions`; the write-scope hooks are Claude Code hooks | `agentic-sdlc-docs/reference/agent-profiles.md` "Change one profile …" | Allowing another provider without its own verified enforcement would remove the write boundary (D3). |
| Codex is installed for testing (limited allowance); `gpt-6-luna` is listed as "fast and affordable" | `~/.codex/models_cache.json` | Any Codex check uses `gpt-6-luna` and stays to a minimal prompt. |
| Installed workflows run from a frozen library; profiles are installed machine-wide | `build-and-install.md` | Each code step ends with a reinstall of all five workflows (a new library). No profile reinstall is needed for the model. |

## 3. Decisions

| # | Question | Decision |
|---|---|---|
| D1 | Where are the settings declared? | **Agreed 2026-11-18.** A new project-file key **`agent_settings`** in `agentic-sdlc-project.json` (trusted checkout, forbidden root, already read by every workflow). Not in `specialists.json` (shared across projects) and not in the profiles (machine-wide). |
| D2 | Shape | **Agreed 2026-11-18.** `{"default": {"provider": "claude_code", "model": "sonnet"}, "profiles": {"sdlc_pr_reviewer": {"model": "opus"}}}`. Each entry may have `provider` and `model`, both optional, nothing else. A profile entry overrides `default` field by field; a field set nowhere means "as today" (provider `claude_code`, no model sent). |
| D3 | Which providers are allowed? | **Agreed 2026-11-18 ("for now").** Only `claude_code`. Any other value is rejected before any agent runs, with a message pointing to the "Change one profile …" section. The field exists now so the format doesn't change when another provider is added with its own enforcement (section 6). |
| D4 | Validation | **Agreed 2026-11-18.** `profiles` keys must be one of the 18 known profile names (new constant `KNOWN_PROFILES`; a test checks it against the `name:` of every profile file). `model` is a string matching `^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,99}$` (aliases like `sonnet`, ids like `claude-opus-5-5` or `claude-opus-5-5[1m]`, other providers' ids like `gpt-6-luna`); the workflow does not check that the account can use the model, and a wrong model fails at the agent's first turn. Unknown keys are errors. Any error stops the run before the first agent. |
| D5 | Effort | **Agreed 2026-11-18: not configurable.** It is provider-specific and not portable (Antigravity has no equivalent), so `effort` is an unknown key and rejected. The machine-wide setting keeps applying. |
| D6 | When are the settings read? | **Agreed 2026-11-18.** Once, at the start of each workflow run, from the trusted checkout (Source review: `repository_root`, never the PR). The runtime holds them for the whole run, so retries, repair turns and parallel calls all use the same values, which keeps CAO replay consistent. |
| D7 | What is recorded? | **Agreed 2026-11-18.** Every step's evidence folder gets `<step_id>.agent-settings.json` (`profile`, `provider`, `model`, and where each came from: `profile`, `default` or `unset`). Planning and Delivery manifests keep `"provider"` and gain `"agent_settings"` (the resolved settings per profile used in the run plus the project file's `sha256`); Source review and follow-up add the same block to their run record. The approval brief / human review brief shows one line per profile that is not `unset`. |
| D8 | Delivery resume | **Agreed 2026-11-18.** The resumed run reads the current settings (so a human can, say, pick a stronger model after a failure) and records `agent_settings_changed: true` with the earlier values when they differ. No refusal. |
| D9 | Without `agent_settings` | **Agreed 2026-11-18.** No `model` is sent; prompts, steps and records are exactly as today except for the new records (which say `unset`). Tests prove the `step()` arguments are unchanged. |
| D10 | Global `PROVIDER` | **Agreed 2026-11-18.** It stays as the default when no provider is declared. `_run_stabilized_step` uses the resolved setting instead of reading it directly. |

## 4. Design

### 4.1 Configuration (D1-D5)

- `source_config.py`: `PROJECT_KEYS` gains `agent_settings`.
- New module `agent_settings.py` (pure Python): `KNOWN_PROFILES`, `ALLOWED_PROVIDERS`, `validate_agent_settings(raw)`, `resolve_agent_settings(raw) ->
  {profile: {"provider", "provider_from", "model", "model_from"}}` for all known profiles, `step_options(resolved, agent) ->
  (provider, opts)` where `opts` holds `model` only when set.
- `.claude/hooks/restrict-write-scope.py`: same `PROJECT_KEYS` (parity test). The hook does not inspect the entries.

### 4.2 Routing (D6, D10)

- `runtime.py`: `set_agent_settings(resolved)` once per run (module-level; the bundle runs one workflow per process),
  read by `_run_stabilized_step`, which calls `step(provider, agent, prompt, …, **opts)` and writes the evidence file
  (D7). A call before `set_agent_settings` uses today's behaviour, so tests and helpers that don't set it still work.
- Each workflow entry (`planning.py`, `delivery.py`, `source_review.py`, `source_followup.py`,
  `source_remediation.py`) loads the project file it already reads, validates and sets the settings before its first
  agent.

### 4.3 Records (D7, D8)

- Manifests and run records as in D7; Delivery resume compares with the earlier manifest (D8).

## 5. Steps (each ends with tests, reinstall and a live check)

0. **Spike, no code in the repo.** With a scratch profile and `cao workflow run` of a two-line script, check that
   `step(..., model="haiku")` launches `claude --model haiku` (terminal log), the agent works on that model, and the
   write-scope hook still fires in that terminal. Optional and minimal (limited allowance): the same call with
   `provider="codex"`, `model="gpt-6-luna"` and a one-line read-only prompt in a scratch directory, only to confirm the
   per-call model reaches the Codex adapter so the format is provider-neutral; no workflow uses Codex. Record in
   `agentic-sdlc-docs/verification/agent-settings-spike.md`.
1. **Configuration**: `agent_settings.py`, `PROJECT_KEYS` in both copies, `KNOWN_PROFILES` + its test, validation
   tests (every D4 rule, `effort` rejected, `provider` other than `claude_code` rejected). No behaviour change.
2. **Routing and evidence**: runtime changes, the five workflow entries, per-step evidence, D9 tests (unchanged `step()`
   arguments without the key). Reinstall all five workflows. Live: a Planning run on the fixture with
   `sdlc_plan_reviewer` on a different model; check the terminal logs, the evidence files, and that the other
   profiles kept their settings.
3. **Records and resume**: manifests / run records, brief line, D8. Live: a Source review run on a closed PR (cheap,
   read-only) with a `default` entry; check `code-review.json` and the evidence.
4. **Docs**: `reference/project-rules.md` or a new `reference/agent-settings.md` for the key; rewrite step 4 of
   "Change one profile …" in `agent-profiles.md` (routing now exists; only enforcement is missing); `build-and-install.md`
   (no profile reinstall needed for the model), the workflow guides' Configuration sections, `architecture.md`.

## 6. Out of scope (later)

- **Other providers.** Each needs its own design for answer delivery and write enforcement (the "Change one profile …"
  section lists what to prove), a per-provider allow-list in `agent_settings.py`, and a live check of denied writes.
  `sdlc_implementer` is also used by hybrid workers, integration and verification repair, so switching it switches
  all of those. Codex tests use `gpt-6-luna` (limited allowance).
- **Effort** (D5), unless a provider-neutral way appears.
- Per-ticket or per-run overrides through workflow inputs.
- Checking at start that the account can use the declared model.

## 7. Progress notes

- 2026-11-18, step 0: per-call `model` reaches Claude Code (`--model haiku`, agent ran as `claude-haiku-5-5`) and
  Codex (`--model gpt-6-luna`); the write-scope hook still denied a Claude agent's write. Record:
  [agent-settings-spike.md](../verification/agent-settings-spike.md).
- 2026-11-18, step 1: `agent_settings.py` (validation, resolution, `step_options`, `load_agent_settings`),
  `agent_settings` in `PROJECT_KEYS` of `source_config.py` and the hook, tests in `tests/test_agent_settings.py`
  (`KNOWN_PROFILES` against the profile files, every D4 rule, `effort` and non-Claude providers rejected), the
  parity corpus and a hook test (the key changes no write scope). No workflow reads it yet. 432 tests pass in both
  modes. `KNOWN_PROFILES` lives in `agent_settings.py`, not `source_config.py`, since only this module uses it.
- 2026-11-18, step 2 (code): `runtime.configure_agent_settings(repo)` called by all five workflow entries before
  their first agent; `_run_stabilized_step` sends the resolved provider and, when set, `model`, and writes
  `<step_id>.agent-settings.json`. 436 tests pass in both modes. Reinstalled `sdlc_dev_plan`, `source_review` (upgrade
  procedure, rollback copy kept) and `source_remediate`. **`sdlc_deliver` and `source_followup` were refused**: CAO
  2.5.4 (installed 2026-11-18 22:51, after the last follow-up install) caps a workflow script at 131,072 characters
  (`SCRIPT_LINT_MAX_SOURCE_CHARS`, a constant, checked at validation and at run start). The bundles on `main` were
  already over it (deliver 166,972, source_followup 138,284), so the installed copies of those two cannot run on 2.5.4
  either. Removing docstrings alone leaves deliver at about 140,000. Needs a decision before Delivery or Follow-up
  can run again.
- 2026-11-19: the size limit is solved by the frozen workflow library
  ([its plan](workflow-library-and-prompt-files.md), merged to `main` `36c164a`). This branch was rebased onto it
  (one conflict in `runtime.py`, both sides kept; `tests/test_agent_settings.py` now tests the shared runtime and the
  five workflow modules instead of flattened bundles). 439 tests pass. Library id `57f24b1529586187`.
- 2026-11-19, step 2 live: `source-review-settings-1` with `sdlc_source_mapper` on `haiku` (temporary key, restored):
  only the mapper's terminal started with `--model haiku`, the other four with no model; each step's
  `agent-settings.json` says so. Record: [agent-settings-live.md](../verification/agent-settings-live.md).
