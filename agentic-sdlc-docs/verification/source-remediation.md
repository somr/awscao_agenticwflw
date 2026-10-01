# Source remediation verification

Checked on 2026-10-02 in the isolated `feature/source-remediation` worktree, based on `8bc6985`.
That initial validation preceded integration. Installed workflows and shared profiles were not replaced.

## Automated results

| Check | Result |
|---|---|
| `python3 -m unittest discover -s tests` (standalone bundled workflows) | **266 passed** |
| `SDLC_TEST_SOURCE=1 python3 -m unittest discover -s tests` (maintained source modules) | **266 passed** |
| New Source remediation suite | **29 passed**, included in both totals |
| CAO server validation via `install_workflow.py source_remediate --validate-only` | Passed; temporary candidate and lock removed |
| New/updated profile validation | Completed with only the existing `fs_write` vocabulary warning |
| Installer shell syntax and `git diff --check` | Passed |

Full-suite runs required local socket access for the existing write-scope tests. Initial sandboxed runs
could not open local test servers; the subsequent socket-enabled runs passed in both modes.

The fixtures use real temporary Git repositories, commits, source exports, application verification commands,
branch creation and file edits. Agent responses and GitHub API responses are controlled by the tests.

Covered behavior includes:

- Exact HEAD/merge-base identity, source exports and reviewed-diff integrity; validation of original routes,
  candidate decisions and coverage gaps; narrowing selection without promoting human findings.
- Preservation of dirty/staged work; denial of out-of-root changes; staging a deleted source root;
  refusing replay over existing records and rejecting unsafe run IDs.
- Baseline and post-fix verification, independent per-finding decisions, reviewer write detection,
  protected profile enforcement through the actual hook, three-attempt limit and escalation.
- Shared Source review eligibility during retries, regression publication blocks, flat PR/run record names,
  original human findings retained, and evidence bound to the candidate commit.
- Stable GitHub finding/comment mapping after human prose edits, legacy general-comment fallback,
  exact remote HEAD/base checks, fork push destination, endpoint restrictions, notification retries,
  recovery after a lost response and refusal to publish tampered evidence.
- Existing Planning, Delivery, Source review and standalone bundling regressions after shared-helper extraction.

## Integration into feature/project-config

On 2026-10-02 the user authorized merging `feature/source-remediation` and removing its worktree.
Integration preserves the current project's configuration loader and the runtime answer-wait budget.
Source remediation now reads verification from `agentic-sdlc-project.json`, pins that file during fixes,
and retains the legacy registry fallback. The inherited worktree helper also checks the project file;
the supervisor prompt requests the ownership paths required by the inherited dispatch validator.

Both complete suites passed after conflict resolution and these compatibility updates:

- Bundled workflows: **280 tests passed**.
- Maintained source modules (`SDLC_TEST_SOURCE=1`): **280 tests passed**.

The Source remediation suite now includes 32 tests, including project-file tampering, legacy settings,
and duplicate-setting rejection. Tests also cover project configuration differences across worktrees.
Local socket access was needed for the existing hook tests, as in the initial validation.

## Remaining live checks and deployment

No live CAO agent remediation run or live GitHub push/comment was performed for this change. The CAO server
check validates the bundle; it does not measure the new reviewer's semantic accuracy. The deterministic
fixtures demonstrate orchestration and policy handling with controlled agents, not real-agent convergence.

Before deployment, coordinate an idle window for the shared remediator profile and install the reviewed
profiles/bundles using the [workflow guide](../workflows/source-remediation.md). Then run the disposable local
fixture and explicitly designated draft-PR checks described in the [plan](../plans/source-remediation.md).
Live publication needs a designated test PR and explicit invocation of the separate publisher.

The existing broad runtime-answer write permission and host execution of configured verification commands
remain the documented hardening limitations. This change does not provide an operating-system sandbox or
cryptographic proof of the author of a locally supplied review report.
