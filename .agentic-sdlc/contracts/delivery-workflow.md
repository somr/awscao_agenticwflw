# Workflow Contract — Deliver

## Purpose

Implement an approved Development Plan, verify the implementation, create a pull request, reduce routine review noise through agentic review/remediation, and present a focused review package to the human reviewer for final approval.

## Required inputs

- Jira ticket identifier.
- Repository root.
- Base branch.
- Approved Development Plan.
- Digest of the approved Development Plan.
- Repository baseline commit SHA associated with the approval.
- Human approval evidence/reference matching `../templates/plan-approval-record.json`.

## Preconditions

- The plan status is `APPROVED`.
- The supplied plan digest matches the approved content.
- The repository baseline is compatible with the approved plan.
- Any material drift from the approved baseline is detected before implementation and escalated.

## Required activities

1. Decompose the approved plan into executable implementation work.
2. Implement independent tasks in parallel where safe.
3. Integrate all changes into a coherent candidate implementation.
4. Run developer verification: build, lint/static checks, unit/integration tests as applicable.
5. Create or update the pull request.
6. Perform independent read-only PR review against requirements, plan, repository conventions and test evidence.
7. Adjudicate and classify findings under `../policies/pr-review.md`.
8. Automatically remediate only eligible Low/Medium findings.
9. Route High, ambiguous or policy-protected findings to a developer.
10. Re-run verification after every remediation batch.
11. Re-review the current PR HEAD after changes.
12. Stop autonomous remediation when convergence limits are reached and escalate remaining findings.
13. Produce the Human Review Brief using `../templates/human-review-brief.md`.
14. Wait for human PR review and final approval.
15. Route human review comments through the same triage/remediation policy, while preserving explicit requests for developer/human judgment.

## Hybrid implementation execution

The default implementation mode is `hybrid`; `single` retains the original
single-implementer execution path. In hybrid mode, a read-only code supervisor
proposes up to 16 ordered assignments using the repository specialist registry.
Python validates worker/skill selections, unique task IDs and prior dependencies,
dispatches workers sequentially, persists results and runs an integration pass.
Parallel dispatch is reserved for a future implementation with isolated workspaces.
The existing application write boundary, approval checks, independent review and
bounded remediation remain mandatory. Required skills add their configured
verification suites; missing tooling is a verification failure.

## Required outputs

- Pull request reference.
- Current PR HEAD SHA.
- Verification/test evidence.
- Structured agent review findings.
- Remediation history.
- Outstanding/escalated issues.
- Human Review Brief.
- Final human PR approval state.

## States

`READY -> IMPLEMENTING -> VERIFYING -> PR_CREATED -> AGENT_REVIEWING -> REMEDIATING -> AWAITING_HUMAN_REVIEW -> HUMAN_APPROVED`

Additional terminal/exception states:

`BLOCKED | REJECTED | FAILED`

## Human gate

Only a human reviewer may provide the final PR approval.

Any material code modification after human approval invalidates that approval according to repository branch-protection/review policy and requires review of the resulting PR HEAD.

## QA boundary

This workflow ends at an approved PR and emits the handoff evidence required by the separate QA workflow. It does not perform the downstream agentic QA process.
