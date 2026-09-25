# Source-only review and fix-routing policy

Policy version: `source-review-v1`. This policy applies only to Workflow 3; it does not
replace Workflow 2's PR review policy.

## Accepted finding

A concrete, source-supported defect introduced or worsened by the PR, anchored to a
changed production/test source file. Include a reachable failure scenario and observable
consequence. Exclude requirements/plan compliance, CI status, stylistic preferences,
speculation and pre-existing issues. Independent validation must accept it explicitly.

## AUTO_FIX

All conditions are mandatory for both LOW and MEDIUM severity:

- Sufficient source evidence (`evidence_sufficient`).
- Clear intended behavior (`intended_behavior_clear`).
- Local bounded scope (`localized_and_bounded`).
- Focused deterministic verification method (`deterministically_verifiable`).
- Confidence >= 0.90.
- No protected boundary detected by either the validator or context mapper.

## HUMAN_REQUIRED

Any failed condition, HIGH severity or protected category forces human handling.
Protected categories: `ARCHITECTURE`, `PUBLIC_API`, `DB_SCHEMA`, `AUTHN_AUTHZ`,
`CRYPTO_SECRETS`, `DATA_LOSS`, `CONCURRENCY`, `BUSINESS_RULES`, `INFRA_TOPOLOGY`,
`DEPENDENCY`. These represent material/sensitive changes, not every incidental use
of a dependency or business-domain identifier. Explicit mapper file-level classification
is conservative and applies to every finding on that file.

The orchestrator derives the route, reviewed HEAD and finding ID; agents cannot override
them. Missing/malformed required fields fail validation rather than defaulting to eligible.
Unsubstantiated candidates are rejected, not sent to a human as confirmed defects.

## Downstream obligations

Require current reviewed HEAD, assess coverage gaps, and verify the proposed fix.
Escalate expanding scope, ambiguous behavior, unsuccessful verification and repeated
findings. Never silently reinterpret `HUMAN_REQUIRED` as automatic eligibility.
No review result authorizes approval or merge. Re-review each changed HEAD; publishing
after the HEAD moved places only findings whose lines are unchanged and lists the rest
as needing a new review. What reaches GitHub is the human-edited draft, always as a
non-blocking `COMMENT` review.
