# Agentic SDLC on CAO

This repository is a learning and validation project for an agentic software-development lifecycle built on AWS Labs CLI Agent Orchestrator (CAO). The maintained implementation lives under [.agentic-sdlc/](.agentic-sdlc/).

Current repository capabilities (2026-09-21):

- Planning (`sdlc_dev_plan`) retrieves and normalizes requirements, analyses the repository, authors a Development Plan, and performs an independent plan review before human approval. It supports developer guidance, review history, and warm starts from eligible non-converged candidates.
- Delivery (`sdlc_deliver`) implements an approved plan, runs configured verification commands, performs independent PR review and up to three remediation rounds, and prepares a human review package. Hybrid mode is the default: a supervisor assigns registered workers sequentially, followed by an integration pass. Single-implementer mode is also available.
- Source review (`source_review`) reviews an existing GitHub pull request in an isolated snapshot and routes validated findings into `AUTO_FIX` or `HUMAN_REQUIRED` queues. It produces feedback only; it does not apply fixes or run tests. A person edits the generated `review-draft.md` and publishes it with a separate command, as one non-blocking `COMMENT` review with comments beside the code.

Python owns orchestration, validation, Git operations, verification and routing. Human approval is recorded separately for the reviewed plan and the delivered commit. Delivery produces local PR artifacts; it does not push, open a GitHub PR or merge.

