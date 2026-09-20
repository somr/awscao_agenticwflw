# Governance Policy

These invariants are workflow controls. Agent prompts may explain them, but prompts must not be the only enforcement mechanism.

## Mandatory invariants

1. **Human plan approval** — implementation cannot begin without approval of the exact Development Plan version.
2. **Requirements integrity** — agents cannot silently redefine Jira acceptance criteria or documented scope.
3. **Plan deviation** — a material deviation from the approved plan requires escalation and, where the approved decision changes, renewed approval.
4. **Independent review** — implementation output must receive an independent review before final human review.
5. **Read-only review** — the trusted PR-review role must not modify production code while acting as reviewer.
6. **High-impact ownership** — High-impact findings require developer ownership and cannot be autonomously closed by an agent.
7. **Human intent precedence** — an explicit request for developer/human judgment cannot be downgraded into an automatic fix.
8. **Verification after change** — every remediation batch must be followed by relevant verification.
9. **Fresh review evidence** — review evidence must refer to the current PR HEAD SHA. Code changes make earlier review evidence stale.
10. **Independent fix verification** — a fixing agent's claim that a finding is resolved is insufficient; the result must be independently verified/re-reviewed.
11. **Convergence limit** — autonomous review/fix loops are bounded. Repeated or unresolved findings escalate to a developer.
12. **Human final PR approval** — no agent may grant the final PR approval.
13. **Post-approval change** — material changes after approval require review again.
14. **QA separation** — delivery ends at approved PR; QA begins as a separate workflow.
15. **Traceability** — outputs must be traceable to Jira ticket, approved plan digest, repository baseline and PR HEAD.
16. **Developer guidance** — human-authored guidance may resolve ambiguity and constrain a plan but cannot relax a source requirement or this policy. It is trusted only because it is human-authored and lives outside every agent-writable path; the workflow records its digest with the plan it shaped, and human plan approval covers that exact guidance.
17. **Review gate** — only a plan whose independent review returned PASS is published for human approval. A run that ends without one leaves a non-approvable candidate for the human, never an approvable plan, and no human or agent can accept a plan with unresolved blocking findings.

## Material change guidance

Treat a change as material when it can alter any of the following:

- acceptance-criteria interpretation;
- architecture or component boundaries;
- public API or event contracts;
- persistence/schema behaviour;
- authentication/authorization/security boundaries;
- transaction/concurrency semantics;
- critical business rules;
- deployment topology or operational characteristics;
- major dependency choices;
- test strategy needed to establish correctness.

When uncertain, escalate rather than silently reinterpret the approved plan.
