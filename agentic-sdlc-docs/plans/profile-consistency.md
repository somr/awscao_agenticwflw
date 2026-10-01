# Plan: consistent, simpler agent profiles

Status: **done 2026-10-01**: all six steps committed, all 13 profiles reinstalled and live-verified
(see section 7). Each step is done, verified and reviewed with the user
before the next.

## 1. Goal

Make the 13 profiles in `.agentic-sdlc/cao/profiles/` consistent and simpler: the same rule is worded the same way
wherever it appears, and each profile carries only what its role needs. What any agent is allowed to do does not
change. Profile names stay unchanged: the write-scope hook, the
workflows, the installer and tests pin them.

## 2. Findings (checked 2026-09-26)

| # | Finding | Effect on agents |
|---|---|---|
| F1 | 10 descriptions say "Workflow 1/2/3"; the five source-review ones are terse ("Source-only PR review security for Workflow 3.") | None: `description` is used only for CAO's profile listing and search, never in the prompt |
| F2 | 8 profiles list `@builtin` in `allowedTools`; the five source-review ones don't | None today: for Claude Code the blocked tools are identical with or without it (checked with CAO's `get_disallowed_tools`). On OpenCode, `@builtin` expands to shell plus all file tools, so it is a latent shell grant if the provider changes |
| F3 | All 13 have `role: reviewer`, including implementer, remediator and code supervisor | None: CAO uses `role` only for default tools when `allowedTools` is missing, and every profile sets it |
| F4 | Three body structures: sections (planning, most delivery), none (`code-supervisor`), one shared template (source review) | Readability |
| F5 | The source-review template gives every role rules meant for others ("review only defects", "only propose automatic eligibility" for the mapper and the feedback author) and produces "You are the source review security." | Prompt noise |
| F6 | `source-feedback` says to write "for a development agent or human"; the workflow prompt now says to address the PR author without internal routing terms | Conflicting instructions |
| F7 | The write restriction is described in several ways; `remediator`, `pr-reviewer` and `code-supervisor` don't point to `reference/write-scope-hook.md`, and the source-review profiles are guarded by a different, generated hook | Readability; wrong pointer risk |
| F8 | The strict-JSON output checklist is copied in five profiles with small differences; the supervisor and source-review profiles use shorter wordings | Drift risk |

## 3. Decisions (accepted 2026-09-26)

| # | Decision | Proposal |
|---|---|---|
| D1 | How to keep shared rules consistent | **By hand, in one editing pass.** Rules that several profiles share (strict JSON output, answer file only, widened source roots, no execution, Git or PRs) get the same wording wherever they appear. No shared-blocks file, drift test or build step: the user judged the added machinery not worth it. Considered and rejected: a blocks file checked by a test, generating profiles from templates, moving the rules into workflow prompts, CAO skills. |
| D2 | Descriptions | One sentence, starting with what the agent does, naming the workflow by name (Planning, Delivery, Source review), never by number. |
| D3 | `@builtin` | Remove it from the eight profiles. It has no effect for Claude Code, would grant shell on OpenCode, and dropping it makes all 13 lists identical (`fs_read`, `fs_list`, `fs_write`). The profile reference records why, so it is not added back. |
| D4 | `role` | Keep `reviewer` on all 13 and document why: it has no effect while `allowedTools` is set, and it is the safest fallback (no shell) if a list were ever removed. |
| D5 | Body outline | Every profile: a one-paragraph mission, then `## Inputs` (when the task gives files), `## Responsibilities`, `## Boundaries`, `## Output`. Role-specific sections (developer guidance, previous reviews, finding semantics) stay where they are. |
| D6 | What lives where | Rules that are the same for every task of a role live in the profile; task data and per-run contracts (JSON shapes, paths) stay in the workflow prompt. Text that the source-review workflow prompt already sends to every agent is not repeated in the profiles. |

## 4. Steps, in order

Each step is a separate commit on a branch. It ends with `cao profile validate` for each changed profile, the full
test suite, reinstalling the changed profiles, and a check that the installed copies match the repository.
Profiles are reinstalled with `cao install`; the source-review profiles use the upgrade steps in the
source-review guide.

1. **Baseline and conventions.** Record that the installed profiles match the repository. Add a short
   "Profile conventions" section to `reference/agent-profiles.md` with D2 to D6.
2. **Frontmatter only (F1, F2, F3).** New descriptions (D2), `@builtin` removed (D3), `role` documented (D4).
   Verify that CAO blocks exactly the same tools for every profile before and after. No live run: nothing reaches
   the agents.
