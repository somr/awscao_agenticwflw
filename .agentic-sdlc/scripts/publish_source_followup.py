#!/usr/bin/env python3
"""Publish a human-edited follow-up draft: replies in the review threads and the new findings.

Without --publish nothing is written to GitHub: the draft is parsed, every thread and anchor is
checked against the pull request as it is now, and the planned requests are printed. With
--publish the tool posts, in this order:

- one reply per thread whose block says publish="yes" (in the thread, under its first comment);
- one PR conversation comment holding the replies to findings of a review's general comment;
- one review with event COMMENT holding the new findings, when any are published.

Every reply carries a marker with the run id, the thread (or finding) and the verdict, so a rerun
reuses what is already there and later follow-ups skip threads answered ADDRESSED. The tool never
resolves, edits or deletes a thread or comment, never approves or requests changes, and never merges.
If the PR moved since the run, replies say so, and new findings are held back for a new follow-up.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cao'))
from sdlc_workflows.followup_draft import FollowupDraftError, parse_draft, blob_link  # noqa: E402
from sdlc_workflows.review_comments import finding_comment_marker, hunk_ranges  # noqa: E402
from sdlc_workflows.review_threads import FOLLOWUP_MARKER, fetch_threads, followup_github, followup_marker  # noqa: E402

MAX_BODY = 65536
FOLLOWUP_RUNTIME = '.agentic-sdlc/runtime/source-followup'
# The complete set of writes; tests pin it. Reads go through review_threads.followup_github.
ALLOWED_WRITES = ('reply', 'conversation', 'review')


def post(kind: str, repository: str, number: str, payload: dict, parent: int | None = None) -> dict:
    if kind not in ALLOWED_WRITES:
        raise ValueError(f'Refusing GitHub write outside the allow-list: {kind}')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not re.fullmatch(r'[1-9][0-9]*', str(number)):
        raise ValueError('Invalid repository or PR number')
    if kind == 'review':
        if payload.get('event') != 'COMMENT' or set(payload) != {'commit_id', 'event', 'body', 'comments'}:
            raise ValueError('Only COMMENT reviews may be created')
        endpoint = f'repos/{repository}/pulls/{number}/reviews'
    else:
        if set(payload) != {'body'} or not payload['body'].strip():
            raise ValueError('Only a comment body may be posted')
        if kind == 'reply':
            if not isinstance(parent, int) or parent < 1:
                raise ValueError('A reply needs the id of the thread\'s first comment')
            endpoint = f'repos/{repository}/pulls/{number}/comments/{parent}/replies'
        else:
            endpoint = f'repos/{repository}/issues/{number}/comments'
    result = subprocess.run(['gh', 'api', '--hostname', 'github.com', endpoint, '--method', 'POST', '--input', '-'],
                            input=json.dumps(payload), text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(f'GitHub rejected the {kind}; what was posted before is in publication.json, and a rerun '
                         f'is safe: {result.stderr[:1000]}')
    return json.loads(result.stdout)


def directories(results: Path) -> tuple[Path, Path]:
    """(results, working) of a follow-up run, from its records folder followup-<run-id>."""
    results = results.resolve()
    if not results.name.startswith('followup-') or not (results / 'follow-up.json').is_file():
        raise ValueError(f'{results} is not a follow-up results folder (followup-<run-id> with follow-up.json)')
    repo = results.parents[3]
    working = repo / FOLLOWUP_RUNTIME / results.name.removeprefix('followup-')
    if not working.is_dir():
        raise ValueError(f"The run's working directory {working} is missing")
    return results, working


def load(results: Path) -> tuple[dict, str]:
    report = json.loads((results / 'follow-up.json').read_text())
    if report.get('status') not in {'JUDGED', 'STALE'}:
        raise ValueError('Only JUDGED or STALE follow-ups can be published')
    if f"followup-{report.get('run_id')}" != results.name:
        raise ValueError(f"follow-up.json belongs to run {report.get('run_id')}, not {results.name}")
    snapshot = report['snapshot']
    if not snapshot.get('pr_url'):
        raise ValueError('This run followed up a local fixture, not a GitHub PR, so there is nothing to publish to')
    return report, (results / 'follow-up-draft.md').read_text()


def review_marker(report: dict) -> str:
    return f"<!-- cao-source-followup-review:{report['run_id']}:{report['snapshot']['head_sha']} -->"


def posted_by_run(comments: list[dict], run_id: str, subject: str, login: str) -> list[dict]:
    """Comments by the publishing account that carry this run's marker for subject (whatever the verdict)."""
    found = []
    for comment in comments:
        author = ((comment.get('author') or comment.get('user') or {}).get('login') or '').lower()
        if author == login.lower() and any(run == run_id and marked == subject
                                           for run, marked, _ in FOLLOWUP_MARKER.findall(comment.get('body') or '')):
            found.append(comment)
    return found


