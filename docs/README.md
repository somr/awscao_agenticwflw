# Documentation map

The root [README](../README.md) is the current project entry point. `.agentic-sdlc/` contains only what the workflows read or write at runtime; everything else lives here, in [`examples/`](../examples/) or in [`tools/`](../tools/).

## Guides

- [Architecture and repository layout](architecture.md)
- [Modular build, validation and installation](build-and-install.md)
- [Planning workflow](workflows/planning.md), including the write-scope hook design
- [Source-review workflow](workflows/source-review.md)
- [Hybrid delivery: adding specialists and skills](workflows/hybrid-delivery.md)
- [Agent profile catalog](reference/agent-profiles.md)
- [Run-report operations](operations/run-report.md)
- [Payment-service example](../app/README.md)

## Contracts and policies

- Read by workflow code, so kept under `.agentic-sdlc/`: [planning](../.agentic-sdlc/contracts/planning-workflow.md), [context package](../.agentic-sdlc/contracts/context-package.md), [delivery](../.agentic-sdlc/contracts/delivery-workflow.md), [governance](../.agentic-sdlc/policies/governance.md), [PR review](../.agentic-sdlc/policies/pr-review.md).
- Not read by workflow code: [source-review contract](contracts/source-review-workflow.md) and [source-review policy](policies/source-review.md).

## Templates

- Read by workflow code: `.agentic-sdlc/templates/development-plan.md`. The approval-record and Human Review Brief templates stay beside it because the delivery contract references them.
- Reference only: [`templates/planning-context.example.json`](templates/planning-context.example.json).

## Verification records

- [Modular refactor validation](verification/modularity-verification.md)
- [Source-review live verification](verification/source-review-live.md)

## Examples

- [`examples/PAY-DEMO-001/`](../examples/PAY-DEMO-001/) — Jira/Confluence fixture used as the planning `source_dir`, with the demonstration workflow records in `records/` (see its README; live records go to `sdlc-records/`).
- [`examples/source-review/`](../examples/source-review/) — fixture generator and sample review output.

## Archive

[Archive](archive/README.md) contains historical handoffs and the earlier documentation reorganization proposal. These files are evidence of previous development sessions, not current operating instructions.
