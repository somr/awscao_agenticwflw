"""Real Git remediation fixtures; model and GitHub boundaries are controlled."""
import copy
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic-sdlc/cao'))
stub = types.ModuleType('cao_workflow')
stub.step = lambda *a, **k: None
stub.get_inputs = lambda: {}
stub.emit_output = lambda value: None
sys.modules.setdefault('cao_workflow', stub)
from build_workflow import build_source
from sdlc_workflows import source_review as source
from sdlc_workflows.artifacts import _write_json
from sdlc_workflows.review_comments import finding_comment_marker

if os.environ.get('SDLC_TEST_SOURCE') == '1':
    mod = importlib.import_module('sdlc_workflows.source_remediation')
else:
    mod = types.ModuleType('source_remediate_bundle')
    exec(compile(build_source('source_remediate'), '<source-remediation-bundle>', 'exec'), mod.__dict__)
spec = importlib.util.spec_from_file_location('publish_source_remediation', ROOT / '.agentic-sdlc/scripts/publish_source_remediation.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class RemediationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        (self.repo / '.gitignore').write_text('.agentic-sdlc/runtime/\n__pycache__/\n')
        for name in ('.claude/settings.json', '.claude/hooks/restrict-write-scope.py',
                     '.agentic-sdlc/policies/governance.md', mod.REMEDIATION_CONTRACT, mod.REMEDIATION_POLICY):
            dest = self.repo / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, dest)
        _write_json(self.repo / '.agentic-sdlc/cao/specialists.json', {'version': 1})
        _write_json(self.repo / 'agentic-sdlc-project.json', {
            'version': 1, 'source_roots': ['app'],
            'verification': {'application': [[sys.executable, 'app/check.py']]}})
        (self.repo / 'app').mkdir()
        (self.repo / 'app/code.py').write_text('def first(xs, n):\n    return xs[:n]\n')
        (self.repo / 'app/check.py').write_text('from code import first\nassert first([1], 5) == [1]\n')
        (self.repo / 'app/secret.py').write_text('def secret(owner):\n    assert owner\n    return 42\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        (self.repo / 'app/code.py').write_text('def first(xs, n):\n    return xs[:n+1]\n')
        (self.repo / 'app/secret.py').write_text('def secret(owner):\n    return 42\n')
        self.git('add', '.')
        self.git('commit', '-m', 'introduce defects')
        self.head = self.git('rev-parse', 'HEAD').strip()
        self.review_dir = self.repo / '.agentic-sdlc/runtime/source-review/fixture'
        self.review_dir.mkdir(parents=True)
        self.snapshot = source.prepare_snapshot({'source_repository': str(self.repo), 'base_sha': self.base,
                                                 'head_sha': self.head}, self.review_dir)
        self.mapping = {'summary': 'Two source changes', 'files': [
            {'file': p, 'scope': 'SOURCE', 'reason': 'Code', 'related_files': [], 'sensitive_boundaries': []}
            for p in self.snapshot['changed_files']], 'coverage_gaps': []}
        self.candidate = {**source.FINDING_SHAPE, 'candidate_id': 'correctness:C1', 'file': 'app/code.py',
                          'line_start': 2, 'line_end': 2, 'symbol': 'first', 'title': 'Slice includes extra item',
                          'failure_scenario': 'first([1,2],1) returns two items', 'severity': 'LOW'}
        self.human = {**self.candidate, 'candidate_id': 'security:S1', 'file': 'app/secret.py',
                      'symbol': 'secret', 'category': 'AUTHN_AUTHZ', 'severity': 'HIGH'}
        self.save_review()
        self.calls = []
        self.mode = 'fixed'

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True, stderr=subprocess.DEVNULL)

    def save_review(self, *, github=False, publication=False):
        if github:
            self.snapshot.update(repository='owner/repo', pr_number='42', pr_url='https://github.com/owner/repo/pull/42')
        findings = [source.route_finding(f, self.snapshot, self.mapping) for f in [self.candidate, self.human]]
        self.fid = findings[0]['stable_id']
        self.report = {'schema_version': 1, 'policy_version': source.POLICY_VERSION, 'run_id': 'review-1',
                       'status': 'REVIEWED', 'snapshot': self.snapshot, 'summary': 'Two defects',
                       'coverage_status': 'INCOMPLETE' if self.snapshot['coverage_gaps'] else 'COMPLETE',
                       'coverage_gaps': self.snapshot['coverage_gaps'], 'findings': findings,
                       'queues': {r: [f['stable_id'] for f in findings if f['route'] == r] for r in ['AUTO_FIX','HUMAN_REQUIRED']}}
        _write_json(self.review_dir / 'code-review.json', self.report)
        _write_json(self.review_dir / 'workspace/snapshot.json', self.snapshot)
        _write_json(self.review_dir / 'workspace/mapping.json', self.mapping)
        _write_json(self.review_dir / 'workspace/candidates.json', [self.candidate, self.human])
        _write_json(self.review_dir / 'workspace/reported-gaps.json', [])
        _write_json(self.review_dir / 'workspace/adjudication.json', {'decisions': [
            {'candidate_id': f['candidate_id'], 'disposition': 'ACCEPT', 'finding': f, 'reason': 'Source supported'}
            for f in [self.candidate, self.human]], 'coverage_gaps': [], 'snapshot_restatements': []})
        self.metadata = {'state': 'open', 'number': 42, 'base': {'sha': self.base, 'repo': {'full_name':'owner/repo'}},
                         'head': {'sha': self.head, 'ref': 'feature/fix', 'repo': {'full_name':'fork/repo'}}}
        if publication:
            self.original_review = {'id': 21, 'html_url': self.snapshot['pr_url'] + '#review-21',
                'body': f"<!-- cao-source-review:review-1:{self.base}:{self.head} -->"}
            self.original_comment = {'id': 300, 'body': 'Original finding\n\n' + finding_comment_marker(self.report, self.fid),
                                     'pull_request_review_id': 21, 'html_url': self.snapshot['pr_url'] + '#comment-300'}
            _write_json(self.review_dir / 'publication.json', {'review': self.original_review,
                'decisions': [{'id': self.fid, 'outcome': 'INLINE'}],
                'finding_comments': {self.fid: {'id': 300, 'review_id': 21}}})

    def agent(self, **kwargs):
        self.calls.append(kwargs['step_id'])
        if kwargs['agent'] == 'sdlc_remediator':
            if self.mode == 'outside':
                (self.repo / 'outside.txt').write_text('unrelated')
            elif self.mode != 'nochange':
                if self.mode == 'unresolved':
                    with (self.repo / 'app/code.py').open('a') as stream:
                        stream.write('# attempt\n')
                else:
                    (self.repo / 'app/code.py').write_text('def first(xs, n):\n    return xs[:n]\n')
                    (self.repo / 'app/check.py').write_text('from code import first\nassert first([1,2], 1) == [1]\n')
                if self.mode == 'verification_failure':
                    (self.repo / 'app/check.py').write_text('raise AssertionError("regression")\n')
            return kwargs['validator']({'findings_addressed': [self.fid], 'files_changed': ['app/code.py'],
                                         'assumptions': [], 'deviations': []})
        if self.mode == 'reviewer_write':
            (self.repo / 'app/code.py').write_text('tamper\n')
        unresolved = self.mode == 'unresolved'
        value = {'summary': 'Independent review', 'candidate_assessable': True,
                 'resolutions': [{'id': self.fid, 'status': 'UNRESOLVED' if unresolved else 'FIXED',
                     'reason': 'Examined original failure', 'source_evidence': 'app/code.py:2 slice boundary',
                     'verification_evidence': 'app/check.py exercises boundary', 'retry_eligible': unresolved,
                     'retry_conditions': {'confidence': .95, 'evidence_sufficient': True, 'intended_behavior_clear': True,
                         'localized_and_bounded': True, 'deterministically_verifiable': True, 'protected_boundary': False},
                     'next_step': f'bounded step {len(self.calls)}' if unresolved else ''}],
                 'introduced_findings': ['new regression'] if self.mode == 'regression' else [],
                 'scope_violations': [], 'coverage_gaps': []}
        return kwargs['validator'](value)

    def run_workflow(self, run='fix-001', **inputs):
        with patch.object(mod, '_run_json_contract_step', side_effect=self.agent), \
             patch.object(mod, 'check_remediation_profiles'), \
             patch.object(mod, 'remediation_github', side_effect=lambda endpoint: self.metadata):
            return mod.execute_source_remediation({'repository_root': str(self.repo),
                'review_directory': str(self.review_dir), **inputs}, run)

    def test_success_fixes_only_auto_finding_and_binds_all_evidence(self):
        result = self.run_workflow()
        self.assertEqual(result['state'], 'AWAITING_HUMAN_REVIEW', result)
        self.assertTrue(result['publication_safe'])
        self.assertEqual(self.calls, ['remediate-r1', 'fix-review-r1'])
        self.assertEqual(result['resolutions'][0]['status'], 'FIXED')
        self.assertEqual(result['excluded'][0]['route'], 'HUMAN_REQUIRED')
        self.assertEqual((self.repo / 'app/secret.py').read_text(), 'def secret(owner):\n    return 42\n')
        self.assertEqual(result['verification']['candidate_sha'], self.git('rev-parse','HEAD').strip())
        records = Path(result['records_directory'])
        self.assertTrue(records.name.startswith('local-' + self.head[:12]))
        self.assertTrue((records / 'human-review-brief.md').is_file())

    def test_pr_records_are_flat_and_fork_identity_uses_base_repo(self):
        self.save_review(github=True)
        result = self.run_workflow()
        self.assertEqual(Path(result['records_directory']).name, 'pr-42-fix-001')
        self.assertEqual(result['snapshot']['repository'], 'owner/repo')

    def test_project_verification_is_pinned_and_changes_block_the_run(self):
        original = self.agent

        def change_project(**kwargs):
            completion = original(**kwargs)
            if kwargs['agent'] == 'sdlc_remediator':
                (self.repo / 'agentic-sdlc-project.json').write_text('{}')
            return completion

        self.agent = change_project
        result = self.run_workflow()
        self.assertIn('agentic-sdlc-project.json', result['configuration_sha256'])
        self.assertEqual(result['state'], 'BLOCKED', result)
        self.assertIn('Protected evidence/configuration changed', result['reason'])
        self.assertEqual(self.calls, ['remediate-r1'])

    def test_legacy_registry_verification_without_project_file(self):
        project = self.repo / 'agentic-sdlc-project.json'
        _write_json(self.repo / '.agentic-sdlc/cao/specialists.json', json.loads(project.read_text()))
        project.unlink()
        roots, commands, pins = mod.remediation_configuration(self.repo)
        self.assertEqual(roots, ['app'])
        self.assertEqual(commands, [[sys.executable, 'app/check.py']])
        self.assertNotIn('agentic-sdlc-project.json', pins)

    def test_duplicate_project_and_registry_settings_are_rejected(self):
        _write_json(self.repo / '.agentic-sdlc/cao/specialists.json', {'source_roots': ['other']})
        with self.assertRaisesRegex(mod.WorkflowContractError, 'set in both'):
            mod.remediation_configuration(self.repo)

    def test_stale_and_tampered_queue_never_dispatch(self):
        for change in [{'status': 'STALE'}, {'queues': {'AUTO_FIX': [], 'HUMAN_REQUIRED': []}}]:
            with self.subTest(change=change):
                _write_json(self.review_dir / 'code-review.json', {**self.report, **change})
                result = self.run_workflow(run='invalid-' + str(len(list((self.repo / '.agentic-sdlc/runtime/source-remediation').glob('*')))) if (self.repo / '.agentic-sdlc/runtime/source-remediation').exists() else 'invalid-0')
                self.assertEqual(result['state'], 'BLOCKED')
                self.assertFalse(self.calls)

    def test_dirty_work_is_preserved(self):
        (self.repo / 'app/code.py').write_text('user change\n')
        result = self.run_workflow()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertEqual((self.repo / 'app/code.py').read_text(), 'user change\n')
        self.assertFalse(self.calls)

    def test_staged_work_is_preserved(self):
        (self.repo / 'app/code.py').write_text('user change\n')
        self.git('add','app/code.py')
        result = self.run_workflow()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertEqual(self.git('diff','--cached','--name-only').strip(), 'app/code.py')

    def test_selection_can_narrow_but_not_promote(self):
        selection = Path(self.temp.name) / 'selection.json'
        _write_json(selection, {'selected': []})
        result = self.run_workflow(selection_file=str(selection))
        self.assertEqual(result['state'], 'NO_CHANGES')
        self.assertFalse(self.calls)
        with self.assertRaises(Exception):
            mod.select_remediation_findings(self.report, {'selected': self.report['queues']['HUMAN_REQUIRED']})

    def test_reviewer_requires_every_id_and_rejects_invented_ids(self):
        selected = [self.report['findings'][0]]
        for entries in [[], [{'id':'unknown'}], [{'id':self.fid}, {'id':self.fid}]]:
            with self.subTest(entries=entries), self.assertRaises(Exception):
                mod.fix_review_contract({'summary':'check','resolutions':entries}, selected, self.head)

    def test_outside_change_blocks_before_commit(self):
        self.mode = 'outside'
        result = self.run_workflow()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertEqual(self.git('rev-parse','HEAD').strip(), self.head)
        self.assertIn('outside', result['reason'])

    def test_no_source_change_escalates(self):
        self.mode = 'nochange'
        result = self.run_workflow()
        self.assertEqual(result['state'], 'AWAITING_HUMAN_REVIEW')
        self.assertFalse(result['publication_safe'])
        self.assertEqual(result['resolutions'][0]['status'], 'HUMAN_REQUIRED')
        self.assertEqual(len(self.calls), 1)

    def test_verification_failure_stops_before_review(self):
        self.mode = 'verification_failure'
        result = self.run_workflow()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertEqual(self.calls, ['remediate-r1'])
        self.assertFalse(result['publication_safe'])

    def test_regressions_block_publication(self):
        self.mode = 'regression'
        result = self.run_workflow()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertFalse(result['publication_safe'])

    def test_reviewer_source_writes_are_detected(self):
        self.mode = 'reviewer_write'
        result = self.run_workflow()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertIn('changed', result['reason'])

    def test_at_most_three_fix_batches(self):
        self.mode = 'unresolved'
        result = self.run_workflow()
        self.assertEqual(result['state'], 'AWAITING_HUMAN_REVIEW', result)
        self.assertEqual(len(result['history']), 3)
        self.assertEqual(result['resolutions'][0]['status'], 'HUMAN_REQUIRED')

    def test_all_source_root_deletions_are_staged(self):
        records = self.repo / 'agentic-sdlc-records/source-remediation/delete'
        records.mkdir(parents=True)
        shutil.rmtree(self.repo / 'app')
        commit = mod.commit_remediation(self.repo, ['app'], records, 'delete source root')
        self.assertTrue(commit)
        self.assertEqual(self.git('ls-tree','--name-only',commit,'app').strip(), '')

    def test_second_run_does_not_overwrite_existing_evidence(self):
        result = self.run_workflow()
        records = Path(result['records_directory'])
        before = (records/'remediation-manifest.json').read_bytes()
        with self.assertRaises(Exception):
            self.run_workflow()
        self.assertEqual((records/'remediation-manifest.json').read_bytes(), before)

    def prepare_github(self):
        self.save_review(github=True, publication=True)
        result = self.run_workflow()
        self.assertEqual(result['state'], 'AWAITING_HUMAN_REVIEW', result)
        self.records = Path(result['records_directory'])
        self.candidate = result['candidate_sha']
        self.comments = [self.original_comment]
        self.issue_comments = []
        self.posts = []
        self.lost_response = False
        return result

    def github(self, endpoint, *, payload=None, paginate=False):
        if payload:
            self.posts.append((endpoint,payload))
            item = {'id': 400 + len(self.posts), 'body': payload['body'], 'html_url':'https://github.com/example'}
            if endpoint.endswith('/replies'):
                item['in_reply_to_id'] = 300
                self.comments.append(item)
            else:
                self.issue_comments.append(item)
            if self.lost_response:
                self.lost_response = False
                raise OSError('response lost after GitHub accepted comment')
            return item
        if '/issues/' in endpoint:
            return [self.issue_comments]
        if '/comments?' in endpoint:
            return [self.comments]
        if '/reviews?' in endpoint:
            return [[self.original_review]]
        return copy.deepcopy(self.metadata)

    def test_publication_requires_pushed_candidate_and_retries_without_duplicates(self):
        self.prepare_github()
        with patch.object(publisher, 'gh', side_effect=self.github):
            with self.assertRaises(Exception):
                publisher.publish(self.records)
            self.assertFalse(self.posts)
            self.metadata['head']['sha'] = self.candidate
            first = publisher.publish(self.records)
            second = publisher.publish(self.records)
        self.assertEqual(first['status'], 'PUBLISHED')
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(second['replies'][0]['reused'])
        self.assertTrue(self.posts[0][0].endswith('/comments/300/replies'))

    def test_lost_response_recovered_from_remote_marker(self):
        self.prepare_github()
        self.metadata['head']['sha'] = self.candidate
        self.lost_response = True
        with patch.object(publisher, 'gh', side_effect=self.github):
            with self.assertRaises(OSError):
                publisher.publish(self.records)
            result = publisher.publish(self.records)
        self.assertEqual(result['status'], 'PUBLISHED')
        self.assertEqual(len(self.posts), 1)

    def test_push_uses_fork_ref_and_posts_only_after_remote_confirmation(self):
        self.prepare_github()
        original_git = publisher.git
        pushes = []
        def git(repo,*args):
            if args[0] == 'push':
                pushes.append(args)
                self.assertFalse(self.posts)
                self.metadata['head']['sha'] = self.candidate
                return ''
            return original_git(repo,*args)
        with patch.object(publisher,'gh',side_effect=self.github), patch.object(publisher,'git',side_effect=git):
            publisher.publish(self.records, push=True)
        self.assertEqual(pushes, [('push','https://github.com/fork/repo.git',self.candidate+':refs/heads/feature/fix')])
        self.assertEqual(len(self.posts), 1)

    def test_changed_base_and_tampered_evidence_block(self):
        self.prepare_github()
        self.metadata['head']['sha'] = self.candidate
        self.metadata['base']['sha'] = 'a'*40
        with patch.object(publisher,'gh',side_effect=self.github), self.assertRaises(Exception):
            publisher.publish(self.records)
        self.assertFalse(self.posts)
        (self.records/'fix-review-r1.json').write_text('{}')
        with self.assertRaises(Exception):
            publisher.load_candidate(self.records)

    def test_legacy_comment_uses_general_summary(self):
        self.prepare_github()
        self.metadata['head']['sha'] = self.candidate
        self.original_comment['body'] = 'Legacy comment without identity marker'
        with patch.object(publisher,'gh',side_effect=self.github):
            publisher.publish(self.records)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0][0], 'repos/owner/repo/issues/42/comments')

    def test_notification_endpoint_allowlist_rejects_other_mutations(self):
        support = importlib.import_module('sdlc_workflows.source_remediation_support')
        for endpoint in ['repos/owner/repo/pulls/42/reviews', 'repos/owner/repo/pulls/42/merge', 'graphql']:
            with self.subTest(endpoint=endpoint), self.assertRaises(Exception):
                support.remediation_github(endpoint, payload={'body':'Done'})

    def test_run_id_cannot_escape_runtime(self):
        for run_id in ('..', '.', '../other', '', 'a/b'):
            with self.subTest(run_id=run_id), self.assertRaises(Exception):
                self.run_workflow(run=run_id)

    def test_fix_reviewer_cannot_be_granted_source_write_access(self):
        source_config = importlib.import_module('sdlc_workflows.source_config')
        with self.assertRaises(Exception):
            source_config.validate_source_config({'write_profiles': {'sdlc_source_fix_reviewer': None}})

    def test_retry_cannot_lower_source_review_eligibility(self):
        resolution = {'id': self.fid, 'status': 'UNRESOLVED', 'reason': 'Needs another attempt',
            'source_evidence': 'app/code.py:2', 'verification_evidence': 'Missing focused check',
            'retry_eligible': True, 'next_step': 'Add the bounded missing check',
            'retry_conditions': {'confidence': .89, 'evidence_sufficient': True, 'intended_behavior_clear': True,
                'localized_and_bounded': True, 'deterministically_verifiable': True, 'protected_boundary': False}}
        value = {'summary': 'Check', 'candidate_assessable': True, 'resolutions': [resolution],
                 'introduced_findings': [], 'scope_violations': [], 'coverage_gaps': []}
        with self.assertRaises(Exception):
            mod.fix_review_contract(value, [self.report['findings'][0]], self.head)
        resolution['retry_conditions']['confidence'] = .95
        resolution['retry_conditions']['protected_boundary'] = True
        with self.assertRaises(Exception):
            mod.fix_review_contract(value, [self.report['findings'][0]], self.head)

    def test_baseline_failure_dispatches_no_agents(self):
        with patch.object(mod, '_run_verification', return_value={'passed': False, 'commands': []}):
            result = self.run_workflow()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertFalse(self.calls)

    def test_source_export_tampering_is_rejected(self):
        (self.review_dir / 'workspace/source/head/app/code.py').write_text('def first(xs,n):\n    return []\n')
        result = self.run_workflow()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertFalse(self.calls)

    def test_validated_coverage_gaps_cannot_be_dropped(self):
        self.assertTrue(self.snapshot['coverage_gaps'])
        _write_json(self.review_dir / 'code-review.json', {**self.report, 'coverage_gaps': [], 'coverage_status': 'COMPLETE'})
        result = self.run_workflow()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertFalse(self.calls)

    def test_reviewed_diff_tampering_is_rejected(self):
        (self.review_dir / 'workspace/diff.patch').write_text('A different diff\n')
        result = self.run_workflow()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertFalse(self.calls)

    def test_remote_advance_rejects_push_without_posting(self):
        self.prepare_github()
        self.metadata['head']['sha'] = 'a' * 40
        with patch.object(publisher, 'gh', side_effect=self.github), self.assertRaises(Exception):
            publisher.publish(self.records, push=True)
        self.assertFalse(self.posts)


if __name__ == '__main__':
    unittest.main()
