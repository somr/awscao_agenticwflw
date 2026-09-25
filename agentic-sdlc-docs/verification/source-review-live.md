# Workflow 3 implementation verification

## Deterministic checks

- Complete repository suite: **114 tests passed** (`python3 -m unittest discover -s tests`).
- Workflow 3 suite: **21 tests passed**, including real local Git fetches simulating
  GitHub PR refs, a moving HEAD, a diverged base, dirty-checkout preservation, source
  instruction/symlink exclusions, routing, write guards and publication behavior.
- Python compilation and installer shell syntax passed.
- CAO server-side workflow validation passed.

The full suite requires local sockets for existing write-scope-hook tests; these tests
were run with socket access. No application source was modified.

## Live CAO fixture

Both runs use disposable local repositories, the real CAO server and the actual five
Claude Code profiles. They do not contact or comment on a GitHub PR.

The first fixture run (`source-review-live-20260918-1`) completed all stages, exercised
bounded contract repair (unsupported severity values were rejected), and produced two
human-routed findings. It exposed overly broad public-API classification for a generic
helper. The prompts were clarified and the fixture made the helper explicitly internal.

The second fixture run (`source-review-live-20260918-2`) tests that clarification with
an internal slice regression and a missing account ownership check. It completed all five stages and produced:

| Finding | Route |
|---|---|
| `_first_items` returns one extra item | `AUTO_FIX` |
| `read_account` no longer checks ownership | `HUMAN_REQUIRED` |

The validator merged the duplicate authorization candidate. One contract repair
corrected candidate ID preservation. Final status: `REVIEWED`; coverage: `INCOMPLETE`,
explicitly retaining the fixture's limited application context and test-source gaps.
This demonstrates that finding eligibility and whole-review coverage are distinct.

The resulting [JSON artifact](../../agentic-sdlc-local-inputs/source-review/sample-review.json) and [comments](../../agentic-sdlc-local-inputs/source-review/sample-comments.md)
are retained as examples. The final additional exclusion of local agent-instruction
files and comment-hash normalization were regression-tested after this live run.

Runtime evidence is retained in the implementation worktree under
`.agentic-sdlc/runtime/source-review/<run-id>/`. These directories are Git-ignored;
archive them separately if desired. The fixture generator is versioned alongside this
record so the integration exercise can be reproduced with new commit SHAs and run IDs.

## Limits of this validation

GitHub transport metadata and publication are covered by mocked API responses plus
real Git object retrieval in local tests. No live GitHub write was performed. The live
fixture demonstrates orchestration and routing, not measured precision/recall across
real PRs. Missing context can still cause conservative coverage gaps or human routing.

## Live GitHub PR