def plan(results: Path, github=followup_github, threads_reader=fetch_threads) -> dict:
    """Read the PR as it is now and decide every request; nothing is written."""
    report, draft_text = load(results)
    draft = parse_draft(draft_text, report)
    snapshot, run_id = report['snapshot'], report['run_id']
    repository, number = snapshot['repository'], snapshot['pr_number']
    metadata = github('pull', repository, number)
    if metadata['state'] != 'open':
        raise ValueError('PR is closed; nothing can be published')
    head = metadata['head']['sha']
    moved = head != snapshot['head_sha']
    note = (f"_Judged at `{snapshot['head_sha'][:12]}`; the PR has moved to `{head[:12]}` since, so later "
            "commits are not reflected here._\n\n") if moved else ''
    login = github('user')['login']
    threads = {node['id']: node for node in threads_reader(repository, number)}
    items = {item['key']: item for item in report['items']}
    replies, general, skipped = [], [], []
    for key, block in draft['replies'].items():
        item = items[key]
        if not block['publish']:
            skipped.append({'item': key, 'reason': 'publish="no"'})
            continue
        body = note + block['body']
        if key.startswith('G'):
            general.append({'item': key, 'finding_id': item['finding_id'], 'verdict': block['verdict'],
                            'verdict_changed': block['verdict'] != item['verdict'], 'body': body})
            continue
        node = threads.get(item['thread_id'])
        if node is None:
            skipped.append({'item': key, 'reason': 'the thread is gone from GitHub'})
            continue
        if node.get('isResolved'):
            skipped.append({'item': key, 'reason': 'the thread was resolved after the run'})
            continue
        comments = node['comments']['nodes']
        existing = posted_by_run(comments, run_id, item['thread_id'], login)
        full = body + '\n\n' + followup_marker(run_id, item['thread_id'], block['verdict'])
        if len(full) > MAX_BODY:
            raise ValueError(f'Reply {key} exceeds {MAX_BODY} characters')
        replies.append({'item': key, 'thread_id': item['thread_id'], 'parent': comments[0]['databaseId'],
                        'verdict': block['verdict'], 'verdict_changed': block['verdict'] != item['verdict'],
                        'body': full, 'existing': existing[0] if existing else None})
    conversation = None
    if general:
        sections = [f"**Follow-up on findings of the review's general comment** (run `{run_id}`):"]
        for entry in general:
            sections.append(f"**Finding `{entry['finding_id']}`: {entry['verdict'].replace('_', ' ').lower()}**\n\n"
                            f"{entry['body']}\n\n{followup_marker(run_id, entry['finding_id'], entry['verdict'])}")
        body = '\n\n'.join(sections)
        if len(body) > MAX_BODY:
            raise ValueError(f'The conversation reply exceeds {MAX_BODY} characters')
        existing = [c for c in github('issue_comments', repository, number)
                    if any(posted_by_run([c], run_id, e['finding_id'], login) for e in general)]
        conversation = {'items': [e['item'] for e in general], 'body': body,
                        'existing': existing[0] if existing else None}
    review = plan_review(report, draft, github, moved, head) if report['findings'] else None
    return {'report': report, 'login': login, 'moved': moved, 'head': head, 'replies': replies,
            'conversation': conversation, 'review': review, 'skipped': skipped,
            'draft_sha256': hashlib.sha256(draft_text.encode()).hexdigest(),
            'draft_edited': hashlib.sha256(draft_text.encode()).hexdigest() != report['draft_sha256']}


