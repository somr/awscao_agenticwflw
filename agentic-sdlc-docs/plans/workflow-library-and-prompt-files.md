# Plan: install the workflow code as a frozen library and keep prompts in files

Status: **steps 0-1 done (2026-11-18, 2026-11-19); step 2 next; D1-D10 agreed (D6 2026-11-18, the rest
2026-11-19).** Progress notes go at the end.

## 1. Why

CAO 2.5.4 (installed 2026-11-18) refuses a workflow script over 131,072 characters, both when installing
(`cao workflow validate`) and when a run starts. The installer flattens every module a workflow uses into one
script, and two of those scripts are over the limit already on `main` `7cb9714`: `deliver` 166,972 and
`source_followup` 138,284. Delivery and the follow-up therefore can neither be installed nor run on 2.5.4.

Moving prompts out, or stripping docstrings and comments, is not enough (`deliver` stays at about 134,000). The fix
is to stop flattening: install the shared modules once, frozen and content-addressed, and install a small entry
script per workflow that imports them. The prompts move into text files inside that library at the same time, so
they can be read and edited as text.

## 2. Facts checked (2026-11-18, CAO 2.5.4)

| Fact | Where | Consequence |
|---|---|---|
| Limit `SCRIPT_LINT_MAX_SOURCE_CHARS = 128 * 1024`, a constant | `cli_agent_orchestrator/constants.py:1209`; checked in `script_lint.py` and `workflow_spec_service.py` | Not configurable; only the installed script counts. |
| Bundle sizes at `7cb9714`: dev_plan 98,305; deliver 166,972; source_review 104,040; source_remediate 106,187; source_followup 138,284 | `build_workflow.build_source` | Three bundles fit for now; all grow with every feature. |
| Prompt-building code is small: about 14,300 characters in `deliver`, 6,300 in `source_followup` | measured on the built bundles | Prompts in files help readability, not size. |
| CAO snapshots the entry script and, on resume, runs that snapshot from an engine-owned temporary file; it does not snapshot imports | `build-and-install.md` "Why bundle at installation?", `script_runner.py` | A relative or repository import could break or pick up newer code on resume. An import from an absolute, never-changing folder does neither. |
| The script runs as `sys.executable <snapshot>` with a constructed environment (`CAO_*`, `PATH`, `HOME`) and `cwd` = the working directory | `script_runner.py` `_drive_process`, env builder | The entry must carry the library's absolute path; it cannot rely on the repository or `PYTHONPATH`. |
| The linter forbids only imports starting with `cli_agent_orchestrator`; a literal `import`/`from` of another package is allowed | `constants.SCRIPT_LINT_DISALLOWED_IMPORT_PREFIXES`, `script_lint.py` | The entry may import `sdlc_workflows` after adding the library to `sys.path`. Needs a live check (step 0). |
| CAO's linter checks only the installed script: `step()` must have `recovery=`, a literal `step_id` must match the server's pattern | `script_lint.py` rule catalogue | With the code in a library, CAO no longer sees our `step()` calls; a repository test must take over those two checks. |
| CAO reads `INPUTS` statically from the installed script; the default values must be literals | `build-and-install.md`, packaging test | The entry must contain each workflow's `INPUTS` literally. |
| The builder refuses `__file__` because flattened code is relocated | `build_workflow.py` | Library modules are not relocated, so they may use `__file__` (needed to find the prompt files). |
| Every bundle carries `SDLC_BUNDLE_MANIFEST` (module → sha256); Delivery records its hash as `bundle.manifest_sha256` and compares it on resume | `build_workflow.py`, `delivery._bundle_identity` | The library's manifest takes that role. |
| Ten test files exec the flattened bundles; six switch to the modules with `SDLC_TEST_SOURCE=1`; each full mode takes about 100 s | `tests/` | Without flattening, the two modes test the same code. |
| Source review's installer refuses to overwrite its installed workflow and six profiles | `install_workflow.py`, `workflows/source-review.md#upgrading` | The rule stays for the entry script. |

## 3. Decisions (agreed; D6 2026-11-18, the rest 2026-11-19)

