# Plan: source-review comments alongside the code on GitHub

Status: accepted 2026-09-25 (all decisions in section 3 as written); implemented and live-verified on
`feature/source-review-inline-comments` (see section 9).

## 1. Goal and target flow

Put the Source review's findings next to the code they concern in the GitHub pull
request. PRs can be large, so one PR-level review body listing every finding is not
workable. Each finding becomes its own comment beside the code. The general comment and
every finding comment are sent in one atomic `COMMENT` review, so a large PR triggers
one notification and never ends up half-posted. Nothing is ever approved or requested as a
change, and a person decides what is published. Unresolved comment threads may block a
merge when branch protection requires conversation resolution; that is accepted.

1. `source_review` pins the PR and exports its source (implemented).
2. The workflow analyzes the code and writes a general comment plus one comment per
   finding, each with its own code location (**partly implemented**; see section 2).
3. The reviewer sees first the findings that need their attention (**not implemented**).
4. The reviewer edits, drops or moves comments (**blocked today**).
5. The reviewer runs the publish tool, which posts a short general comment plus one
   comment per finding beside the code (**body-only today**). If the PR has moved since
   the review, the tool checks each finding against the new head before posting it.

## 2. Current state (at `f07d22f`)

- Each finding in `code-review.json` has `file`, `side` (`head`/`base`), `line_start`,
  `line_end` and a plain-language `comment` (about 700 to 1,200 characters). The data
  for an inline comment exists.
- The workflow only checks that the lines exist in the file (`source_review.py`
  `validate_finding`). It does not check that they fall inside a changed section of the
  diff, and GitHub rejects inline comments anywhere else.
- `render_comments` writes one Markdown document. `publish_source_review.py` posts it as
  the body of a single `COMMENT` review with no `comments[]`. Findings link to
  commit-specific lines but do not appear in the diff view.
- **Step 4 is blocked.** `build_payload` refuses unless `comments.md` still matches
  `comments_sha256` in `code-review.json`, so any human edit prevents publication.
- **Step 3 does not exist.** Findings appear in validator order. The same document
  serves the reviewer and the PR audience, so internal terms (`AUTO_FIX`, routing
  reasons, stable IDs) reach GitHub.
- Already correct: `event` is hard-coded to `COMMENT`, the head and base are rechecked
  before posting, and a retry reuses the review through the base:head marker.

## 3. Decisions (accepted 2026-09-25)

| # | Decision | Proposal |
|---|---|---|
| D1 | Where the human edits | A generated `review-draft.md` in the run directory, with strict markers per comment. `code-review.json` and `comments.md` stay unchanged as the audit record. |
| D2 | Findings that cannot be anchored (outside the diff, renamed files, binary files) | Move them to the general comment with their commit-specific link. Never fail the whole review because of one anchor. |
| D3 | Whose diff decides the anchors | The workflow pre-classifies with `diff.patch`. At publish time the tool re-checks against GitHub's own patches (`GET pulls/{n}/files`), which are authoritative. |
| D4 | What reaches GitHub | Inline: title, severity, explanation, fix direction, suggested verification. Routing, confidence and stable ID go into a collapsed `<details>` block. The reviewer can edit all of it. |
| D5 | Posting mode | **Confirmed 2026-09-25:** one atomic `COMMENT` review containing the general comment (summary, coverage gaps, findings that cannot be placed beside the code) and one comment per finding beside the code. Every location is checked against GitHub's current diff first, so one bad location cannot reject the batch. Unresolved threads blocking a merge is accepted. |
| D6 | Pending review in GitHub's UI (create without `event`, the human submits in GitHub) | Not the default: the human could then choose Approve or Request changes. Could be an explicit `--pending` flag later. |
| D7 | PR head moved between review and publication | **Confirmed direction 2026-09-25:** check each finding against the new head (section 4.3). Post only when its lines are unchanged; otherwise list it as needing re-review. A moved **base** (merge-base change) is handled the same way, through GitHub's current diff. |

## 4. Design

### 4.1 Workflow (`sdlc_workflows/source_review.py`)

- **Hunk index.** Parse `diff.patch` into, for each path, the head-side and base-side
  line ranges that GitHub accepts (added, removed and context lines within a hunk).
- **Placement.** For each routed finding, record
  `placement: {"mode": "INLINE"|"GENERAL", "reason": ...}`. The mode is `INLINE` only
  when `line_start` and `line_end` fall in the same hunk on the finding's side
  (`head` becomes `RIGHT`, `base` becomes `LEFT`). This is deterministic Python; agents
  do not choose anchors.