def plan_review(report: dict, draft: dict, github, moved: bool, head: str) -> dict | None:
    snapshot = report['snapshot']
    repository, number = snapshot['repository'], snapshot['pr_number']
    by_id = {f['stable_id']: f for f in report['findings']}
    patches = {f['filename']: f for f in github('files', repository, number)}
    comments, general_items, decisions = [], [], []
    for fid, block in draft['findings'].items():
        finding = by_id[fid]
        if not block['publish']:
            decisions.append({'id': fid, 'outcome': 'OMITTED'})
            continue
        if moved:
            decisions.append({'id': fid, 'outcome': 'HELD', 'reason': 'the PR moved after the run'})
            continue
        anchor, reason = block['anchor'], None
        if anchor is None:
            reason = finding['placement'].get('reason', 'placed in the general comment')
        else:
            entry = patches.get(anchor['path'])
            if entry is None or 'patch' not in entry:
                reason = "the file is not in the pull request's current diff"
            elif not any(low <= anchor['line_start'] and anchor['line_end'] <= high
                         for low, high in hunk_ranges(entry['patch'])[anchor['side']]):
                reason = f"lines {anchor['line_start']}-{anchor['line_end']} are not within one changed section of the diff"
        if reason:
            general_items.append((finding, block, reason))
            decisions.append({'id': fid, 'outcome': 'GENERAL', 'reason': reason})
            continue
        comment = {'path': anchor['path'], 'side': anchor['side'], 'line': anchor['line_end'],
                   'body': block['body'] + '\n\n' + finding_comment_marker(report, fid)}
        if anchor['line_start'] != anchor['line_end']:
            comment.update(start_line=anchor['line_start'], start_side=anchor['side'])
        comments.append(comment)
        decisions.append({'id': fid, 'outcome': 'INLINE', 'path': anchor['path'], 'side': anchor['side'],
                          'line_start': anchor['line_start'], 'line_end': anchor['line_end']})
    held = [d for d in decisions if d['outcome'] == 'HELD']
    if not comments and not general_items:
        return {'request': None, 'decisions': decisions, 'held': held}
    parts = [draft['general']]
    if general_items:
        parts.append('## Findings not placed beside the code')
        for finding, block, why in general_items:
            parts.append(f"### {blob_link(repository, snapshot['head_sha'], finding['file'], finding['line_start'], finding['line_end'])}"
                         f"\n\n_{why}._\n\n{block['body']}\n\n{finding_comment_marker(report, finding['stable_id'])}")
    body = '\n\n'.join(parts) + '\n\n' + review_marker(report)
    if len(body) > MAX_BODY:
        raise ValueError(f'The review body exceeds {MAX_BODY} characters; move findings beside the code or shorten it')
    request = {'commit_id': head, 'event': 'COMMENT', 'body': body, 'comments': comments}
    return {'request': request, 'decisions': decisions, 'held': held}


