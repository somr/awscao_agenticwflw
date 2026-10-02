#!/usr/bin/env python3
"""Preview or explicitly publish verified source fixes and idempotent GitHub done replies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cao'))
from sdlc_workflows.artifacts import _read_json, _sha256_file
from sdlc_workflows.errors import WorkflowContractError
from sdlc_workflows.source_review_contracts import require, sha, safe_path
from sdlc_workflows.review_comments import finding_comment_marker
from sdlc_workflows.source_remediation_support import (
    remediation_git as git, remediation_github as gh,
    remediation_pr_endpoint, remediation_remote_matches,
)


def load_candidate(root: Path) -> tuple[dict, dict, dict]:
    manifest = _read_json(root / 'remediation-manifest.json')
    require(manifest.get('schema_version') == 1 and manifest.get('workflow') == 'source_remediate', 'Unsupported remediation manifest')
    require(manifest.get('state') == 'AWAITING_HUMAN_REVIEW' and manifest.get('publication_safe') is True,
            'Candidate is not independently verified for publication')
    for name, digest in manifest['evidence_sha256'].items():
        path = root / safe_path(name)
        require(path.resolve().is_relative_to(root.resolve()) and path.is_file() and not path.is_symlink(), 'Unsafe evidence path')
        require(_sha256_file(path) == digest, f'Evidence changed: {name}')
    required = {'source-review.json', 'selected-findings.json', 'authorization.json',
                'remediation-diff.patch', manifest['review_path'], manifest['verification_path']}
    require(required <= set(manifest['evidence_sha256']), 'Missing evidence digests')
    report = _read_json(root / 'source-review.json')
    review = _read_json(root / manifest['review_path'])
    verification = _read_json(root / manifest['verification_path'])
    candidate = sha(manifest['candidate_sha'])
    require(manifest['snapshot'] == report['snapshot'], 'Snapshot identity mismatch')
    require(_sha256_file(root / 'source-review.json') == manifest['review_sha256'], 'Source review digest mismatch')
    authorization = _read_json(root / 'authorization.json')
    require(authorization['snapshot'] == report['snapshot'] and authorization['review_sha256'] == manifest['review_sha256']
            and authorization['selected_ids'] == manifest['selected_ids'], 'Authorization mismatch')
    require(review['candidate_sha'] == verification['candidate_sha'] == candidate, 'Evidence does not describe the candidate')
    require(review['publication_safe'] is True and review['candidate_assessable'] is True
            and not review['introduced_findings'] and not review['scope_violations'], 'Review blocks publication')
    require(verification['passed'] is True and bool(verification['commands']) and all(
        c['passed'] is True and c['returncode'] == 0 for c in verification['commands']), 'Verification did not pass')
    require({r['id'] for r in review['resolutions']} == set(manifest['selected_ids'])
            and len(review['resolutions']) == len(manifest['selected_ids']), 'Incomplete final resolution accounting')
    fixed = {r['id'] for r in review['resolutions'] if r['status'] == 'FIXED'}
    require(fixed <= set(report['queues']['AUTO_FIX']), 'A human finding cannot be published as fixed')
    repo = Path(manifest['repository_root']).resolve()
    require(git(repo, 'rev-parse', 'HEAD').strip() == candidate, 'Checkout no longer points to verified candidate')
    require(not git(repo, 'diff', '--name-only', 'HEAD', '--').strip() and not git(repo, 'diff', '--cached', '--name-only').strip(),
            'Checkout has uncommitted changes')
    git(repo, 'merge-base', '--is-ancestor', report['snapshot']['head_sha'], candidate)
    patch = git(repo, 'diff', '--no-ext-diff', '--no-textconv', report['snapshot']['head_sha'], candidate, '--').rstrip() + '\n'
    require((root / 'remediation-diff.patch').read_text() == patch, 'Candidate diff does not match reviewed changes')
    return manifest, report, review


def done_marker(report: dict, fid: str, candidate: str) -> str:
    identity = [report['run_id'], report['snapshot'], fid, candidate]
    token = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return f'<!-- cao-source-remediation:{token} -->'


def prepare_publication(root: Path, *, push: bool = False) -> dict:
    manifest, report, review = load_candidate(root)
    snapshot = report['snapshot']
    endpoint = remediation_pr_endpoint(snapshot)
    metadata = gh(endpoint)
    candidate = manifest['candidate_sha']
    expected = candidate if metadata['head']['sha'] == candidate else snapshot['head_sha']
    remediation_remote_matches(snapshot, metadata, expected)
    require(push or expected == candidate, 'Verified commit is not the PR HEAD; push it first or use --publish --push')
    remote_repo = metadata['head'].get('repo')
    require(isinstance(remote_repo, dict), 'PR head repository was deleted')
    remote_name = remote_repo.get('full_name', '')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', remote_name) is not None, 'Invalid head repository')
    ref = metadata['head']['ref']
    git(Path(manifest['repository_root']), 'check-ref-format', f'refs/heads/{ref}')
    comments = [c for page in gh(endpoint + '/comments?per_page=100', paginate=True) for c in page]
    reviews = [r for page in gh(endpoint + '/reviews?per_page=100', paginate=True) for r in page]
    receipt_path = root / 'source-publication.json'
    source_receipt = _read_json(receipt_path) if receipt_path.exists() else {}
    if source_receipt:
        require('source-publication.json' in manifest['evidence_sha256'], 'Publication provenance is not bound to the run')
    original = next((r for r in reviews if r['id'] == source_receipt.get('review', {}).get('id')), None)
    identity = f"<!-- cao-source-review:{report['run_id']}:{snapshot['base_sha']}:{snapshot['head_sha']} -->"
    legacy = f"<!-- cao-source-review:{snapshot['base_sha']}:{snapshot['head_sha']} -->"
    require(original is None or identity in original.get('body', '') or legacy in original.get('body', ''), 'Publication review identity mismatch')
    decisions = {d['id']: d['outcome'] for d in source_receipt.get('decisions', [])}
    notifications, skipped = [], []
    for resolution in review['resolutions']:
        if resolution['status'] != 'FIXED':
            continue
        fid = resolution['id']
        if original is None or decisions.get(fid) in {'HELD', 'OMITTED'}:
            skipped.append({'id': fid, 'reason': 'No original published finding'})
            continue
        token = finding_comment_marker(report, fid)
        matching = [c for c in comments if c.get('pull_request_review_id') == original['id']
                    and not c.get('in_reply_to_id') and c.get('body', '').rstrip().endswith(token)]
        require(len(matching) <= 1, 'Ambiguous original comment mapping')
        known = decisions.get(fid) in {'INLINE', 'GENERAL'} or fid in original.get('body', '') or bool(matching)
        if not known:
            skipped.append({'id': fid, 'reason': 'No reliable published finding identity'})
            continue
        mapped = source_receipt.get('finding_comments', {}).get(fid)
        if mapped and matching:
            require(mapped['id'] == matching[0]['id'], 'Original comment ID mismatch')
        comment_id = matching[0]['id'] if matching else None
        commit_link = f"https://github.com/{snapshot['repository']}/commit/{candidate}"
        body = (f"Done in [{candidate[:12]}]({commit_link}) for `{fid}`. "
                "The configured verification suite passed and an independent source review confirmed this finding is fixed.")
        if comment_id is None:
            body += f"\n\nOriginal review: {original.get('html_url', snapshot['pr_url'])}"
        body += '\n\n' + done_marker(report, fid, candidate)
        notifications.append({'id': fid, 'comment_id': comment_id, 'body': body})
    # General findings share one summary notification; inline findings retain their threads.
    general = [n for n in notifications if n['comment_id'] is None]
    inline = [n for n in notifications if n['comment_id'] is not None]
    if general:
        inline.append({'id': 'general', 'finding_ids': [n['id'] for n in general], 'comment_id': None,
                       'body': '\n\n'.join(n['body'] for n in general)})
    return {'manifest': manifest, 'report': report, 'endpoint': endpoint, 'candidate_sha': candidate,
            'remote_url': f'https://github.com/{remote_name}.git', 'remote_ref': f'refs/heads/{ref}',
            'push_required': expected != candidate, 'notifications': inline, 'skipped': skipped}


def publish(root: Path, *, push: bool = False) -> dict:
    lock = root / 'publication.lock'
    with lock.open('x'):
        pass
    receipt_path = root / 'publication.json'
    receipt = {'status': 'INCOMPLETE', 'replies': []}

    def persist():
        temporary = root / 'publication.json.tmp'
        temporary.write_text(json.dumps(receipt, indent=2) + '\n')
        os.replace(temporary, receipt_path)

    try:
        plan = prepare_publication(root, push=push)
        snapshot = plan['report']['snapshot']
        endpoint = plan['endpoint']
        candidate = plan['candidate_sha']
        if receipt_path.exists():
            previous = _read_json(receipt_path)
            require(previous.get('candidate_sha') == candidate, 'Publication receipt belongs to another candidate')
            receipt = previous
        receipt.update(status='INCOMPLETE', candidate_sha=candidate, skipped=plan['skipped'])
        persist()
        if plan['push_required']:
            remediation_remote_matches(snapshot, gh(endpoint), snapshot['head_sha'])
            git(Path(plan['manifest']['repository_root']), 'push', plan['remote_url'], f"{candidate}:{plan['remote_ref']}")
        remediation_remote_matches(snapshot, gh(endpoint), candidate)
        receipt['pushed'] = True
        persist()
        for notification in plan['notifications']:
            remediation_remote_matches(snapshot, gh(endpoint), candidate)
            parent = notification['comment_id']
            target = endpoint + f'/comments/{parent}/replies' if parent else f"repos/{snapshot['repository']}/issues/{snapshot['pr_number']}/comments"
            listing = endpoint + '/comments?per_page=100' if parent else target + '?per_page=100'
            existing = [c for page in gh(listing, paginate=True) for c in page]
            ids = notification.get('finding_ids', [notification['id']])
            markers = [done_marker(plan['report'], fid, candidate) for fid in ids]
            matches = [c for c in existing if all(m in c.get('body', '') for m in markers)
                       and (not parent or c.get('in_reply_to_id') == parent)]
            # A lost response is recovered from GitHub before a new POST.
            posted = matches[0] if matches else gh(target, payload={'body': notification['body']})
            receipt['replies'] = [r for r in receipt['replies'] if r['finding_ids'] != ids]
            receipt['replies'].append({'finding_ids': ids, 'id': posted['id'], 'html_url': posted.get('html_url'),
                                       'parent_comment_id': parent, 'reused': bool(matches)})
            persist()
        remediation_remote_matches(snapshot, gh(endpoint), candidate)
        receipt['status'] = 'PUBLISHED'
        receipt.pop('error', None)
        persist()
        return receipt
    except Exception as exc:
        if 'candidate_sha' in receipt:
            receipt.update(status='INCOMPLETE', error=str(exc))
            persist()
        raise
    finally:
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('records_directory', type=Path)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--push', action='store_true', help='With --publish, fast-forward the existing PR branch before commenting')
    args = parser.parse_args()
    if args.push and not args.publish:
        parser.error('--push requires --publish')
    try:
        root = args.records_directory.resolve()
        if args.publish:
            result = publish(root, push=args.push)
        else:
            # Preview includes the optional push destination without performing it.
            plan = prepare_publication(root, push=True)
            result = {k: v for k, v in plan.items() if k not in {'manifest', 'report'}}
        print(json.dumps(result, indent=2))
    except (WorkflowContractError, OSError, ValueError, KeyError) as exc:
        raise SystemExit(f'Publication stopped: {exc}')


if __name__ == '__main__':
    main()