Run `source-review-live-pr1-1` (2026-09-24) reviewed a real GitHub.com pull request in
GitHub mode: draft [PR #1](https://github.com/somr/awscao_agenticwflw/pull/1), branch
`test/source-review-live-pr` (head `65ece4d`) against `main` (`74e0cdb`). The PR is a
test PR that must never be merged. It adds webhook signature verification and a
diagnostics helper to `app/`, with two planted defects and three new tests that miss
both. The answer key was kept out of the PR, and the agents do not read the PR body.

Before the run, the installed `source_review` was still the single-file version from
`90795c4`. The installer refuses to overwrite it, so it was never rebuilt after the
modular split in `39c6d10`. Compared with a build of `main`, it behaved the same: 55 of
its 58 top-level definitions were unchanged. The two changed functions only reworded an
error message and added a parameter that `source_review` does not pass, and the one
removed function was never called. The five profiles already matched the repository.
The workflow was still upgraded with the documented procedure, so the run used exactly
the build of `main`, and the run's frozen bundle was checked against the build. The old
bundle was then deleted; it is recoverable from `90795c4`. `cao profile remove` cannot
remove these profiles: they are stored only in
`~/.aws/cli-agent-orchestrator/agent-context/`, which it does not check. They had to be
deleted there before the installer would accept them.

| Answer key | Result |
|---|---|
| `_recent_fulfilments` slices `[: limit + 1]` (internal helper, documented contract) → `AUTO_FIX` | Found: LOW, 0.96, `AUTO_FIX` |
| Missing or empty signature skips HMAC verification when a secret is configured → `HUMAN_REQUIRED` | Found: HIGH, 0.97, `HUMAN_REQUIRED` (authentication, secrets, public API) |
| Existing duplicate-refulfilment gap on `main`, not introduced by the PR → no finding | Not reported |

Every stage completed on its first attempt, and the run took 3 min 46 s. The pinned base, head and
merge base matched the PR, and the changed paths were exactly the three edited files.
Both reviewers raised the signature bypass. The validator merged the correctness
candidate into the security candidate as a duplicate and accepted all three candidates
(two findings). Precision and recall were both 2/2 on this answer key. Status was
`REVIEWED`. Coverage was `INCOMPLETE` because the snapshot excludes the repository's own
`.claude/` files and the reviewers noted context outside the repository, such as the host
HTTP layer and the provider signing scheme.

Publication was checked live. The preview matched the review. `--publish` posted one
`COMMENT` review ([review 5310975043](https://github.com/somr/awscao_agenticwflw/pull/1#pullrequestreview-5310975043))
with `commit_id` `65ece4d` and the base:head marker. An immediate second `--publish`
reused that review, and the PR still has exactly one review.

Observation: `coverage_gaps` is a union of every agent's free-text gaps, so the same gap
appears up to four times in different wording (18 entries for about six distinct gaps).
One entry is really a note on scope ("left to the correctness reviewer"), not a gap. The
published comment repeats the whole list.

### Merging duplicate coverage gaps

After this run, the Finding Validator was changed to merge reworded coverage gaps against
numbered `reported-gaps.json` entries. Python rejects an answer that leaves out any reported
gap. The shared prompt also stops agents from repeating snapshot gaps or reporting that the
review is static. The workflow was reinstalled with the new upgrade procedure. Both checks
reused PR #1's commits in fixture mode, with the first run's object store as the source,
because the closed PR can no longer be reviewed in GitHub mode.

- `source-review-gapdedup-1`: same findings and routes, but the agents reported no gaps at
  all. The first prompt only listed what not to report, and they dropped real limitations
  too. The prompt was reworded to define a coverage gap and ask for each one.
- `source-review-gapdedup-2` (3 min 33 s, no repairs): same findings and routes. The mapper
  and both reviewers reported six gaps, the same two limitations three times each. The
  validator merged them into two and accounted for every ID. The final list has four
  gaps: the two snapshot exclusions plus the host HTTP layer and the provider's signing
  contract.

Limits of the live PR run: stale-head handling and retry after a crash are still
covered only by local tests with mocked API responses. One live PR with two planted defects shows the reviewer and routing
behave correctly on a real GitHub PR. It does not measure precision or recall across
realistic PRs. Missing context can still cause conservative coverage gaps or human routing.

## Comments beside the code (PR #2)

Run `source-review-inline-pr2-1` (2026-09-25, 4 min 27 s) reviewed draft
[PR #2](https://github.com/somr/awscao_agenticwflw/pull/2) at head `bc4a669` with the
workflow from `feature/source-review-inline-comments`. The PR is a test PR that must never be
merged. It carries PR #1's two planted defects plus a third: `self._conn.commit()` deleted
from `PaymentRepository.record_if_new`.

| Answer key | Found | Placement |
|---|---|---|
| Missing or empty signature skips HMAC verification | HIGH, `HUMAN_REQUIRED` | Beside the code, RIGHT 30-34 |
| `_recent_fulfilments` off-by-one | LOW, `AUTO_FIX` | Beside the code, RIGHT 19-22 |
| Deleted `commit()` loses idempotency inserts | HIGH, `HUMAN_REQUIRED` | General comment: the reviewer anchored the whole `try` block (head 30-42), which extends past the changed section |

`review-draft.md` listed the two `HUMAN_REQUIRED` findings first and named the one placed in
the general comment. Acting as the human reviewer, the draft was edited:
- the general comment got a reviewer summary;
- the off-by-one comment got a reviewer note;
- the repository finding was re-anchored to the deleted line (`repository.py:LEFT:37-37`).

A preview then placed all three findings beside the code.

The PR was then moved on purpose (head `c78b8e2`): the signature-check line got a trailing
comment, and two lines were inserted above the off-by-one helper. The preview reported:

| Finding | Result |
|---|---|
| Signature bypass | `CHANGED`, held and listed as needing a new review |
| Off-by-one | `CURRENT`, placed at the shifted lines 21-24 with a "reviewed at / unchanged at" note |
| Deleted `commit()` | `CONTEXT_CHANGED`: the mapper related `callback_controller.py` to `repository.py`, and it changed |

The change in `callback_controller.py` was only a code comment, so the reviewer published
with `--include-context-changed`. One request created review 5316992580, `COMMENTED`, at
`c78b8e2`. It holds the general comment and two comments beside the code: `repository.py`
LEFT 37 (a deleted line) and `fulfilment_service.py` RIGHT 21-24. The held finding appears
in the general comment. A second `--publish` reused the same review. Afterwards the PR
showed no review decision and a `CLEAN` merge state. `publication.json` recorded the edited
draft, each finding's outcome and the commit.

Not exercised live (covered by the regression suite): `publish="no"`, the `GONE` result, a
moved base, and a request rejected by GitHub.
