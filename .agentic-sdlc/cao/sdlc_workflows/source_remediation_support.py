"""Pure contracts and Git helpers usable by both remediation and its publisher."""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

from .artifacts import _read_json, _sha256_file
from .source_review_contracts import (
    POLICY_VERSION, CONFIDENCE_THRESHOLD, ELIGIBILITY, require, string, strings, sha, safe_path, mapping_contract,
    validate_finding, adjudication_contract, route_finding,
)


def remediation_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
         '-c', 'diff.external=', '-C', str(repo), *args],
        capture_output=True, text=True, timeout=300,
    )
    require(result.returncode == 0, f"Git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def remediation_github(endpoint: str, *, payload: dict | None = None, paginate: bool = False):
    # Only PR metadata, published feedback and done notifications are supported.
    read = re.fullmatch(r'repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:pulls/[1-9][0-9]*(?:/reviews|/comments)?|issues/[1-9][0-9]*/comments)(?:\?per_page=100)?', endpoint)
    write = re.fullmatch(r'repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:pulls/[1-9][0-9]*/comments/[1-9][0-9]*/replies|issues/[1-9][0-9]*/comments)', endpoint)
    require(bool(read if payload is None else write), 'GitHub endpoint is outside remediation allow-list')
    if payload is not None:
        require(set(payload) == {'body'} and bool(payload['body']), 'Only comment bodies may be published')
    args = ['gh', 'api', '--hostname', 'github.com', endpoint]
    if paginate:
        args += ['--paginate', '--slurp']
    if payload is not None:
        args += ['--method', 'POST', '--input', '-']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, timeout=120)
    require(result.returncode == 0,
            f'GitHub request failed; check remote state before retrying: {result.stderr[:1000]}')
    return json.loads(result.stdout)


def remediation_pr_endpoint(snapshot: dict) -> str:
    repository = snapshot.get('repository')
    number = str(snapshot.get('pr_number'))
    require(isinstance(repository, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository),
            'A GitHub repository identity is required')
    require(re.fullmatch(r'[1-9][0-9]*', number) is not None, 'Invalid PR number')
    expected = f'https://github.com/{repository}/pull/{number}'
    require(snapshot.get('pr_url', '').rstrip('/') == expected, 'PR URL and identity disagree')
    return f'repos/{repository}/pulls/{number}'


def remediation_remote_matches(snapshot: dict, metadata: dict, head: str) -> None:
    require(metadata.get('state') == 'open', 'PR is no longer open')
    require(metadata['base']['repo']['full_name'].lower() == snapshot['repository'].lower(),
            'PR repository identity changed')
    require(str(metadata['number']) == str(snapshot['pr_number']), 'Wrong PR returned')
    require(metadata['base']['sha'] == snapshot['base_sha'], 'PR base moved; run a fresh review')
    require(metadata['head']['sha'] == head, 'PR HEAD moved; run a fresh review')


def load_remediation_review(directory: Path, repo: Path) -> tuple[dict, dict, dict]:
    files = ['code-review.json', 'workspace/snapshot.json', 'workspace/mapping.json',
             'workspace/candidates.json', 'workspace/adjudication.json', 'workspace/reported-gaps.json', 'workspace/diff.patch']
    for name in files:
        path = directory / name
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(directory.resolve()),
                f'Missing or unsafe review evidence: {name}')
    digests = {name: _sha256_file(directory / name) for name in files}
    report = _read_json(directory / files[0])
    require(report.get('schema_version') == 1 and report.get('policy_version') == POLICY_VERSION,
            'Unsupported Source review schema/policy')
    require(report.get('status') == 'REVIEWED', 'Only a REVIEWED report may authorize remediation')
    string(report.get('run_id'), 'source review run ID')
    strings(report.get('coverage_gaps'), 'coverage_gaps')
    require(report.get('coverage_status') in {'COMPLETE', 'INCOMPLETE'}, 'Invalid coverage status')
    require((report['coverage_status'] == 'INCOMPLETE') == bool(report['coverage_gaps']), 'Coverage status disagrees with gaps')
    snapshot = report['snapshot']
    require(snapshot == _read_json(directory / files[1]), 'Report snapshot differs from original evidence')
    for field in ('base_sha', 'head_sha', 'merge_base_sha'):
        sha(snapshot[field])
        require(remediation_git(repo, 'cat-file', '-t', snapshot[field]).strip() == 'commit', 'Missing snapshot commit')
    require(remediation_git(repo, 'merge-base', snapshot['base_sha'], snapshot['head_sha']).strip() == snapshot['merge_base_sha'],
            'Incorrect reviewed merge base')
    require(remediation_git(repo, 'rev-parse', 'HEAD').strip() == snapshot['head_sha'], 'Checkout must equal reviewed HEAD')
    if snapshot.get('pr_url'):
        remediation_pr_endpoint(snapshot)
    else:
        require(snapshot.get('repository') is None and snapshot.get('pr_number') is None, 'Inconsistent fixture identity')
    changed = remediation_git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z',
                              snapshot['merge_base_sha'], snapshot['head_sha'], '--').split('\0')
    require([p for p in changed if p] == snapshot['changed_files'], 'Reviewed changed paths differ from Git')
    actual_diff = remediation_git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--unified=3',
                                  snapshot['merge_base_sha'], snapshot['head_sha'], '--')
    require((directory / 'workspace/diff.patch').read_text() == actual_diff, 'Reviewed diff differs from Git')
    mapping = mapping_contract(_read_json(directory / files[2]), snapshot)
    candidates = _read_json(directory / files[3])
    require(isinstance(candidates, list), 'Candidates must be an array')
    # Validate against fresh blobs from the target repository, not merely the exported answer directory.
    for finding in candidates + [d['finding'] for d in _read_json(directory / files[4])['decisions'] if d.get('disposition') == 'ACCEPT']:
        validate_finding(finding, directory / 'workspace', snapshot, mapping)
        path = safe_path(finding['file'])
        commit = snapshot['head_sha'] if finding['side'] == 'head' else snapshot['merge_base_sha']
        source = directory / 'workspace/source' / finding['side'] / path
        require(source.resolve().is_relative_to((directory / 'workspace/source').resolve()), 'Source snapshot path escape')
        require(source.read_text() == remediation_git(repo, 'show', f'{commit}:{path}'), 'Exported source differs from Git')
    decisions = adjudication_contract(_read_json(directory / files[4]), candidates, directory / 'workspace',
                                      snapshot, mapping, _read_json(directory / files[5]))
    expected_gaps = sorted(set(snapshot['coverage_gaps'] + [entry['gap'] for entry in decisions['coverage_gaps']]))
    require(report['coverage_gaps'] == expected_gaps, 'Report dropped or altered validated coverage gaps')
    accepted = [route_finding(d['finding'], snapshot, mapping) for d in decisions['decisions'] if d['disposition'] == 'ACCEPT']
    require(isinstance(report.get('findings'), list), 'Missing findings')
    by_id = {f['stable_id']: f for f in report['findings']}
    require(len(by_id) == len(report['findings']) == len(accepted), 'Finding count or IDs differ from validation')
    for finding in accepted:
        actual = by_id.get(finding['stable_id'], {})
        require(all(actual.get(k) == v for k, v in finding.items()), 'Finding differs from accepted evidence/routing')
    require(report.get('queues') == {route: [f['stable_id'] for f in report['findings'] if f['route'] == route]
                                   for route in ('AUTO_FIX', 'HUMAN_REQUIRED')}, 'Queue does not match findings')
    return report, mapping, digests


