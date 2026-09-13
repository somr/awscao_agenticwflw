# PR Review and Remediation Policy

## Finding model

Each review finding must contain:

- stable finding ID;
- file/location;
- category;
- impact: `LOW | MEDIUM | HIGH`;
- reviewer confidence;
- failure scenario or concrete concern;
- potential consequence;
- related acceptance criterion or plan item when applicable;
- remediation direction;
- automation eligibility: `AUTO_FIX | DEVELOPER_REQUIRED`;
- reason for the automation decision;
- PR HEAD SHA reviewed.

## Routing policy

### Low impact

Normally eligible for automatic remediation when the requested change is deterministic and local.

Examples: formatting/lint cleanup, obvious naming/documentation defects, dead imports, straightforward test gaps, small defensive checks.

### Medium impact

May be automatically remediated only when all are true:

- reviewer confidence meets the configured threshold;
- the change is localized and bounded;
- no protected/sensitive boundary is crossed;
- the change does not reinterpret an approved requirement or design decision;
- correctness can be established through deterministic verification/tests;
- the remediation does not trigger a material plan deviation.

Otherwise route to `DEVELOPER_REQUIRED`.

### High impact

Always `DEVELOPER_REQUIRED`.

High-impact/protected areas include, at minimum:

- architecture changes;
- public API/event contract changes;
- database/schema migrations;
- authentication/authorization/IAM;
- cryptography/secrets handling;
- data-loss/corruption risks;
- concurrency/transaction semantics;
- critical financial/business rules;
- significant infrastructure topology changes;
- major dependency changes;
- changes that contradict or materially extend the approved plan.

## Human comments

Human review comments are triaged using the same model, except explicit requests for discussion, design judgment or developer action are always `DEVELOPER_REQUIRED` regardless of inferred severity.

## Re-review rule

Any code change invalidates review evidence for the previous PR HEAD. The reviewer must review the new HEAD before the automated portion can be considered converged.

## Convergence rule

Default maximum autonomous remediation rounds: **3**.

Escalate to a developer when:

- the limit is reached;
- the same finding reopens;
- review/fix agents disagree repeatedly;
- verification cannot establish correctness;
- the proposed remediation grows beyond its original local scope.
