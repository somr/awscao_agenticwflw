# Developer guidance: <TICKET-ID>

<!--
Human-authored input to the planning workflow (`guidance_file`). Keep it short and specific.
Recommended location: agentic-sdlc-records/<TICKET-ID>/guidance.md (small UTF-8 file, inside the repository).

What it may do: resolve an ambiguity, choose between options the sources allow, narrow scope, constrain the design,
answer a reviewer finding.
What it may not do: relax an acceptance criterion, requirement or constraint from the Jira/Confluence sources, or
the governance policy. If a requirement is wrong, correct the source instead. The reviewer reports a conflict as
HUMAN_DECISION_REQUIRED.

Refer to reviewer findings as `r<round>:<id>` (for example `r2:PLAN-003`); bare ids repeat across reviews.
Source: the candidate's human-needed.json and reviews/ in agentic-sdlc-records/<TICKET-ID>/candidates/<run-id>/.
-->

## Decisions

- **D1** — <a decision the planner should treat as settled, with a one-line reason>.

## Constraints

- <a constraint on the design, e.g. "no new runtime dependency">.

## Clarifications

- <a fact the sources do not state but the team knows>.

## Answers to reviewer findings

- **r1:PLAN-001** — <what was decided and why; say when a finding is intentionally accepted as a limitation>.
