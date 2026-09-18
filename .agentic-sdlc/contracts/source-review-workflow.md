# Workflow contract — source review

## Purpose and boundary

Review an existing PR's source and test code for introduced or worsened defects.
Produce evidence-based feedback routed to automatic fixing or human handling.
The workflow does not implement fixes, execute project code/tests, assess a plan or
CI, approve/merge a PR, or publish comments without a separate explicit command.

## Inputs and immutable evidence

GitHub.com PR URL plus a server-accessible artifact root; alternatively, a local Git
repository and exact base/head SHAs for fixtures. Pin both tips; review merge-base to
HEAD. Source exports, diff and agent answers are isolated per unique run ID.

## Required stages

Context mapping → independent correctness and security reviews → independent candidate
validation/deduplication → deterministic eligibility gate → feedback authoring.
Source semantics are agent decisions; routing, JSON validation, provenance and artifact
publication are deterministic orchestration. Account for every changed path and every
candidate. Reject unsupported findings; retain coverage limitations separately.

## Outputs and states

`code-review.json`, `comments.md`, candidate decisions and step evidence.
Successful orchestration emits `REVIEWED` or `STALE`, independently of coverage
`COMPLETE`/`INCOMPLETE`. Execution failure writes `failure.json` and fails the run.
A result with no findings is not an approval. Moving either PR tip invalidates currency.

Every accepted finding includes stable identity, reviewed HEAD, source location,
category/severity/confidence, failure scenario, evidence, consequence, remediation
direction, suggested verification, eligibility conditions, route and routing reasons.
Apply [source-review policy](../policies/source-review.md).

## Development handoff

Automatic eligibility permits a bounded attempt; the development workflow owns fixes,
verification and escalation. `HUMAN_REQUIRED` findings remain human work. New commits
require new review runs. Cross-run ID matching is best-effort; absence is not resolution.

See [installation and usage](../cao/workflows/source-review.md).