| # | Question | Decision |
|---|---|---|
| D1 | What is installed where? | The whole `sdlc_workflows` package (all modules and the prompt files) as one library in `~/.aws/cli-agent-orchestrator/sdlc-lib/<id>/sdlc_workflows/`, where `<id>` is the first 16 hex digits of the sha256 of its manifest. One library serves all five workflows installed from the same tree. Each workflow gets an entry script in CAO's workflows folder under today's name. |
| D2 | What does the entry script do? | Generated, a few KB: a docstring, the workflow's `INPUTS` literal, the library path, the library manifest (every file → sha256). It checks that every listed file exists with that hash and that the package holds no other `.py` file, then puts the library first on `sys.path`, imports the workflow's `main` and runs it. Any mismatch stops the run with "reinstall; this run cannot resume". Uses only the standard library. |
| D3 | Can a library change? | Never. Install builds it in a temporary folder next to the target and renames it into place; when `<id>` already exists, it verifies the hashes and reuses it. Files are made read-only. Nothing deletes a library automatically, because an older run may still resume from it; a later small tool can list libraries no installed entry uses. |
| D4 | `INPUTS` | Still defined in each workflow module; the builder copies it into the entry as a literal (as today), and a test checks the two are equal. |
| D5 | Tests | Drop the flattened-bundle mode and `SDLC_TEST_SOURCE`: tests import the modules (what the library contains, byte for byte). New tests: the entry is built correctly and refuses a changed, missing or extra file; `INPUTS` equality; a library builds identically twice; every entry stays under a margin (say 32 KB); and a static check that every `step()` call has `recovery=` and every literal `step_id` matches CAO's pattern (replacing the lint CAO no longer sees). The full suite then runs once, about 100 s instead of about 200 s. |
| D6 | Where do prompts live and how are they filled in? | **Agreed 2026-11-18, with the workflow in the path.** `sdlc_workflows/prompts/<workflow>/<name>.md`, one folder per workflow that uses the prompt (`planning`, `delivery` (including hybrid workers and remediation), `source-review`, `source-review-follow-up`, `source-remediation`) and `shared/` for parts several workflows use (answer-file delivery instructions, rules guidance for authors). One file per prompt or reusable part, read through one helper `prompts.render("<workflow>/<name>", **values)` that uses `string.Template` with `${name}` placeholders and `substitute` (a missing value is an error; `$` in text is written `$$`). Conditions and loops stay in code, which joins rendered parts. |
| D7 | What counts as "prompts and fixed text"? | Every agent prompt the workflows build (Planning, Delivery, hybrid, remediation, Source review, follow-up, source remediation), the answer-file delivery instructions, the rules guidance for authors, and the fixed Markdown the workflows write (the human review brief, the review draft). Error messages, contract vocabularies and JSON field names stay in code. |
| D8 | How do we know nothing changed? | Before moving a prompt, a test renders it with fixed inputs through the current code and stores the output as a fixture; after the move the same test must give byte-identical output. Fixtures are removed only if a later change is meant to alter a prompt. |
| D9 | Bundle identity | `bundle.manifest_sha256` becomes the sha256 of the library manifest, read from `manifest.json` in the library folder (absent when running from the repository, as today). Resume compares it as before. |
| D10 | Branch and order | New branch `feature/workflow-library` from `main` (this plan's branch). The agent-settings branch stays paused at its step 2 code and is rebased after this merges. |

## 4. Design

### 4.1 Build (`build_workflow.py` replaced by `build_library.py`)

- `build_library(package) -> (files, manifest, id)`: every `*.py` and `prompts/<workflow>/*.md` of the package, read as bytes,
  hashed, in sorted order; no timestamps or machine paths, so the same tree gives the same id.
- `build_entry(workflow, library_dir, manifest) -> str`: the entry script (D2); compiled once to check it.
- The local entry points in `.agentic-sdlc/cao/workflows/*.py` stay for development.

### 4.2 Install (`install_workflow.py`)

- Writes the library (D3), then validates and installs the entry exactly as today (temporary candidate,
  `cao workflow validate`, atomic replace or Source review's no-overwrite rule, profile handling unchanged).
- `--validate-only` builds both in temporary folders.

### 4.3 Prompts (`sdlc_workflows/prompts.py` + `prompts/*.md`)

- `render("<workflow>/<name>", **values)` reads `Path(__file__).parent / "prompts" / "<workflow>/<name>.md"`
  and substitutes; the name must be one of the known workflow folders (or `shared`) plus a plain file name.
- Moved one workflow at a time under D8.

## 5. Steps (each ends with tests, reinstall and a live check)

0. **Spike, no repository change.** A tiny library in a scratch `sdlc-lib/<id>/` folder with a module that calls
   `step()`, and an entry script in CAO's workflows folder that verifies, imports and runs it. Check that
   `cao workflow validate` passes, a run works, and the linter says nothing about the import. Needs your
   `cao workflow approve`.
1. **Library build and entry**: `build_library.py`, entry generator, installer, test changes (D5), docs of the
   build. Reinstall all five workflows. Live: a Source review run on a closed PR and a short Planning run; check
   that each installed entry is small and that a changed library file stops a run.
2. **Delivery and follow-up live**: a Delivery run on the demo ticket (old branch moved aside as before) and a
   follow-up run on a closed PR, the two workflows that could not run on 2.5.4.
3. **Prompts, one workflow at a time** (Source review, follow-up, source remediation, Planning, Delivery with
   hybrid and remediation): fixtures first, then move, byte-identical tests (D8). Reinstall; one cheap live run at
   the end.
4. **Docs**: `build-and-install.md` ("Why bundle" becomes "Why a frozen library"), `architecture.md`, the workflow
   guides' install sections, `agent-profiles.md` where it mentions bundles, and a note on the size limit.

## 6. Out of scope

- Automatic removal of unused libraries (a listing tool can come later).
- Changing any prompt's wording (D8 keeps them identical).
- Splitting workflows.

## 7. Progress notes

- 2026-11-18, step 0: CAO 2.5.4 validates (no findings) and runs a 1.4 KB entry that verifies and imports a
  content-addressed library; a template prompt from the library reached the agent; a changed library file stopped
  the run before any step. Record: [workflow-library-spike.md](../verification/workflow-library-spike.md).
- 2026-11-19, step 1: `build_library.py` replaces `build_workflow.py`; installer writes the library once
  (`sdlc-lib/<id>/`, read-only, never replaced) and installs ~4.7 KB entries; tests run once (425 in about 50 s; the
  10 `ResourceWarning`s were already on `main`); `build-and-install.md` rewritten for the library. Installed all five
  (library `bc242cd5ecfe82ca`). Live: Planning ran from the library to its approved-plan guard, Source review fixture
  routed as expected, a changed library file stopped a run. CAO 2.5.4 refuses to replace profiles installed by an
  earlier CAO (delete and reinstall) and adds `x-cao-source-stem:` to installed profiles. Record:
  [workflow-library-live.md](../verification/workflow-library-live.md).
