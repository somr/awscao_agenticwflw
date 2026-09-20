# Workflow Contract — Plan

## Purpose

Transform a Jira development request, its referenced Confluence documentation, and repository evidence into a technically reviewed Development Plan that a human can approve before implementation begins.

## Boundary

This workflow may retrieve context, normalize requirements, analyse and plan. It must not implement production changes.

## Required inputs

- Jira ticket identifier.
- Repository root.
- Base branch.
- Repository baseline commit SHA.
- Access to Jira and the referenced Confluence material, or equivalent mocked raw-source files during initial development.
- Optional additional context explicitly supplied by the user/team, as developer guidance (`guidance_file`: a small UTF-8 Markdown file inside the repository, outside `.agentic-sdlc/runtime/`).

## Sources of truth

1. Jira defines requested scope and ticket-level acceptance criteria where present.
2. Referenced Confluence documentation defines supporting functional/technical context.
3. The validated Planning Context is the stable requirements interface for downstream planning agents.
4. The repository defines the current implementation reality, not missing business intent.
5. Explicit human clarification overrides an agent assumption and must be recorded with provenance. Developer guidance is such a clarification: it may resolve ambiguity and constrain the design, cannot relax requirements from sources 1 to 3 or the governance policy, and is recorded by digest with the plan it shaped.

Agents must not silently invent missing requirements.

## Required activities

1. Retrieve the Jira ticket and referenced Confluence sources without semantic rewriting.
2. Normalize the retrieved material into the Context Package defined by `context-package.md`.
3. Validate the normalized context deterministically against `../schemas/planning-context.schema.json` and readiness policy.
4. Inspect the repository for affected components and existing patterns using the validated Planning Context.
5. Identify assumptions, ambiguities, dependencies and risks.
6. Propose an implementation approach.
7. Decompose the work into implementation tasks and dependencies.
8. Identify tasks that may execute in parallel.
9. Define the developer verification/test strategy.
10. Independently review the proposed plan against validated context, raw-source provenance and repository evidence.
11. Re-normalize context, revise the plan, or escalate to a human according to structured review findings.
12. Produce the Development Plan using `../templates/development-plan.md`.

## Required output

A Development Plan containing at minimum:

- ticket and source references;
- repository baseline SHA;
- problem statement and scope;
- acceptance criteria mapping;
- assumptions and unresolved questions;
- affected components;
- proposed design;
- implementation tasks and dependencies;
- parallelisation opportunities;
- test/verification strategy;
- risks and rollout/compatibility considerations;
- a sibling independent review artifact (`plan-review.json`) containing findings/dispositions and the SHA-256 of the exact plan reviewed.

After the plan passes independent review, compute a stable content digest (SHA-256) over the immutable plan file and bind the review artifact to that digest. Human approval is recorded separately using `../templates/plan-approval-record.json`, binding the decision to that digest and to the repository baseline.

## States

`CONTEXT_RETRIEVED -> CONTEXT_NORMALIZED -> CONTEXT_VALIDATED -> DRAFT -> AGENT_REVIEWED -> AWAITING_HUMAN_APPROVAL -> APPROVED | REJECTED`

A blocking context defect may transition to `AWAITING_HUMAN_CLARIFICATION` before planning continues.

A run that stops without a passing independent review (round limit reached, human decision required, or a context that is not ready) ends in `AWAITING_HUMAN_CLARIFICATION` and leaves a `NOT_CONVERGED` candidate under `agentic-sdlc-records/<ticket>/candidates/<run-id>/`. A candidate is evidence for the human. It is never a Development Plan record and cannot be approved or delivered.

## Human gate

Implementation is forbidden until the Development Plan reaches `APPROVED`. Only a plan whose independent review returned `PASS` is published for approval.

Approval applies to the exact plan content and repository baseline. A material plan change or material baseline change invalidates the approval and requires review again.

## Completion criteria

The workflow is complete when:

- the Context Package has been validated;
- the Development Plan is generated;
- independent plan review is complete;
- unresolved blocking questions are visible;
- the plan is presented for human approval.

Human approval is a lifecycle gate after the planning run; it is not delegated to an agent. Approval metadata must not be written back into the immutable plan file.
