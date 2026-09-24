# Documentation map

Start with the [project README](../README.md) for the quick start and current capabilities. Use this index to find detailed guides, specifications and evidence.

## Guides

| To… | Read… |
|---|---|
| Understand components, workflow relationships and deployment | [Architecture and diagrams](architecture.md) |
| Locate tooling, records and runtime evidence | [Layout and evidence lifecycle](architecture.md#layout-and-evidence-lifecycle) |
| Build, validate, install or upgrade workflows | [Build and install](build-and-install.md) |
| Run the workflow test suites | [Tests](build-and-install.md#tests) |
| Plan work, supply guidance, warm-start or approve a plan | [Planning](workflows/planning.md) |
| Deliver a plan, configure source roots or record a delivery decision | [Delivery](workflows/delivery.md) |
| Review an existing PR or publish feedback | [Source review](workflows/source-review.md) |
| Extend Delivery with workers, skills or verification toolchains | [Specialists and skills](workflows/hybrid-delivery.md) |
| Install or change agent profiles and providers | [Agent profiles](reference/agent-profiles.md) |
| Inspect the answer protocol and write hook | [Agent answers and write scope](reference/write-scope-hook.md) |
| Check current enforcement boundaries and limitations | [Enforcement and human authority](architecture.md#enforcement-and-human-authority) |

## Contracts and policies

- Runtime contracts: [Planning](../.agentic-sdlc/contracts/planning-workflow.md), [Context package](../.agentic-sdlc/contracts/context-package.md), [Delivery](../.agentic-sdlc/contracts/delivery-workflow.md).
- Runtime policies: [Governance](../.agentic-sdlc/policies/governance.md), [PR review](../.agentic-sdlc/policies/pr-review.md).
- Source-review specifications: [Contract](contracts/source-review-workflow.md), [Policy](policies/source-review.md).
- Schema: [Planning Context](../.agentic-sdlc/schemas/planning-context.schema.json).

## Templates

- [Development Plan](../.agentic-sdlc/templates/development-plan.md)
- [Plan approval record](../.agentic-sdlc/templates/plan-approval-record.json)
- [Human Review Brief](../.agentic-sdlc/templates/human-review-brief.md)
- [Planning Context example](templates/planning-context.example.json)
- [Developer guidance](templates/developer-guidance.md)

## Plans

Proposed work and remaining gaps:

- [Workflow hardening plan](../hardening-plan.md)
- [Future versions and reassessment criteria](../future-versions.md)

Implementation design history; use the workflow guides above for current operating instructions:

- [Planning guidance and warm start](plans/planning-guidance-and-warm-start.md)
- [Configurable source roots](plans/configurable-source-roots.md)

## Verification records

- [Modular refactor validation](verification/modularity-verification.md)
- [Source-review live verification](verification/source-review-live.md)
- [Hybrid delivery live verification](verification/hybrid-delivery-live.md)
- [Configurable source roots live verification](verification/configurable-source-roots-live.md)

## Examples

- [Payment-service application](../app/README.md)
- [PAY-DEMO-001 requirements fixture](../agentic-sdlc-local-inputs/PAY-DEMO-001/)
- [PAY-DEMO-001 records of a complete Planning and Delivery cycle](../agentic-sdlc-records/PAY-DEMO-001/) (the delivered code is on the local branch `sdlc/PAY-DEMO-001`, not on `main`)
- [Source-review fixture instructions](workflows/source-review.md#local-fixture)
