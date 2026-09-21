# Agentic SDLC on CAO

This repository is a learning and validation project for an agentic software-development lifecycle built on AWS Labs CLI Agent Orchestrator (CAO). The maintained implementation lives under [.agentic-sdlc/](.agentic-sdlc/).

Three workflows are available:

- Planning (`sdlc_dev_plan`) retrieves and normalizes requirements, analyses the repository, authors a Development Plan, and performs an independent plan review before human approval.
- Delivery (`sdlc_deliver`) implements an approved plan, runs deterministic verification, performs PR review and bounded remediation, and prepares a human review package.
- Source review (`source_review`) reviews an existing GitHub pull request in an isolated snapshot and routes validated findings to automatic fixing or human handling. It does not modify the PR by default.

The workflow source is modular and is bundled into standalone CAO scripts during installation. The repository copy is the source of truth; installed CAO copies are deployment artifacts.

## Quick start

Use Python 3.14 or newer, a running `cao-server`, the `cao` CLI, and a configured `claude_code` provider.

Install or validate the planning workflow:

```bash
cao-server
python3 .agentic-sdlc/cao/install_workflow.py dev_plan --validate-only
bash .agentic-sdlc/cao/workflows/install.sh "$PWD"
```

Install the four planning profiles when needed:

```bash
for profile in context-normalizer planning-analyst plan-author plan-reviewer; do
  cao profile validate ".agentic-sdlc/cao/profiles/$profile.md"
  cao install ".agentic-sdlc/cao/profiles/$profile.md"
done
```

Run the local PAY-DEMO-001 fixture with a fresh run ID:

```bash
BASELINE_SHA=$(git rev-parse --verify HEAD)
cao workflow run sdlc_dev_plan \
  --run-id plan-PAY-DEMO-001-1 \
  --input ticket_id=PAY-DEMO-001 \
  --input repository_root="$PWD" \
  --input source_dir="$PWD/agentic-sdlc-local-inputs/PAY-DEMO-001" \
  --input baseline_sha="$BASELINE_SHA" \
  --input base_branch=main \
  --input max_review_rounds=3
```

The default `local_fixture` source adapter is deterministic and network-free. The planning workflow also supports `jira_confluence_live`; its credentials are supplied through environment variables as described in [agentic-sdlc-docs/workflows/planning.md](agentic-sdlc-docs/workflows/planning.md).

## Approve the plan, then deliver

Planning ends at `AWAITING_HUMAN_APPROVAL`. A human records the decision, and only an approved plan can be delivered:

```bash
python3 .agentic-sdlc/scripts/approve_plan.py --repository-root "$PWD" --ticket-id PAY-DEMO-001 \
  --decision APPROVED --approved-by "<your name>" --reference "<ticket or review link>"
```

Install the four delivery profiles before the delivery workflow (order matters; see the [Delivery guide](agentic-sdlc-docs/workflows/delivery.md)), then run it:

```bash
cao workflow run sdlc_deliver --wait --json --run-id deliver-PAY-DEMO-001-1 \
  --input ticket_id=PAY-DEMO-001 --input repository_root="$PWD"
```

## Configure for your project

The sample application lives under `app/`. To point the workflows at your own code, edit [`.agentic-sdlc/cao/specialists.json`](.agentic-sdlc/cao/specialists.json):

- `source_roots`: the directories where generated source is written, committed and verified (default `["app"]`);
- `write_profiles`: which agent profiles may write there;
- `verification`: the commands that verify a change, so use your own build and test commands;
- `workers` and `skills`: the specialists Delivery can assign work to.

Agents cannot edit this file. An invalid file stops Delivery and denies all source writes instead of falling back to `app/`. See [source roots](agentic-sdlc-docs/workflows/delivery.md#source-roots) and the [registry reference](agentic-sdlc-docs/workflows/delivery.md#registry-reference). Planning accepts optional developer guidance and can be warm-started from a run that did not converge; see the [Planning guide](agentic-sdlc-docs/workflows/planning.md).

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

Durable workflow records belong under `agentic-sdlc-records/`, outside the embeddable `.agentic-sdlc/` tooling directory. Detailed per-run evidence belongs under `.agentic-sdlc/runtime/` and is ignored by Git. Human approval is always recorded separately; an agent cannot approve a plan or a pull request.
