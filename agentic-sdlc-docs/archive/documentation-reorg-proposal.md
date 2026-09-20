# Historical documentation reorganization review

This was a proposal from an earlier audit. It is retained for context; the current
documentation map is [docs/README.md](../README.md).

The Markdown files were reviewed against the current working tree, including uncommitted work. No existing files were changed during the review. This document records proposed actions only.

Recommended common documentation location: `docs/`.

## Documentation candidates

| File | Finding | Proposed action |
|---|---|---|
| [README.md](README.md) | Outdated and contradictory: describes v1.4 but installs a v1.3 archive; says both to reinstall profiles and leave v1.2 profiles unchanged. Covers planning only. | Move detailed setup to `docs/getting-started.md`. Correct installation instructions, include the required Claude hook, and document planning and delivery. Keep a short root README linking to `docs/`. |
| [.agentic-sdlc/README.md](.agentic-sdlc/README.md) | Obsolete implementation status: says delivery is unimplemented. Layout omits delivery components; safety description predates scoped answer-file writes. | Merge into `docs/architecture.md`; update implemented stages, directory layout and role-specific write permissions. |
| [CODEX_HANDOFF.md](CODEX_HANDOFF.md) | Historical instructions presented as current work: output transport is still described as unresolved and delivery as future work. | Archive under `docs/archive/`; preserve investigation evidence, mark superseded sections, and extract remaining work into a current status document. |
| [Agentic SDLC Planning Workflow — Codex CLI Engineering Handoff.md](<Agentic SDLC Planning Workflow — Codex CLI Engineering Handoff.md>) | Substantially overlaps `CODEX_HANDOFF.md`, with differences; repeats superseded objectives. | Compare unique content, consolidate into one archived handoff, and retire the duplicate. |
| [.agentic-sdlc/cao/workflows/README.md](.agentic-sdlc/cao/workflows/README.md) | Incomplete: documents planning only. Hook explanation omits the current `app/**` permission for implementer/remediator roles; output description omits Markdown answers. | Consolidate into `docs/workflows/`, covering planning, delivery installation/run commands, approval recording, output formats and current hook behavior. |
| [.agentic-sdlc/cao/profiles/README.md](.agentic-sdlc/cao/profiles/README.md) | Documents four planning profiles, although seven profiles exist. | Move to `docs/reference/agent-profiles.md`; add implementer, PR reviewer and remediator installation, responsibilities and permissions. |
| `RUN_REPORT.md` (retired) | Historical usage guide for the reporting helper. | The helper, pricing example, and operations guide were removed on 2026-09-20; the earlier relocation proposal no longer applies. |
| [app/README.md](app/README.md) | Incomplete: only gives a test command, without explaining the sample application's purpose. | Move to `docs/examples/payment-service.md`; explain its role in the lifecycle demo, components, SQLite stand-in and test command. |
| [.agentic-sdlc/contracts/context-package.md](.agentic-sdlc/contracts/context-package.md) | Example artifact layout differs from current output: implementation uses `context/raw/sources/` and versioned normalized filenames. | Move to `docs/contracts/context-package.md`; distinguish conceptual contract names from actual generated paths. |
| [.agentic-sdlc/contracts/planning-workflow.md](.agentic-sdlc/contracts/planning-workflow.md) | Useful current contract, dispersed from other documentation. | Move to `docs/contracts/planning-workflow.md`; repair relative links and workflow references. |
| [.agentic-sdlc/contracts/delivery-workflow.md](.agentic-sdlc/contracts/delivery-workflow.md) | Does not distinguish the full target lifecycle from currently implemented behavior. | Move to `docs/contracts/delivery-workflow.md`; add implementation status, local PR preparation versus actual PR creation, and human approval procedure. |
| `.agentic-sdlc/policies/governance.md` and `pr-review.md` | Useful policies, dispersed from other documentation. | Move to `docs/policies/`; update consuming workflow paths and cross-references. |

## Workflow inputs and evidence

Some Markdown files need separate treatment because they are workflow inputs or evidence.

| Files | Proposed action |
|---|---|
| `.agentic-sdlc/cao/profiles/*.md` excluding README | Active CAO configuration. Index from `docs/`; relocating requires updating installation paths and references. |
| `.agentic-sdlc/templates/development-plan.md`, `human-review-brief.md` | Intentional templates; placeholders are not unfinished documentation. If consolidating literally all Markdown, move to `docs/templates/` with corresponding consumer updates. |
| `.agentic-sdlc/examples/PAY-DEMO-001/jira.md`, `confluence/architecture.md`, `confluence/feature-spec.md` | Active input fixtures. Relocation to `docs/examples/PAY-DEMO-001/` must preserve structure and update context/source-directory references. |
| `.agentic-sdlc/records/PAY-DEMO-001/pr-body.md` | Stale: its HEAD SHA differs from the human review brief, which also identifies an undisclosed plan-verification gap. Regenerate for the intended review target and resolve or disclose deviations. |
| `.agentic-sdlc/records/PAY-DEMO-001/human-review-brief.md` | Pending review artifact: PR is “local, not yet created,” with unresolved findings. Complete through the delivery/review process; preserve the existing snapshot as evidence. |
| `.agentic-sdlc/records/PAY-DEMO-001/development-plan.md` | Approval-bound evidence. Preserve exact contents and index from `docs/`; relocation requires updating consumers. |
| `.agentic-sdlc/runtime/**/*.md` | Generated run evidence, including intermediate plans and source snapshots. Exclude from maintained documentation; assess retention separately rather than declaring old runs obsolete. |

A proposed `docs/README.md` would provide one entry point to the consolidated guides and links to operational Markdown that remains in place.
