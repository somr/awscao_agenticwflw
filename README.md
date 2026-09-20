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
bash install-profiles.sh
```

Run the local PAY-DEMO-001 fixture with a fresh run ID:

```bash
BASELINE_SHA=$(git rev-parse --verify HEAD)
cao workflow run sdlc_dev_plan \
  --run-id plan-PAY-DEMO-001-1 \
  --input ticket_id=PAY-DEMO-001 \
  --input repository_root="$PWD" \
  --input source_dir="$PWD/examples/PAY-DEMO-001" \
  --input baseline_sha="$BASELINE_SHA" \
  --input base_branch=main \
  --input max_review_rounds=3
```

The default `local_fixture` source adapter is deterministic and network-free. The planning workflow also supports `jira_confluence_live`; its credentials are supplied through environment variables as described in [docs/workflows/planning.md](docs/workflows/planning.md).

## Where to read next

- [Workflow overview and repository layout](docs/architecture.md)
- [Modular workflow build, validation, and installation](docs/build-and-install.md)
- [Planning workflow](docs/workflows/planning.md)
- [Delivery contract](.agentic-sdlc/contracts/delivery-workflow.md)
- [Source-review workflow](docs/workflows/source-review.md)
- [CAO profiles](docs/reference/agent-profiles.md)
- [Documentation map](docs/README.md)
- [Run-report guide](docs/operations/run-report.md)
- [Payment-service fixture](app/README.md)

Durable workflow records belong under `sdlc-records/`, outside the embeddable `.agentic-sdlc/` tooling directory. Detailed per-run evidence belongs under `.agentic-sdlc/runtime/` and is ignored by Git. Human approval is always recorded separately; an agent cannot approve a plan or a pull request.
