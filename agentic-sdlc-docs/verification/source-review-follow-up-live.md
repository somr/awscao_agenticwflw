# Follow-up on review comments: live verification

Checks of `source_followup` against a running `cao-server` with the `claude_code` provider, on 2026-11-18. See the
[guide](../workflows/source-review-follow-up.md) for how the workflow works.

## Local fixture

Built with `agentic-sdlc-local-inputs/source-followup/create_fixture.py`: one published Source review (a NAMING-1 rule
finding beside the code and a finding in the general comment), five hand-written threads, one resolved thread and one
the developer started, and a developer push that fixes some comments, part of one, and adds a COMMENTS-1 violation.

| Run | What it checked | Result |
|---|---|---|
| `followup-fixture-1` | Judges only, two batches of three | All six verdicts as planted: rule thread `ADDRESSED` with `NOT_VIOLATED`; fix in another file `ADDRESSED`; "done" without a change plus an instruction to the "review bot" `NOT_ADDRESSED` (the judge named the attempt); partial fix `PARTIALLY_ADDRESSED`; `DISPUTED` with no drafted reply; general finding `ADDRESSED`. Resolved and developer-started threads skipped. Every step passed its contract on the first attempt. |
| `followup-fixture-2` | Judges plus the change check | Failed before any agent ran: the fourth Claude Code terminal, started together with three others in a new workspace, exited at the workspace trust dialog ("initialization timed out"). CAO auto-accepts that dialog, but when the screen is not yet fully drawn it presses Enter on the default "No, exit". A start-up race in CAO, not in the workflow; any workflow that starts several terminals at once can hit it. |
| `followup-fixture-3` | Same as run 2 | The same six verdicts; the change check started at the recorded review head, the standards batch found the planted COMMENTS-1 violation (rule verdicts per file as expected), and the regression checker found a real regression nobody planted (`parse_quantity(3)` now raises `AttributeError`). Records and draft written. |

## Real pull request

Draft PR #3 on this repository (`[TEST - DO NOT MERGE]`), with the fixture's code. One GitHub account played both the
reviewer and the developer, so the developer's replies count as the reviewer's; the follow-up markers are what F4
relies on, and those were only posted by the publisher.

1. `source_review` run `source-review-pr3-1` found the tax bug and the two NAMING-1 names; the review was published
   unedited (3 comments beside the code). Four more comments were added by hand. The developer pushed fixes and
   replied "done" (with the same injected instruction) and disputed one comment.
2. `followup-pr3-1`: 7 threads, 3 joined to their findings by marker, 4 hand-written. All verdicts as expected
   (4 `ADDRESSED`, 1 `NOT_ADDRESSED`, 1 `PARTIALLY_ADDRESSED`, 1 `DISPUTED`); the change check started at the review's
   head and found the COMMENTS-1 violation, the `AttributeError` regression and "`fmt` removed without an alias"
   (removed code, so placed in the general comment).
3. The draft was edited as a person would: the disputed thread was answered and its verdict changed to `ADDRESSED`,
   and the "removed without an alias" finding set to `publish="no"`. The preview showed the edit; `--publish` posted
   7 thread replies and one `COMMENT` review with 2 comments beside the code. Running `--publish` again reused all of
   them (18 PR comments before and after).
4. `followup-pr3-2` (no new push): the 5 threads answered `ADDRESSED` were skipped, the other 2 judged again with the
   same verdicts, and the change check had nothing to review (start commit = the head just checked). The follow-up's
   own 2 published findings were judged as hand-written threads because the installed bundle predated the code that
   reads follow-up records.
5. `followup-pr3-3`, after reinstalling: the same, with those 2 threads joined to their findings as tool threads.

## Limits

- The developer and the reviewer were one account; a second account would also exercise `by_reviewer` on replies.
- Not exercised live: a PR that moved between the run and publication (unit-tested), `BASE_UNKNOWN` after a
  force-push (unit-tested), the `reviewers` input, and comments on removed code (`BASE_SIDE`).
