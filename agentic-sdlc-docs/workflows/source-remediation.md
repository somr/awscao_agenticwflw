# Source remediation (`source_remediate`)

Fix eligible Source review comments without a Jira ticket or Development Plan. The workflow reuses Delivery's
remediator, configured write roots, verification runner and execution support. A separate read-only reviewer
checks each attempted fix and the full PR diff. Source review itself remains read-only.

## Install

Wait until runs using the shared remediator have finished before updating its profile. Installation is explicit;
the workflow installer checks profiles exist and does not overwrite the shared profile automatically.

```bash
cao profile validate .agentic-sdlc/cao/profiles/remediator.md
cao profile validate .agentic-sdlc/cao/profiles/source-fix-reviewer.md
cao install .agentic-sdlc/cao/profiles/remediator.md
cao install .agentic-sdlc/cao/profiles/source-fix-reviewer.md
bash .agentic-sdlc/cao/workflows/install_source_remediate.sh "$PWD"
```

The target repository needs the current contract/policy, `.claude` write-scope hook and trusted registry
`.agentic-sdlc/cao/specialists.json`, including an `application` verification suite. Tests execute on the host;
use only operator-trusted repositories and configuration. The known broad runtime-answer write permission
remains the separate hardening task; this is not an operating-system isolation boundary.

## Run

Start from a clean checkout at the exact reviewed HEAD, with all base/merge-base objects available. Keep other
workflows and editors out of this checkout while it runs. Use a fresh run ID. Commit or deliberately relocate
previous untracked records before starting another run. Never delete evidence just to satisfy cleanliness.

```bash
cao workflow run source_remediate --wait --json --run-id fix-001 \
  --input repository_root="$PWD" \
  --input review_directory="$PWD/.agentic-sdlc/runtime/source-review/review-pr42-1"
```

By default all eligible findings are selected. Optional `selection_file` JSON narrows scope:

```json
{"selected":["SR-0123456789abcdef"],"excluded":[{"id":"SR-fedcba9876543210","reason":"Human will handle this"}]}
```

Unknown, duplicate, conflicting and ineligible selections are refused. Do not pass Markdown comments as
input: their structured report, mapping and validator evidence establish identity and eligibility. The original
report must be `REVIEWED`; moved GitHub base/HEAD requires a new review, even if the commented lines match.

The workflow creates `sdlc/source-remediate/<run-id>` and leaves the checkout on that branch. Baseline tests
run first; each fix batch is committed, verified and independently reviewed. At most three batches run.
Failed verification, regressions and scope violations block the candidate and preserve evidence for inspection.

## Results

For PR #42 and run `fix-001`: `agentic-sdlc-records/source-remediation/pr-42-fix-001/`.
Local reviews use `local-<sha12>-<run-id>/`. The workflow emits the full records path.

- `human-review-brief.md`: fixes, escalations, original human findings and coverage gaps.
- `remediation-manifest.json`: review identity, candidate, state, history and evidence hashes.
- `authorization.json`, `selected-findings.json`: exact authorized scope and exclusions.
- `fix-review-r<N>.json`, `verify-r<N>.json`: current-commit independent decisions and verification.
- `remediation-diff.patch`: cumulative repair changes.
- `publication.json`: created by the separate publisher, recording pushed state and replies.

Detailed logs remain under `.agentic-sdlc/runtime/source-remediation/<run-id>/`.
If a hard crash leaves `.agentic-sdlc/runtime/source-remediation.checkout.lock`, confirm that the workflow
and its terminals have stopped, inspect the preserved branch/evidence, then remove only that stale lock.
Do not reuse the run ID or delete source changes automatically.
`AWAITING_HUMAN_REVIEW` does not imply all findings were fixed. Read each resolution and `publication_safe`.
`NO_CHANGES` means no eligible work was selected; `BLOCKED` or `FAILED` means publication is not permitted.

## Publish after reviewing the local fixes

```bash
python3 .agentic-sdlc/scripts/publish_source_remediation.py \
  agentic-sdlc-records/source-remediation/pr-42-fix-001
```

Preview shows the exact notification bodies and optional push destination without writing to GitHub.
Add `--publish` to post replies after you have pushed the exact candidate yourself; add `--publish --push`
to fast-forward the existing PR branch first. `--push` alone is invalid. Fork destinations are taken from
the PR's actual head repository and ref, not assumed to be `origin`. A missing branch/access or moved remote
tip stops publication. There is no force-push, PR creation, approval, merge or thread resolution.

Only independently verified fixes at the current PR HEAD receive a done reply. A stable marker in comments
published by the updated Source review publisher identifies the original thread. Legacy/general findings
with published provenance use one PR conversation summary; omitted/unpublished findings get no notification.
Publish the original source review before starting remediation if you want that provenance captured in the run.

Push and comments are not atomic. A failure after pushing keeps the commit and records outstanding replies.
Retry the same command: GitHub markers recover completed notifications, including after a lost response.
A surviving `publication.lock` means another invocation is running or crashed: check the process and remote
state before removing that lock. Moving the PR again requires a new review; do not rewrite old evidence.

See [contract](../contracts/source-remediation-workflow.md), [policy](../policies/source-remediation.md),
[verification and limits](../verification/source-remediation.md), [Source review](source-review.md) and [Delivery](delivery.md).
