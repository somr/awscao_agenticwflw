# Modular workflows and standalone CAO deployment

The three workflows share maintained Python modules. Each installer builds a
self-contained Python artifact for CAO. This removes duplicated lifecycle code while
keeping a resumed run independent of later edits to the source package.

## Source layout

```text
cao/
├── workflows/
│   ├── dev_plan.py                  # Local entry point
│   ├── deliver.py                   # Local entry point
│   ├── source_review.py             # Local entry point
│   └── install*.sh                  # Compatibility installer commands
├── sdlc_workflows/
│   ├── errors.py                    # Contract/execution exception types
│   ├── artifacts.py                 # JSON/text files and content digests
│   ├── validation.py                # Common structural validators
│   ├── runtime.py                   # CAO transport, stabilization and JSON repair
│   ├── planning.py                  # Planning inputs, adapters, policy and orchestration
│   ├── delivery.py                  # Implementation, verification, review/remediation
│   ├── hybrid.py                    # Delivery's registry-driven supervisor/worker dispatch
│   └── source_review.py             # PR snapshots, source review and fix routing
├── build_workflow.py                # Deterministic standalone bundler
└── install_workflow.py              # Shared validation and atomic installation
```

The workflow entry points import their package for local development. **Do not copy
those entry points directly into CAO's workflow directory.** CAO's installed input
scanner needs a literal `INPUTS` declaration, and its resume mechanism relocates a
frozen script. The generated bundle contains the declaration and every local dependency.

Keep each workflow's policy in its own module. In particular, Delivery's review policy
and Source Review's policy deliberately have different routing contracts and confidence
thresholds. Write permissions remain in their existing profiles/hooks; sharing execution
helpers does not grant the reviewer an implementer's permissions.

## Why bundle at installation?

The inspected CAO runner snapshots the entry script's source, path and content hash.
On resume it executes that source from an engine-owned temporary file. It does not
snapshot a Python import tree. A normal sibling import could therefore fail after
relocation, or use updated code when resuming an older run.

The builder follows the local dependency graph and emits ordinary Python definitions
in dependency order. CAO can lint the complete code, including the actual `step()` call.
There is no embedded archive, opaque `exec` payload or import-time dependency on this
repository in the installed artifact. Python's standard library and `cao_workflow`
remain environment dependencies, as before.

Every bundle includes `SDLC_BUNDLE_MANIFEST`, mapping module filenames to their source
SHA-256 digests. Builds contain no timestamp or machine-specific source path, so the
same source produces identical bytes. A changed shared module changes the bundle and
its CAO source hash. Already-frozen bundles retain the previous helper implementation.
This preserves CAO's source-snapshot boundary; it does not add resume support to
Workflow 3, which still refuses reuse of an existing run directory.

## Build without installation

From the repository root:

```bash
python3 .agentic-sdlc/cao/build_workflow.py dev_plan --output /tmp/sdlc_dev_plan.py
python3 .agentic-sdlc/cao/build_workflow.py deliver --output /tmp/sdlc_deliver.py
python3 .agentic-sdlc/cao/build_workflow.py source_review --output /tmp/source_review.py
```

These commands require no CAO server and do not install profiles or run agents.
Treat generated files as deployment artifacts; edit the package, then rebuild.

## Validate against CAO without replacing anything

The server must be running. CAO restricts validation to its workflow directory, so the
installer creates a unique temporary candidate there and removes it afterwards:

```bash
python3 .agentic-sdlc/cao/install_workflow.py dev_plan --validate-only
python3 .agentic-sdlc/cao/install_workflow.py deliver --validate-only
python3 .agentic-sdlc/cao/install_workflow.py source_review --validate-only
```

No registered workflow or profile is changed. The default repository root is the current
directory; use `--repository-root /path/to/repo` when running elsewhere.

## Install or upgrade

The existing commands and registered names are preserved:

| Installer | Registered workflow |
|---|---|
| `.agentic-sdlc/cao/workflows/install.sh` | `sdlc_dev_plan` |
| `.agentic-sdlc/cao/workflows/install_deliver.sh` | `sdlc_deliver` |
| `.agentic-sdlc/cao/workflows/install_source_review.sh` | `source_review` |

Each wrapper accepts an optional repository-root argument as before. `CAO_WORKFLOW_DIR`
still controls the destination. The equivalent Python installer also supports
`--workflow-dir` explicitly.

The shared installer builds the complete dependency closure, validates its candidate,
and then promotes it. Planning and Delivery keep the previous installed file as `.bak`.
Source Review retains its stricter existing refusal to replace an installed workflow
or any of its five profiles; follow its [upgrade procedure](workflows/source-review.md).
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