The workflow source is modular and is bundled into standalone CAO scripts during installation. The repository copy is the source of truth; installed CAO copies are deployment artifacts. This remains a learning prototype with [known enforcement gaps and planned hardening](#current-limits-and-planned-work).

## Quick start

Use Python 3.14 or newer, Git, the `cao` CLI, and a configured `claude_code` provider. The [profile guide](agentic-sdlc-docs/reference/agent-profiles.md) records checks against locally installed CAO 2.5.0. Run the commands below from the repository root, with paths accessible on the CAO server host. Keep the repository's [Claude hook configuration](.claude/settings.json) and [write-scope hook](.claude/hooks/restrict-write-scope.py) in place.

Start the server in a separate terminal:

```bash
cao-server
```

Install the four planning profiles, then the workflow. The planning installer does not install profiles:

```bash
for profile in context-normalizer planning-analyst plan-author plan-reviewer; do
  cao profile validate ".agentic-sdlc/cao/profiles/$profile.md"
  cao install ".agentic-sdlc/cao/profiles/$profile.md"
done
bash .agentic-sdlc/cao/workflows/install.sh "$PWD"
```

Run the local PAY-DEMO-001 fixture, replacing the example run ID with a fresh one on each invocation:

```bash
BASELINE_SHA=$(git rev-parse --verify HEAD)
cao workflow run sdlc_dev_plan --wait --json \
  --run-id plan-PAY-DEMO-001-1 \
  --input ticket_id=PAY-DEMO-001 \
  --input repository_root="$PWD" \
  --input source_dir="$PWD/agentic-sdlc-local-inputs/PAY-DEMO-001" \
  --input baseline_sha="$BASELINE_SHA" \
  --input base_branch=main \
  --input max_review_rounds=3
```

The default `local_fixture` source adapter retrieves files without network access; the workflow still uses the configured model provider. The optional `jira_confluence_live` adapter uses credentials supplied through environment variables. It has been tested against a local fake server, not a real Atlassian tenant. See the [Planning guide](agentic-sdlc-docs/workflows/planning.md).

Only a passing independent review publishes a plan with outcome `AWAITING_HUMAN_APPROVAL`. A run that cannot converge or needs a human decision returns `AWAITING_HUMAN_CLARIFICATION` and preserves a non-approvable candidate under `agentic-sdlc-records/<ticket>/candidates/<run-id>/`. Read its `human-needed.json`, then use `guidance_file` and, where eligible, `resume_from` for a new run. CAO's `completed` state alone does not mean a plan is ready to approve.

## Approve the plan, then deliver

After `AWAITING_HUMAN_APPROVAL`, a human reads the published plan and records the decision. Approval binds the reviewed plan's SHA-256, repository baseline and any developer guidance:

```bash
python3 .agentic-sdlc/scripts/approve_plan.py --repository-root "$PWD" --ticket-id PAY-DEMO-001 \
  --decision APPROVED --approved-by "<your name>" --reference "<ticket or review link>"
```

Install the four delivery profiles, then the workflow:

```bash
for profile in code-supervisor implementer pr-reviewer remediator; do
  cao profile validate ".agentic-sdlc/cao/profiles/$profile.md"
  cao install ".agentic-sdlc/cao/profiles/$profile.md"
done
bash .agentic-sdlc/cao/workflows/install_deliver.sh "$PWD"
```

Use a clean checkout with an existing base branch and exclusive access while Delivery runs. It switches to `sdlc/<ticket>` and commits source changes there. Run it with a fresh run ID:

```bash
cao workflow run sdlc_deliver --wait --json --run-id deliver-PAY-DEMO-001-1 \
  --input ticket_id=PAY-DEMO-001 --input repository_root="$PWD" --input base_branch=main
```

Add `--input implementation_mode=single` to use one implementer. The default hybrid mode runs workers sequentially in the same checkout.

When Delivery reaches `AWAITING_HUMAN_REVIEW`, read `agentic-sdlc-records/PAY-DEMO-001/human-review-brief.md` and review the branch before recording a human decision:

```bash
python3 .agentic-sdlc/scripts/record_pr_approval.py --repository-root "$PWD" --ticket-id PAY-DEMO-001 \
  --decision APPROVED --approved-by "<your name>" --reference "<review link or note>"
```

This decision is bound to the exact reviewed commit. Delivery leaves the checkout on its delivery branch. See the [Delivery guide](agentic-sdlc-docs/workflows/delivery.md) for outcomes, recovery and approval rules.

## Review an existing pull request

Source review runs independently of Planning and Delivery. GitHub mode additionally needs authenticated `gh` and Git HTTPS read access on the server host:

```bash
bash .agentic-sdlc/cao/workflows/install_source_review.sh "$PWD"
cao workflow run source_review --wait --json --run-id source-review-pr42-1 \
  --input repository_root="$PWD" \
  --input pr_url=https://github.com/OWNER/REPO/pull/42
```

Replace the PR URL and run ID. This installer includes its five profiles and refuses to overwrite an existing workflow or profile; use the [Source-review guide](agentic-sdlc-docs/workflows/source-review.md) for upgrades, local fixture mode and optional publication. Results are `code-review.json`, `comments.md` and the editable `review-draft.md` under `.agentic-sdlc/runtime/source-review/<run-id>/`.

## Configure for your project

The sample application lives under `app/`. To point the workflows at your own code, edit [`.agentic-sdlc/cao/specialists.json`](.agentic-sdlc/cao/specialists.json):

- `source_roots`: the directories where generated source is written, committed and diffed (default `["app"]`);
- `write_profiles`: which agent profiles may write there;
- `verification`: the commands that verify a change, so use your own build and test commands;
- `workers` and `skills`: the specialists Delivery can assign work to.

Agents cannot edit this file. An invalid file stops Delivery and denies all source writes instead of falling back to `app/`. Verification commands are configured separately from the source roots. Planning does not yet consume those roots, so check that the plan's tasks fit the permitted directories before approval.

The shipped registry has one general `developer` worker and AngularJS/Spark skills. Their verification commands require project files and toolchains beyond the Python payment fixture. See [source roots](agentic-sdlc-docs/workflows/delivery.md#source-roots), the [registry reference](agentic-sdlc-docs/workflows/delivery.md#registry-reference) and the [specialist/skill extension guide](agentic-sdlc-docs/workflows/hybrid-delivery.md).

Registry, skill and contract edits are read from the repository on subsequent runs. After changing Python workflow code, rebuild and reinstall the bundles; after changing profiles, reinstall them before the workflows. See [build and install](agentic-sdlc-docs/build-and-install.md) for validation-only commands and installed-copy comparisons.

## Repository layout and verification

| Path | Purpose |
|---|---|
| `.agentic-sdlc/` | Embeddable workflow modules, installers, profiles, registry, skills and runtime contracts |
| `.claude/` | Repository hook and its configuration |
| `agentic-sdlc-docs/` | Operating guides, references and dated verification records |
| `agentic-sdlc-local-inputs/` | Local requirements and source-review fixtures; demonstration records |
| `agentic-sdlc-records/<ticket>/` | Durable plans, approval records, delivery evidence and non-converged candidates |
| `.agentic-sdlc/runtime/` | Detailed per-run evidence, ignored by Git |
| `app/` | Python payment-service example |
| `tests/` | Workflow, integration, packaging and hook tests |

Run the workflow suite in both bundled and source-module modes, then the sample application tests:

```bash
python3 -m unittest discover -s tests -v
SDLC_TEST_SOURCE=1 python3 -m unittest discover -s tests -v
python3 -m unittest discover -t app -s app/tests -v
```

These tests simulate agent responses and CAO transport; local HTTP fixtures need socket access. Real CAO/provider checks are recorded separately in the [verification records](agentic-sdlc-docs/README.md#verification-records), including the latest [configurable-source-roots check](agentic-sdlc-docs/verification/configurable-source-roots-live.md).

## Current limits and planned work

The [hardening plan](hardening-plan.md) is proposed work, not implemented protection. Current limitations include:

- Agents are instructed to write one answer file, but hooks permit the broader runtime subtree; exact per-step answer authorization is still planned.
- Delivery checks baseline ancestry, which does not detect all changes that could invalidate an approved plan. Clean-source and empty-index preconditions are currently enforced for hybrid mode, not consistently for single mode.
- Verification runs application code and tests in host subprocesses; the agent write hook does not sandbox that execution. Delivery also shares its checkout and has no enforced run isolation.
- Human PR decisions are local records, not verified GitHub reviews. Parallel workers, remote PR creation and deployment automation are outside the current implementation.

See [future versions](future-versions.md) for remaining gaps, mitigation directions and reassessment criteria, including planning/source-root alignment and failure reporting.

## Where to read next

- [Workflow overview and repository layout](agentic-sdlc-docs/architecture.md)
- [Modular workflow build, validation, and installation](agentic-sdlc-docs/build-and-install.md)
- [Planning workflow](agentic-sdlc-docs/workflows/planning.md)
- [Delivery workflow: running, configuring and approving](agentic-sdlc-docs/workflows/delivery.md)
- [Delivery contract](.agentic-sdlc/contracts/delivery-workflow.md)
- [Hybrid delivery and specialist/skill extension guide](agentic-sdlc-docs/workflows/hybrid-delivery.md)
- [Source-review workflow](agentic-sdlc-docs/workflows/source-review.md)
- [CAO profiles](agentic-sdlc-docs/reference/agent-profiles.md)
- [Documentation map](agentic-sdlc-docs/README.md)
- [Payment-service fixture](app/README.md)
