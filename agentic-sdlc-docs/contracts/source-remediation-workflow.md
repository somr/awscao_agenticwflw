# Workflow contract — Source remediation

`source_remediate` consumes a completed `source_review` run and attempts selected `AUTO_FIX` findings.
It requires no ticket or approved Development Plan. Explicit invocation authorizes only the findings
bound to the validated report, exact reviewed HEAD and optional human selection.

The checkout must be clean and operator-configured, with the trusted write-scope hook, registry application
verification suite and required profiles installed. Both GitHub tips must still match the original review.
Only the configured `sdlc_remediator` roots may change. Python owns Git, verification, records and publication.
This runs project code on the host; it is not an execution sandbox for hostile repositories.

Steps: validate evidence and authorization → branch at reviewed HEAD → baseline verification → bounded
remediation → scoped commit → verification → independent fix/regression review → Human Review Brief.
At most three fix batches run. A failed verification stops the loop. Reopened fixes, repeated no-progress,
scope growth, ambiguity and exhaustion require human handling. New findings are never added automatically.

Every selected finding must receive an explicit independent decision at the current candidate HEAD,
including findings accepted in earlier rounds. `FIXED` requires source and verification evidence that the
original failure is prevented. A fixer claim or absence from a findings list is insufficient. Introduced
regressions, scope violations or unassessable changes block publication. Original human findings stay visible.

Records live in `agentic-sdlc-records/source-remediation/pr-<number>-<run-id>/`, or
`local-<sha12>-<run-id>/`. They bind review/selection/configuration digests, original and candidate commits,
attempts, verification and independent decisions. Existing records are never overwritten by a new run.
States: `READY`, `REMEDIATING`, `VERIFIED`, `AGENT_REVIEWING`, `AWAITING_HUMAN_REVIEW`, `NO_CHANGES`,
`BLOCKED`, `FAILED`. Publication status is separate and never constitutes human PR approval.

Publication is a separate explicit command. It may fast-forward the existing PR branch and post done replies
only after the exact verified candidate is the remote PR HEAD and the base is unchanged. It cannot approve,
merge, force-push, resolve threads or edit/delete existing comments. Preserve receipts across partial failure.

See [policy](../policies/source-remediation.md) and [usage](../workflows/source-remediation.md).