Installed workflows are frozen copies, so after any change compare them with a fresh build. The paths are
CAO's defaults (`~/.aws/cli-agent-orchestrator/`; `CAO_WORKFLOW_DIR` moves the workflows):

```bash
for w in dev_plan deliver; do
  python3 .agentic-sdlc/cao/build_workflow.py "$w" --output "/tmp/check_$w.py" > /dev/null
  cmp -s "/tmp/check_$w.py" ~/.aws/cli-agent-orchestrator/workflows/sdlc_$w.py && echo "sdlc_$w matches" || echo "sdlc_$w DIFFERS"
done
for pair in context-normalizer:sdlc_context_normalizer planning-analyst:sdlc_planning_analyst plan-author:sdlc_plan_author \
            plan-reviewer:sdlc_plan_reviewer code-supervisor:sdlc_code_supervisor implementer:sdlc_implementer \
            pr-reviewer:sdlc_pr_reviewer remediator:sdlc_remediator; do
  cmp -s ~/.aws/cli-agent-orchestrator/agent-context/${pair##*:}.md ".agentic-sdlc/cao/profiles/${pair%%:*}.md" \
    && echo "${pair##*:} matches" || echo "${pair##*:} DIFFERS"
done
```

After pulling changes, run the tests, reinstall the profiles that differ, then reinstall the workflows that
differ (profiles first), and use new run IDs. Registry, skill and contract edits need no reinstall because
they are read from the repository when a run starts.

## Editing and extending modules

Use explicit, top-level relative imports for local dependencies:

```python
from .errors import WorkflowContractError
from .runtime import _run_json_contract_step
```

This is a small project-specific bundler, not a general Python application packager.
Local imports must be single-level, named and unaliased; wildcard imports, dependency
cycles and ambiguous top-level bindings are rejected. The package initializer must contain only documentation, not hidden import-time
side effects. References to `__file__`/`__package__` are rejected. Shared definitions
and constants must have exactly one owner. Identical external imports can appear in multiple modules.

Keep local module namespaces distinct: do not rely on a module's `__file__`, inspect its
namespace dynamically, or introduce hidden runtime imports of local dependencies.
Repository files must be resolved from explicit workflow inputs, as the existing code
does. Add bundler support and regression tests before using a new module pattern.

`runtime.py` owns the same timing, retry limits, recovery policy, answer-file names and
terminal cleanup protocol as before extraction. Its `preserve_source` option retains
the two existing repair-prompt variants: true for Planning/Source Review, explicitly
false for Delivery. The headless-interaction error message now says "workflow agents"
for all three; the exception and control flow are unchanged.

## Runtime assets outside the bundle

A bundle contains code only. Everything a run reads from the repository at start-up is
resolved from `repository_root`, not embedded: the contracts, policies, schemas and
templates under `.agentic-sdlc/`, and, for hybrid Delivery, `.agentic-sdlc/cao/specialists.json`
and `.agentic-sdlc/cao/skills/`. Changing those files takes effect on the next run without a
rebuild; changing Python modules requires rebuilding and reinstalling the bundle. Installing
`sdlc_deliver` also requires the `sdlc_code_supervisor` profile, because Delivery defaults to
`implementation_mode=hybrid` (see [hybrid delivery](workflows/hybrid-delivery.md)).

## Tests

Run the full suite against **both** execution forms:

```bash
python3 -m unittest discover -s tests -v
SDLC_TEST_SOURCE=1 python3 -m unittest discover -s tests -v
```

The default mode builds the deployed artifacts in memory and tests those exact
implementations. Source mode tests the domain modules and the shared runtime directly.
Existing assertions are retained; common lifecycle assertions now live in their own
test class. Local HTTP fixture tests require socket access.

Additional packaging tests cover deterministic output and dependency digests, module
conflicts, execution in isolated Python with no source-package path, frozen bundles
surviving dependency changes, exact terminal cleanup, and installer collision/failure
handling. Further suites cover the write-scope hook (including the configurable source roots, and a parity test that runs the workflow's validator and the hook's copy over one corpus), non-converged planning, developer guidance, warm start, reviewer history and hybrid delivery. The integration tests use real Git commits and application verification
subprocesses, with only agent responses/CAO transport simulated. They cover planning
JSON repair and plan revision, digest-bound approval, delivery verification repair,
automatic remediation, protected findings, re-review and human handoff.

A live source-review fixture additionally exercises the real CAO server and provider.
See the [refactor validation record](verification/modularity-verification.md) for results and limits.