def select_remediation_findings(report: dict, selection: dict | None) -> tuple[list[dict], list[dict]]:
    findings = {f['stable_id']: f for f in report['findings']}
    eligible = report['queues']['AUTO_FIX']
    excluded = {}
    if selection is None:
        selected = eligible
    else:
        require(isinstance(selection, dict) and set(selection) <= {'selected', 'excluded'}, 'Invalid selection shape')
        selected = strings(selection.get('selected'), 'selected')
        require(len(selected) == len(set(selected)), 'Duplicate selection')
        require(isinstance(selection.get('excluded', []), list), 'excluded must be an array')
        for entry in selection.get('excluded', []):
            require(isinstance(entry, dict), 'Exclusion must be an object')
            fid = entry.get('id')
            require(fid in findings and fid not in excluded and fid not in selected, 'Invalid/conflicting exclusion')
            excluded[fid] = string(entry.get('reason'), 'exclusion reason')
        require(all(fid in eligible for fid in selected), 'Selection may contain only known AUTO_FIX findings')
    return [findings[fid] for fid in selected], [
        {'id': fid, 'route': f['route'], 'reason': excluded.get(fid, 'Human handling required' if f['route'] == 'HUMAN_REQUIRED' else 'Not selected')}
        for fid, f in findings.items() if fid not in selected]


def fix_review_contract(value: dict, selected: list[dict], head: str) -> dict:
    require(isinstance(value, dict), 'Fix review must be an object')
    string(value.get('summary'), 'review summary')
    require(isinstance(value.get('resolutions'), list), 'resolutions array required')
    expected = {f['stable_id'] for f in selected}
    seen = set()
    for item in value['resolutions']:
        require(isinstance(item, dict), 'Each resolution must be an object')
        fid = item.get('id')
        require(isinstance(fid, str), 'Resolution ID must be a string')
        require(fid in expected and fid not in seen, 'Unknown or repeated resolution ID')
        seen.add(fid)
        require(item.get('status') in {'FIXED', 'UNRESOLVED', 'HUMAN_REQUIRED'}, 'Invalid resolution status')
        for field in ('reason', 'source_evidence', 'verification_evidence'):
            string(item.get(field), field)
        require(type(item.get('retry_eligible')) is bool, 'retry_eligible must be boolean')
        if item['retry_eligible']:
            require(item['status'] == 'UNRESOLVED', 'Only unresolved findings may be retried')
            string(item.get('next_step'), 'bounded next_step')
            conditions = item.get('retry_conditions')
            require(isinstance(conditions, dict), 'Retry requires independently reassessed eligibility conditions')
            require(all(conditions.get(flag) is True for flag in ELIGIBILITY)
                    and conditions.get('protected_boundary') is False, 'Retry no longer meets Source review eligibility')
            confidence = conditions.get('confidence')
            require(type(confidence) in (int, float) and math.isfinite(confidence)
                    and CONFIDENCE_THRESHOLD <= confidence <= 1, 'Retry confidence is below Source review threshold')
        else:
            require(isinstance(item.get('next_step', ''), str), 'next_step must be text')
    require(seen == expected, 'Review must account for every selected finding, including earlier fixes')
    for field in ('introduced_findings', 'scope_violations', 'coverage_gaps'):
        strings(value.get(field), field)
    require(type(value.get('candidate_assessable')) is bool, 'candidate_assessable must be boolean')
    return {**value, 'candidate_sha': sha(head),
            'publication_safe': value['candidate_assessable'] and not value['introduced_findings'] and not value['scope_violations']}