3. **Source-review profiles (F5, F6, F7, F8).** Rewrite the five: one mission and only the rules each role needs, the
   feedback author addressing the PR author, the write rule pointing to the source-review guide's generated hook.
   Remove what the workflow's shared prompt already says (D6). Live check: a fixture run on PR #1's commits,
   compared with runs `source-review-gapdedup-2` and `source-review-inline-pr2-1` (same findings and routes).
4. **Delivery profiles (F4, F7, F8).** Give `code-supervisor` the standard outline; add the missing write-scope pointer
   to `remediator` and `pr-reviewer`; use one wording for the shared rules (D1). Live check: one hybrid delivery run
   in a throwaway clone, compared with the last approved run.
5. **Planning profiles (F4, F8).** Align the outline and the shared rules' wording of the four planning profiles.
   Live check: one planning run in a throwaway clone, compared with the last approved run.
6. **Docs.** Update the profile tables in `reference/agent-profiles.md` and anything that quotes profile text, and
   remove the remaining workflow numbering from the write-scope hook's comments.

Step 2 is the foundation; steps 3 to 5 can be reordered. Step 3 goes first among them because it fixes a conflicting
instruction (F6). The wording chosen for a shared rule in the first of these steps is reused in the later ones.

## 5. Risks

- Profile text shapes agent behavior. Steps 3 to 5 each end with a live run; a change that alters findings, routes
  or plan quality is reverted or reworded before the next step.
- Installed profiles are machine-wide. Reinstall only when no run of that workflow is active.
- The source-review installer refuses to overwrite; follow its upgrade steps exactly.

## 6. Non-goals

Renaming profiles, changing any tool permission or write scope, changing workflow JSON contracts, a shared-blocks
file or drift test, and a profile build step.

## 7. Progress notes

- 2026-09-26, step 1 (`3c89a59`): conventions added to `reference/agent-profiles.md`. Baseline: all 13 installed
  profiles matched the repository.
- 2026-09-26, step 2 (`fa55612`): descriptions and tool lists updated. CAO blocks exactly the same eight Claude Code
  tools for every profile before and after (`Agent`, `Bash`, `BashOutput`, `KillShell`, `Monitor`, `Task`, `WebFetch`,
  `WebSearch`). Not reinstalled yet; nothing in this step reaches the agents.
- 2026-09-26, step 3 (commit after `fa55612`): the five source-review profiles rewritten. Shared wording chosen here,
  to reuse in steps 4 and 5: the "effectively read-only" write rule, "Never run tests, a build or any other command
  yourself: you have no execution, network or subagent tools.", and the strict-JSON checklist ending with "Use exactly
  the JSON shape given in the task." The runtime already appends the answer path and the one-line confirmation to
  every task, so profiles do not repeat them.
- 2026-10-01, reinstall: the five source-review profiles reinstalled with the upgrade steps (bundle unchanged), the
  eight planning and delivery profiles with `cao install`. All 13 installed copies and both bundles match the
  repository. 221 tests pass.
- 2026-10-01, step 3 live check (`source-review-profiles-1`, fixture on PR #1's commits): the same two findings and
  routes as `source-review-gapdedup-2` (HIGH HMAC bypass, HUMAN_REQUIRED; LOW off-by-one, AUTO_FIX); 5 coverage gaps
  instead of 4. The agent-written feedback addresses the PR author without routing terms (F6 fixed).
- 2026-10-01, step 4 (`f4ae709`): `code-supervisor` given the standard outline; shared write and no-command wording in
  all four delivery profiles; write-scope pointer added to `remediator` and `pr-reviewer`. Live check
  (`deliver-profiles-1`, hybrid, throwaway clone): `AWAITING_HUMAN_REVIEW`, 7 workers, 2 remediation rounds
  (approved run 10 had 1), one MEDIUM `DEVELOPER_REQUIRED` testing finding left; all app tests pass on the branch.
- 2026-10-01, step 5 (`34b5cc1`): shared write and no-command wording in the four planning profiles (the outline
  already matched). Live check (`plan-profiles-1`, throwaway clone): `PASS` in review round 1 with 4 advisory
  findings, about 9 minutes, comparable to approved run 24 (`PASS`, 5 advisories).
- 2026-10-01, step 6 (`e277231`): workflow numbering removed from the hook comments, `build-and-install.md` and
  `reference/agent-profiles.md`; catalog row for `sdlc_source_feedback` updated. Left as is: the planning prompt in
  `planning.py` still says "Planning Workflow 1" (prompt text, would need a bundle reinstall and a new live run).
