# Development Plan — <JIRA-ID>

## Metadata

- Jira ticket: `<JIRA-ID>`
- Repository: `<repository>`
- Base branch: `<branch>`
- Repository baseline SHA: `<sha>`

> This plan is immutable once presented for human approval. Its SHA-256 and approval state are stored separately. Independent agent-review evidence is stored in `plan-review.json` and is bound to the SHA-256 of the exact plan content reviewed.

## Source references

### Jira
- <ticket/reference>

### Confluence
- <referenced page>

## Problem statement

<What needs to change and why.>

## Scope

### In scope
- ...

### Out of scope
- ...

## Acceptance criteria

| ID | Criterion | Planned implementation | Planned verification |
|---|---|---|---|
| AC-1 | ... | ... | ... |

## Assumptions and unresolved questions

- ...

## Repository / impact analysis

- Affected components: ...
- Existing patterns to reuse: ...
- Dependencies: ...

## Proposed design

<Concise technical design and rationale.>

## Implementation tasks

| Task | Goal | Depends on | Parallelizable |
|---|---|---|---|
| T1 | ... | - | Yes/No |

## Developer verification strategy

- Build/static checks: ...
- Unit tests: ...
- Integration tests: ...
- Other deterministic checks: ...

## Risks and compatibility / rollout considerations

- ...

## Independent plan review

Final independent review evidence is maintained in the sibling `plan-review.json` artifact and is cryptographically bound to this plan's SHA-256. The plan itself is not modified after the passing review merely to embed the review result.
