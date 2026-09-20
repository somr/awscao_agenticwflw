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