- **Draft.** Render `review-draft.md` (D1). The general comment comes first, then the
  findings ordered by attention: `HUMAN_REQUIRED` before `AUTO_FIX`, then severity,
  then confidence. Each finding is a block:

  ```markdown
  <!-- finding SR-4a25c4154b177682 publish=yes anchor=app/payment_service/callback_controller.py:RIGHT:31-34 -->
  **Missing or empty signature skips webhook HMAC verification** (HIGH)

  ...comment body the reviewer may edit...
  <!-- end finding -->
  ```

  `code-review.json` gains `draft_sha256` and each finding's `placement`. It never
  stores the edited text.
- **Feedback prompt.** Ask for an explanation addressed to the PR author, without
  internal routing terms (D4). The current `comment` field stays; only the wording
  guidance changes.
- **Reviewer view (step 3).** The draft header lists what needs a decision: the
  `HUMAN_REQUIRED` findings, which findings could not be placed inline, and the coverage
  gaps. The workflow's final output (`emit_output`) reports these counts.

### 4.2 Publish tool (`scripts/publish_source_review.py`)

- `check` (default, no network write): parse the draft strictly. Unknown markers,
  duplicate IDs, IDs not in `code-review.json`, a malformed anchor or an empty body
  all fail with a line-numbered error. The reviewer may:
  - edit any text;
  - set `publish=no`, optionally with `reason="..."`;
  - change an anchor;
  - move a finding to the general comment with `anchor=general`.

  Adding findings is refused. A human's own extra remarks go into the general comment.
- `check` also fetches `pulls/{n}` and `pulls/{n}/files` (reads only), re-validates each
  anchor against GitHub's patches (D3), and prints the exact payload: general body plus
  inline comments, and which findings were moved to the general comment and why.
- `--publish`: repeat the check, then send one `POST pulls/{n}/reviews` with
  `event: "COMMENT"`, `commit_id` (the reviewed head, or the current head after the
  section 4.3 check), `body` (the general comment and the base:head marker) and
  `comments[]` (`path`, `line`, `side`, and `start_line`/`start_side` for ranges). The
  request is atomic: either the whole review appears or nothing does. Before posting,
  every location is checked against GitHub's current diff and failures are moved to the
  general comment, so the batch is not rejected because of one comment. If GitHub still
  rejects it, nothing is posted, the error is shown and a rerun is safe.
- Idempotency: the review body carries `<!-- cao-source-review:<base>:<reviewed head> -->`.
  A rerun finds an existing review with that marker and reuses it.
- The receipt (`publication.json`) records the draft hash, whether the draft was edited,
  published, omitted and held findings with reasons, the location and commit of each
  comment, the review ID and the IDs of its comments.
- Hard guarantees, each covered by a test: `event` is always the literal `COMMENT`. The
  tool calls only `GET pulls/{n}`, `GET pulls/{n}/files`, `GET pulls/{n}/reviews` and
  `POST pulls/{n}/reviews`. It never approves, requests changes, dismisses, submits a
  pending review, edits or deletes comments, or resolves threads.
- Limits: 65,536 characters per body. GitHub documents no maximum number of comments
  per review. If a very large review is rejected for size, split it into a few atomic
  `COMMENT` reviews (at most one notification each), measured during the live check
  rather than guessed now.

### 4.3 Moved PR: per-finding currency check

When the PR's current head differs from the reviewed head, the tool fetches the new head
into the run's private `objects.git` (never the developer checkout) and checks each
finding separately. The review is then posted atomically on the new head, listing held
findings in the general comment:

| Check (deterministic) | Outcome |
|---|---|
| Anchored lines are identical at the new head after mapping line shifts through `git diff <reviewed>..<new> -- <file>`, and still inside GitHub's current diff | `CURRENT`: included at the mapped line, noting "reviewed at `<old>`; these lines are unchanged at `<new>`" |
| The anchored lines, or the hunk around them, changed | `CHANGED`: not posted; listed as needing re-review |
| Lines unchanged, but a mapper `related_files` entry changed | `CONTEXT_CHANGED`: held by default; `--include-context-changed` posts it with a warning |
| File deleted or renamed at the new head | `GONE`: not posted |

