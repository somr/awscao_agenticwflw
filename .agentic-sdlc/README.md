# Agentic SDLC on AWS Labs CAO

This directory is the repository-local, version-controlled contract and implementation for the agentic development lifecycle.

## Implemented now

Planning Workflow 1 is executable:

```text
local Jira/Confluence retrieval adapter
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

[Delivery Workflow 2](cao/workflows/deliver.py) implements approved plans and manages
verification, review and remediation.

[Source Review Workflow 3](cao/workflows/source-review.md) reviews an existing GitHub
PR independently of planning/delivery. Five agents map source, review correctness and
security, validate findings and author feedback. A deterministic gate routes findings
to `AUTO_FIX` or `HUMAN_REQUIRED`. The guide covers installation, running, publication,
isolation and development-agent handoff.

## Layout

- `contracts/` — lifecycle and context contracts.
- `policies/` — governance and PR-review policy.
- `schemas/` — machine-readable Planning Context schema.
- `templates/` — durable artifact templates.
- `cao/profiles/` — repository-owned CAO agent profiles.
- `cao/workflows/dev_plan.py` — Planning Workflow 1 Python orchestration.
- `scripts/approve_plan.py` — deterministic human approval recorder.
- `examples/` — synthetic Jira/Confluence fixtures.
- `records/<ticket>/` — durable planning evidence intended for Git.
- `runtime/<ticket>/<run-id>/` — temporary detailed execution evidence; Git-ignore this directory.

## Safety model

The four planning agents are read-only. They reason and return content; the Python workflow persists artifacts and owns control flow. The workflow, not the model, decides whether review findings cause plan revision, context re-normalization or human escalation.

A generated Development Plan is not approved by an agent. Human approval is recorded separately and is bound to the exact SHA-256 of the reviewed plan and the repository baseline SHA.
