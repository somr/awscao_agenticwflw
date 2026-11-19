# Modular workflows and frozen CAO deployment

The five workflows share maintained Python modules. The installer copies the module package, unchanged, into a
frozen library and gives CAO a small generated entry script per workflow. This keeps every installed script far
below CAO's size limit and keeps a resumed run independent of later edits to the source package.

## Source layout

```text
cao/
├── workflows/
│   ├── dev_plan.py                  # Local entry point
│   ├── deliver.py                   # Local entry point
│   ├── source_review.py             # Local entry point
│   ├── source_remediate.py          # Local entry point
│   ├── source_followup.py           # Local entry point
│   └── install*.sh                  # Compatibility installer commands
├── sdlc_workflows/
│   ├── errors.py                    # Contract/execution exception types
│   ├── artifacts.py                 # JSON/text files and content digests
│   ├── validation.py                # Common structural validators
│   ├── runtime.py                   # CAO transport, stabilization and JSON repair
│   ├── planning.py                  # Planning inputs, adapters, policy and orchestration
│   ├── delivery.py                  # Implementation, verification, review/remediation
│   ├── hybrid.py                    # Delivery's registry-driven supervisor/worker dispatch
│   ├── worktrees.py                 # Per-task worktrees, commits and ordered cherry-picks
│   ├── resume.py                    # Resuming a BLOCKED delivery from its manifest
│   ├── source_config.py             # Project file, source roots and write profiles
│   ├── review_knowledge.py          # Project rules: loading, selection, batches, per-file verdicts
│   ├── delivery_standards.py        # Delivery's standards review rounds
│   ├── source_review.py             # PR snapshots, source review and fix routing
│   ├── source_remediation.py        # Ticket-independent bounded fixes and independent review
│   ├── source_followup.py           # Follow-up on review comments: judging threads, checking the changes
│   ├── source_snapshot.py           # Pinned PR snapshots and the isolated agent workspace
│   ├── review_threads.py            # The follow-up's GitHub reads, thread selection and joining to findings
│   ├── followup_draft.py            # The follow-up's editable draft, shared with its publisher
│   ├── source_review_contracts.py   # Shared Source review policy and evidence contracts
│   ├── source_remediation_support.py # Remediation helpers shared with its publisher
│   ├── review_comments.py           # Identity and placement of published review comments
│   ├── verification.py              # Shared trusted verification runner
│   └── remediation.py               # Shared remediator completion contract
├── build_library.py                 # Deterministic library and entry-script builder
└── install_workflow.py              # Shared validation and atomic installation
```

The workflow entry points import their package for local development. **Do not copy
those entry points directly into CAO's workflow directory.** CAO's installed input
scanner needs a literal `INPUTS` declaration, and its resume mechanism relocates a
frozen script. The generated entry contains the declaration and the absolute path of its library.

Keep each workflow's policy in its own module. In particular, Delivery's review policy
and Source Review's policy deliberately have different routing contracts and confidence
thresholds. Write permissions remain in their existing profiles/hooks; sharing execution
helpers does not grant the reviewer an implementer's permissions.

## Why a frozen library?

The inspected CAO runner snapshots the entry script's source, path and content hash. On resume it executes that
source from an engine-owned temporary file; it does not snapshot a Python import tree. A normal sibling import could
therefore fail after relocation, or use updated code when resuming an older run. CAO 2.5.4 also refuses any workflow
script over 131,072 characters (`SCRIPT_LINT_MAX_SOURCE_CHARS`, a constant), both at validation and when a run
starts; flattening every module into one script made Delivery and the follow-up too large.