def publish(results: Path, github=followup_github, threads_reader=fetch_threads, poster=post) -> dict:
    results, working = directories(results)
    lock = working / 'publication.lock'
    # Exclusive lock against concurrent invocations; a crash leaves it: check the PR, then remove it.
    with lock.open('x'):
        pass
    receipt_path = results / 'publication.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {'replies': {}}

    def persist():
        temporary = results / 'publication.json.tmp'
        temporary.write_text(json.dumps(receipt, indent=2) + '\n')
        os.replace(temporary, receipt_path)

    try:
        planned = plan(results, github, threads_reader)
        report = planned['report']
        repository, number = report['snapshot']['repository'], report['snapshot']['pr_number']
        receipt.update(status='INCOMPLETE', head=planned['head'], moved=planned['moved'], skipped=planned['skipped'],
                       draft_sha256=planned['draft_sha256'], draft_edited=planned['draft_edited'])
        persist()
        for reply in planned['replies']:
            posted = reply['existing'] or poster('reply', repository, number, {'body': reply['body']}, reply['parent'])
            receipt['replies'][reply['item']] = {'id': posted.get('id') or posted.get('databaseId'),
                                                 'url': posted.get('html_url') or posted.get('url'),
                                                 'thread_id': reply['thread_id'], 'verdict': reply['verdict'],
                                                 'verdict_changed': reply['verdict_changed'],
                                                 'reused': reply['existing'] is not None}
            persist()
        if planned['conversation']:
            conversation = planned['conversation']
            posted = conversation['existing'] or poster('conversation', repository, number, {'body': conversation['body']})
            receipt['conversation'] = {'id': posted['id'], 'url': posted.get('html_url'), 'items': conversation['items'],
                                       'reused': conversation['existing'] is not None}
            persist()
        review = planned['review']
        if review is not None:
            receipt['decisions'], receipt['held'] = review['decisions'], review['held']
            if review['request'] is not None:
                existing = [r for r in github('reviews', repository, number)
                            if review_marker(report) in (r.get('body') or '')]
                posted = existing[0] if existing else poster('review', repository, number, review['request'])
                receipt['review'] = {'id': posted['id'], 'html_url': posted.get('html_url'), 'reused': bool(existing)}
                persist()
                # The create response omits the comments; read back what GitHub stored, to join later threads.
                receipt['finding_comments'] = {}
                for node in threads_reader(repository, number):
                    first = node['comments']['nodes'][0]
                    if (first.get('pullRequestReview') or {}).get('databaseId') != posted['id']:
                        continue
                    for finding in report['findings']:
                        if (first.get('body') or '').rstrip().endswith(finding_comment_marker(report, finding['stable_id'])):
                            receipt['finding_comments'][finding['stable_id']] = {'id': first['databaseId'], 'url': first.get('url')}
        receipt['status'] = 'PUBLISHED'
        receipt.pop('error', None)
        persist()
        return receipt
    except Exception as exc:
        receipt.update(status='INCOMPLETE', error=str(exc))
        persist()
        raise
    finally:
        lock.unlink()


def summary(planned: dict) -> str:
    lines = [f"PR moved since the run: {'yes' if planned['moved'] else 'no'}; draft edited: "
             f"{'yes' if planned['draft_edited'] else 'no'}; publishing as {planned['login']}"]
    for reply in planned['replies']:
        state = 'already posted' if reply['existing'] else 'new reply'
        changed = ' (verdict changed by you)' if reply['verdict_changed'] else ''
        lines.append(f"  {reply['item']}: {reply['verdict']}{changed}, {state} in thread {reply['thread_id']}")
    if planned['conversation']:
        state = 'already posted' if planned['conversation']['existing'] else 'new comment'
        lines.append(f"  {', '.join(planned['conversation']['items'])}: one PR conversation reply, {state}")
    for skip in planned['skipped']:
        lines.append(f"  {skip['item']}: not posted, {skip['reason']}")
    if planned['review']:
        for decision in planned['review']['decisions']:
            lines.append(f"  {decision['id']}: {decision['outcome']} " +
                         json.dumps({k: v for k, v in decision.items() if k not in {'id', 'outcome'}}))
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('results_directory', type=Path,
                        help='agentic-sdlc-records/source-review/<repo>-pr<N>/followup-<run-id>')
    parser.add_argument('--publish', action='store_true', help='Post the replies and the review on GitHub')
    args = parser.parse_args()
    try:
        results, _ = directories(args.results_directory)
        if args.publish:
            print(json.dumps(publish(results), indent=2))
        else:
            planned = plan(results)
            print(summary(planned))
            print(json.dumps({'replies': [{k: r[k] for k in ('item', 'parent', 'body')} for r in planned['replies']],
                              'conversation': planned['conversation'] and planned['conversation']['body'],
                              'review': planned['review'] and planned['review']['request']}, indent=2))
    except FollowupDraftError as exc:
        raise SystemExit(f'follow-up-draft.md: {exc}')
    except FileExistsError:
        raise SystemExit('publication.lock exists in the run\'s working directory: another publication is running '
                         'or crashed. Check the PR on GitHub, then remove the lock and rerun.')
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(f'Not published: {exc}')


if __name__ == '__main__':
    main()
