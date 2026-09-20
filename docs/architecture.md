# Agentic SDLC on AWS Labs CAO

This directory is the repository-local, version-controlled contract and implementation for the agentic development lifecycle.

## Implemented workflows

Planning Workflow 1 is executable:

```text
Jira/Confluence retrieval adapter (local_fixture default, jira_confluence_live for production — see workflows/planning.md "Source adapters")
        ↓
Context Normalizer (Claude)
        ↓
deterministic validation/readiness
        ↓
Planning Analyst (Claude)
        ↓
Plan Author (Claude)
        ↓
Plan Reviewer (Claude)
        ↓
revise / re-normalize / human escalation
        ↓
reviewed Development Plan
        ↓
HUMAN APPROVAL
```

[Delivery Workflow 2](../.agentic-sdlc/contracts/delivery-workflow.md) implements approved plans and manages
verification, review and remediation. Install its bundled workflow with
`.agentic-sdlc/cao/workflows/install_deliver.sh`; the delivery profiles are listed in the profile guide.
Delivery defaults to a hybrid implementation stage: a code supervisor assigns
approved work to registered workers with required skills, Python dispatches them
sequentially, and an integration pass reconciles the feature before verification.
See [hybrid delivery](workflows/hybrid-delivery.md) for configuration, limitations
and step-by-step specialist/skill extension procedures.

[Source Review Workflow 3](workflows/source-review.md) reviews an existing GitHub
PR independently of planning/delivery. Five agents map source, review correctness and
security, validate findings and author feedback. A deterministic gate routes findings
to `AUTO_FIX` or `HUMAN_REQUIRED`. The guide covers installation, running, publication,
isolation and development-agent handoff.

## Layout

`.agentic-sdlc/` holds only what the workflows read or write at runtime; paths are resolved directly by workflow code, the installer and the write-scope hook.

- `.agentic-sdlc/contracts/` — lifecycle and context contracts read by Planning and Delivery.
- `.agentic-sdlc/policies/` — governance and PR-review policy.
- `.agentic-sdlc/schemas/` — machine-readable Planning Context schema.
- `.agentic-sdlc/templates/` — Development Plan template, plus the approval-record and Human Review Brief templates named by the delivery contract.
- `.agentic-sdlc/cao/profiles/` — repository-owned CAO agent profiles.
- `.agentic-sdlc/cao/workflows/` — local workflow entry points and installer commands.
- `.agentic-sdlc/cao/sdlc_workflows/` — workflow implementations and shared execution modules.
- `.agentic-sdlc/cao/build_workflow.py`, `install_workflow.py` — standalone CAO deployment builder and installer; see [the deployment guide](build-and-install.md).
- `.agentic-sdlc/scripts/` — deterministic human-decision recorders (`approve_plan.py`, `record_pr_approval.py`) and the optional source-review publisher.
- `.agentic-sdlc/runtime/<ticket>/<run-id>/` — temporary detailed execution evidence; Git-ignored; created on demand.

Outside `.agentic-sdlc/`:

- `sdlc-records/<ticket>/` — durable workflow evidence (approved plans, approval records, PR review results, manifests) intended for Git; created on demand. It is project data, so it lives outside the embeddable tooling directory and survives tooling upgrades. Tickets recorded before this location existed can be moved with `git mv .agentic-sdlc/records/<ticket> sdlc-records/<ticket>`.
- `docs/` — guides, contracts and policies that no workflow reads, verification records and reference templates.
- `examples/` — synthetic Jira/Confluence fixtures, the PAY-DEMO-001 demonstration records and the source-review fixture generator.
- `tools/` — operational helpers such as `run-report`.

## Safety model

Planning and review agents only write their instructed answer files under runtime evidence. Delivery implementer/remediator agents can write application files under `app/`, while source-review agents work in isolated run workspaces. The repository hook enforces these write boundaries because CAO worker permission bypasses do not provide path-level protection.

The Python workflows persist artifacts and own control flow. The workflow, not the model, decides whether review findings cause plan revision, context re-normalization, automatic remediation or human escalation.

A generated Development Plan is not approved by an agent. Human approval is recorded separately and is bound to the exact SHA-256 of the reviewed plan and the repository baseline SHA.