So the installer writes the whole `sdlc_workflows` package once into
`~/.aws/cli-agent-orchestrator/sdlc-lib/<id>/` (beside CAO's workflows folder), byte for byte, with a
`manifest.json` of every file's SHA-256. `<id>` is the first 16 hex digits of the manifest's SHA-256, so the same
source always gives the same library, and a changed module gives a new one. A library is written to a temporary
folder, renamed into place and made read-only; an existing one is verified and reused, never replaced or deleted
automatically, because an older run may still resume from it.

Each workflow's entry script (a few KB) holds its literal `INPUTS`, the library's absolute path and its manifest.
Before importing anything it checks that the library holds exactly the listed files with those hashes, then puts the
library first on `sys.path` and runs the workflow. Any difference stops the run before an agent starts, with a
message to reinstall. There is no import-time dependency on this repository. Python's standard library and
`cao_workflow` remain environment dependencies, as before.

CAO's linter sees only the entry script, not the `step()` calls in the library. The packaging tests take over its two
relevant checks: every `step()` call declares `recovery=`, and every literal `step_id` matches CAO's pattern.
Delivery records the SHA-256 of the library manifest as `bundle.manifest_sha256` (`null` when run from the
repository) and compares it on resume.

## Build without installation

From the repository root, print a workflow's entry script for a given library folder (the library id is printed too):

```bash
python3 .agentic-sdlc/cao/build_library.py dev_plan --library /tmp/sdlc-lib/check --output /tmp/sdlc_dev_plan.py
```

This requires no CAO server and does not install anything. Treat generated files as deployment artifacts; edit the
package, then reinstall.

## Validate against CAO without replacing anything

The server must be running. CAO restricts validation to its workflow directory, so the
installer creates a unique temporary candidate there and removes it afterwards:

```bash
python3 .agentic-sdlc/cao/install_workflow.py dev_plan --validate-only
python3 .agentic-sdlc/cao/install_workflow.py deliver --validate-only
python3 .agentic-sdlc/cao/install_workflow.py source_review --validate-only
python3 .agentic-sdlc/cao/install_workflow.py source_remediate --validate-only
python3 .agentic-sdlc/cao/install_workflow.py source_followup --validate-only
```

No library, registered workflow or profile is changed. The default repository root is the current
directory; use `--repository-root /path/to/repo` when running elsewhere.

## Install or upgrade

The existing commands and registered names are preserved:

| Installer | Registered workflow |
|---|---|
| `.agentic-sdlc/cao/workflows/install.sh` | `sdlc_dev_plan` |
| `.agentic-sdlc/cao/workflows/install_deliver.sh` | `sdlc_deliver` |
| `.agentic-sdlc/cao/workflows/install_source_review.sh` | `source_review` |
| `.agentic-sdlc/cao/workflows/install_source_remediate.sh` | `source_remediate` |
| `.agentic-sdlc/cao/workflows/install_source_followup.sh` | `source_followup` |

Each wrapper accepts an optional repository-root argument as before. `CAO_WORKFLOW_DIR`
still controls the destination. The equivalent Python installer also supports
`--workflow-dir` explicitly.

The shared installer builds the entry, validates it as a temporary candidate, writes the library (or verifies an
existing one with the same id), and then promotes the entry. `--library-root` moves the libraries (default:
`sdlc-lib` beside the workflow folder). Planning, Delivery and Source remediation keep the previous installed file as `.bak`.
Source remediation requires its two profiles to be installed explicitly; see its [guide](workflows/source-remediation.md).
The follow-up installs its own two profiles and needs Source review's standards profile; see its [guide](workflows/source-review-follow-up.md).
Source Review retains its stricter existing refusal to replace an installed workflow
or any of its six profiles; follow its [upgrade procedure](workflows/source-review.md#upgrading).
Profile installation is not transactional; inspect partial source-review installations
before retrying.

A per-workflow exclusive lock prevents two instances of this installer from racing.
A hard process crash can leave `.<registered-name>.install.lock`; verify no installer
owns it before removing it. Candidates have unique filenames and are removed on normal
success or failure. Both `.yaml` and `.yml` name collisions are rejected. Candidate
validation failure leaves the installed file unchanged.

Coordinate upgrades with other operators. The installer does not stop active runs,
restart the server or clean up another workflow's terminals. An operator should retain
the previous artifact and restore it if a later environment-dependent check fails.

## Check that CAO matches the repository

Installed workflows are frozen, so after any change check that each installed entry points to the library the current
source would produce, and that the profiles match. A change to any module or prompt file gives a new library id, so
all five workflows need reinstalling together. The paths are CAO's defaults (`~/.aws/cli-agent-orchestrator/`;
`CAO_WORKFLOW_DIR` moves the workflows):

```bash
id=$(cd .agentic-sdlc/cao && python3 -c 'from build_library import build_library; print(build_library()[2])')
for name in sdlc_dev_plan sdlc_deliver source_review source_remediate source_followup; do
  grep -q "sdlc-lib/$id'" ~/.aws/cli-agent-orchestrator/workflows/$name.py \
    && echo "$name matches" || echo "$name DIFFERS"
done
for profile in .agentic-sdlc/cao/profiles/*.md; do
  name=$(sed -n 's/^name: *//p' "$profile" | head -1)
  grep -v '^x-cao-source-stem: ' ~/.aws/cli-agent-orchestrator/agent-context/$name.md | cmp -s - "$profile" \
    && echo "$name matches" || echo "$name DIFFERS"
done
```

CAO 2.5.4 adds an `x-cao-source-stem:` line (the source file's name) to each profile it installs, which the check
ignores. It also refuses to replace a profile installed by an earlier CAO version, which has no such line; after
checking that the installed copy is the repository's, delete `agent-context/<name>.md` and install again.

After pulling changes, run the tests, reinstall the profiles that differ, then reinstall the workflows that
differ (profiles first; `source_review` needs its [upgrade procedure](workflows/source-review.md#upgrading)), and use new
run IDs. Registry, skill and contract edits need no reinstall because
they are read from the repository when a run starts.

## Editing and extending modules

Modules use explicit, top-level relative imports for local dependencies:

```python
from .errors import WorkflowContractError
from .runtime import _run_json_contract_step
```

The library holds only `.py` modules and prompt files under `prompts/<workflow>/`; the builder refuses any other
file in the package. A module may locate its own files with `__file__` (the library is never moved), but repository
files must still be resolved from explicit workflow inputs, as the existing code does. Each workflow module keeps a
literal `INPUTS`, which the builder copies into the entry.

`runtime.py` owns the same timing, retry limits, recovery policy, answer-file names and
terminal cleanup protocol as before extraction. Its `preserve_source` option retains
the two existing repair-prompt variants: true for Planning/Source Review, explicitly
false for Delivery and remediation writes. The headless-interaction error message says "workflow agents";
the exception and control flow are unchanged.

## Runtime assets outside the library

The library contains code and prompt texts only. Everything a run reads from the repository at start-up is
resolved from `repository_root`, not embedded: the contracts, policies, schemas and
templates under `.agentic-sdlc/`, the project's `agentic-sdlc-project.json` and the rule files it names in
`agentic-sdlc-pr-review-kb/` ([project rules](reference/project-rules.md)), and, for hybrid Delivery,
`.agentic-sdlc/cao/specialists.json` and `.agentic-sdlc/cao/skills/`. Changing those files takes effect on the next run without a
reinstall; changing Python modules or prompt files requires reinstalling the workflows. Installing
`sdlc_deliver` also requires the `sdlc_code_supervisor` profile, because Delivery defaults to
`implementation_mode=hybrid` (see [hybrid delivery](workflows/hybrid-delivery.md)), and, for a project with rules,
the `sdlc_standards_reviewer` profile.

## Tests

Run the full suite from the repository root:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The tests import the modules directly; the installed library is those files byte for byte. The packaging tests
cover the library build (deterministic id, only modules and prompt files, a changed module giving a new library),
the entry scripts (literal `INPUTS` equal to the modules', a size margin, refusal of a changed, missing or extra
file or a changed manifest before anything is imported), a relocated entry running a JSON repair from the library in
an isolated interpreter, the `step()` checks CAO's linter no longer sees, exact terminal cleanup, and installer
collision/failure handling, including a library that is never replaced. Local HTTP fixture tests require socket
access.

Further suites cover the write-scope hook (including the configurable source roots, and a parity test that runs the workflow's validator and the hook's copy over one corpus), non-converged planning, developer guidance, warm start, reviewer history, hybrid delivery with parallel worktree waves, Delivery resume, Source remediation and project rules (parsing, batching, per-file verdicts, both workflows' standards reviews). The integration tests use real Git commits and application verification
subprocesses, with only agent responses/CAO transport simulated. They cover planning
JSON repair and plan revision, digest-bound approval, delivery verification repair,
automatic remediation, protected findings, re-review and human handoff.

A live source-review fixture additionally exercises the real CAO server and provider.
See the [refactor validation record](verification/modularity-verification.md) for results and limits.
