# Context Package Contract

## Purpose

Create a stable, validated boundary between enterprise source systems (initially Jira and Confluence) and downstream planning agents.

The package prevents every planning agent from independently interpreting raw enterprise data and makes missing, contradictory or unavailable information explicit.

## Stages

1. **Retrieve** — deterministic workflow code retrieves Jira and referenced Confluence material without semantic rewriting.
2. **Normalize** — `sdlc_context_normalizer` converts the raw sources into `planning-context.json` while preserving provenance.
3. **Validate structure** — deterministic workflow code validates the JSON against `../schemas/planning-context.schema.json`.
4. **Apply readiness checks** — deterministic workflow code evaluates mandatory completeness rules and blocking warnings.
5. **Render** — deterministic workflow code renders a human-readable `planning-context.md` from the validated JSON. The Markdown is a view, not a second source of truth.

## Raw package

Raw retrieved sources belong under the runtime directory, for example:

```text
.agentic-sdlc/runtime/PAY-1427/<run-id>/context/raw/
    jira.json
    confluence/
        CONF-4812.md
        CONF-4934.md
    retrieval.json
```

Raw files are runtime evidence and are not committed to Git.

Each retrieved source receives a stable source ID for the run, for example:

- `JIRA:PAY-1427`
- `CONF:4812`
- `CONF:4934`

The normalizer must use those IDs in provenance references.

## Normalized package

```text
.agentic-sdlc/runtime/PAY-1427/<run-id>/context/normalized/
    planning-context.json
    planning-context.md
    validation.json
```

`planning-context.json` is the authoritative normalized representation.

`planning-context.md` must be generated deterministically from the JSON and must not be independently authored by an agent.

`validation.json` records schema-validation results and readiness checks.

## Defensive rules

- Retrieval does not fix source data.
- Normalization may restate source text but must not invent business intent.
- Every requirement, acceptance criterion, constraint and non-functional requirement must have provenance.
- Contradictions are retained, not silently reconciled.
- Missing referenced documents are retained as retrieval warnings.
- Existing repository behaviour is not used by the Context Normalizer to redefine source requirements.
- A structurally valid package may still be semantically incomplete. Readiness checks decide whether planning can continue or must escalate.

## Suggested deterministic readiness checks

The workflow should mark the package as requiring clarification when any of the following is true:

- no acceptance criteria and no sufficiently explicit functional requirements are available;
- a contradiction is marked `blocking: true`;
- an open question is marked `blocking: true`;
- a required source could not be retrieved;
- provenance validation finds a reference to an unknown source ID.

Whether a non-blocking warning permits planning should remain a workflow policy rather than an agent decision.