This proves only that the commented code is textually unchanged. A change elsewhere can
fix or invalidate a finding while its own lines stay the same. Only a new review run
judges relevance, and the tool and docs say so. Base-side (deleted-line) findings are
checked against the current merge-base in the same way.

### 4.4 Docs

The source-review guide (Results, Human decisions, Publishing to GitHub), the contract
(outputs), the policy (what reaches GitHub) and the README publication paragraph.

## 5. Files to change

`.agentic-sdlc/cao/sdlc_workflows/source_review.py`, `.agentic-sdlc/scripts/publish_source_review.py`,
`tests/test_source_review.py`, `agentic-sdlc-docs/workflows/source-review.md`,
`agentic-sdlc-docs/contracts/source-review-workflow.md`, `agentic-sdlc-docs/policies/source-review.md`,
`README.md`, `agentic-sdlc-docs/verification/source-review-live.md` (new section).

## 6. Milestones (each ends with both test modes passing)

1. **Hunk index and placement** in the workflow, with unit tests: added, removed and
   context lines, a range that spans two hunks, a base-side finding on a deleted line,
   a file absent from the diff, and a file with no newline at the end.
2. **Draft rendering and strict parsing**, with round-trip tests covering edits,
   `publish=no`, a moved anchor, a malformed marker and an unknown ID.
3. **Publish tool.** `check` against mocked responses; the `comments[]` payload shape and
   the forced `COMMENT` event; an endpoint allow-list test; locations outside the diff
   moved to the general comment; a rejected request posts nothing; retry reuses the
   existing review.
4. **Currency check** with real local Git commits: an unchanged line shifted by an insert
   above it (`CURRENT` at the new line), an edited anchored line (`CHANGED`), an edited
   related file (`CONTEXT_CHANGED`), a deleted file (`GONE`), and a moved base.
5. **Live check** on a new draft test PR (never merged, closed afterwards):
   - planted defects covering an added line, a deleted line (base side) and a defect
     whose natural anchor is unchanged code, which should fall back to the general
     comment;
   - edit one comment and set another to `publish=no`;
   - `check`, then `--publish`, then `--publish` again;
   - on a second test PR, or before the first publish, push a commit that edits one
     finding's lines and shifts another's, then publish and confirm the edited one is
     held (`CHANGED`) and the shifted one is `CURRENT` at its new line;
   - confirm through `pulls/{n}/comments` that the inline comments are on the right
     lines, the review state is `COMMENTED`, and a retry adds nothing.
6. Docs and verification record.

## 7. Risks and open questions

- R1: Comment threads beside the code must be resolved before merge when branch
  protection requires conversation resolution. **Accepted 2026-09-25**; documented for
  users.
- R6: One atomic request is all-or-nothing. Checking every location first makes a
  rejection unlikely. If one happens, nothing is posted and a rerun is safe.
- R2: GitHub's rename detection differs from `--no-renames`. Anchors on renamed files
  fall back to the general comment unless GitHub's `files` entry confirms the path.
- R3: `pulls/{n}/files` omits patches for very large files or diffs. Those anchors fall
  back to the general comment.
- R4: The review posts as the reviewer's `gh` account. A bot or app identity is out of
  scope.
- R5: Idempotency is per base and reviewed head. After a review has been posted, a
  re-edited draft for the same head is not posted again; the tool reports the existing
  review and never edits comments.

## 8. Non-goals

GitHub `suggestion` blocks or any code change (the workflow does not implement fixes),
approving or requesting changes, resolving or replying to threads, GitHub Enterprise,
judging semantic relevance after the head moves (that needs a new review run).

## 9. Progress notes (2026-09-25)

- Milestones 1-4 are done in one commit. `hunk_ranges` exists in both the workflow and the tool and is
  parity-tested. The regression suite has 216 tests and passes in both test modes.
- Deviation from section 4.3: `CHANGED` means the commented lines were edited or lines were inserted
  inside them. The surrounding hunk is not considered. A final line-by-line comparison double-checks
  every `CURRENT` result.
- Milestone 5 was run live on PR #2 and recorded in `verification/source-review-live.md`. `publish="no"`,
  `GONE`, a moved base and a rejected request are covered only by tests. The fallback that splits a very
  large review was not needed and is not built.
- Milestone 6 (docs) is done: guide, contract, policy and README.
- Follow-up idea: when a finding's range only partly overlaps a changed section (PR #2's repository
  finding), the placement could clip it to the overlap instead of using the general comment. For now the
  reviewer can re-anchor it in the draft.

