"""Workflow 3 contracts, routing, isolation and orchestration regression tests."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
stub = types.ModuleType('cao_workflow')
stub.emit_output = lambda value: None
stub.get_inputs = lambda: {}
stub.step = lambda *args, **kwargs: None
sys.modules.setdefault('cao_workflow', stub)
# Exercise the exact standalone artifact installed into CAO, not a second
# compatibility implementation of the extracted helpers.
sys.path.insert(0, str(ROOT / ".agentic-sdlc" / "cao"))
from build_workflow import build_source
if os.environ.get("SDLC_TEST_SOURCE") == "1":
    mod = importlib.import_module("sdlc_workflows.source_review")
else:
    mod = types.ModuleType("source_review_bundle")
    exec(compile(build_source("source_review"), "<bundled:source_review>", "exec"), mod.__dict__)
execution_error = importlib.import_module("sdlc_workflows.errors").IncompleteAgentExecutionError if os.environ.get("SDLC_TEST_SOURCE") == "1" else mod.IncompleteAgentExecutionError
spec2 = importlib.util.spec_from_file_location('publish_source_review', ROOT / '.agentic-sdlc/scripts/publish_source_review.py')
publisher = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(publisher)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'
        for side in ('base', 'head'):
            folder = self.work / 'source' / side
            folder.mkdir(parents=True)
            (folder / 'code.py').write_text('def count(x):\n    return x + 1\n')
        self.snapshot = {'repository': 'owner/repo', 'pr_number': '1', 'pr_url': 'https://github.com/owner/repo/pull/1',
                         'base_sha': 'a' * 40, 'head_sha': 'b' * 40, 'merge_base_sha': 'a' * 40,
                         'changed_files': ['code.py'], 'coverage_gaps': []}
        self.mapping = {'summary': 'One changed function', 'files': [{'file': 'code.py', 'scope': 'SOURCE',
                        'reason': 'Python code', 'related_files': [], 'sensitive_boundaries': []}], 'coverage_gaps': []}
        self.finding = {**mod.FINDING_SHAPE, 'file': 'code.py', 'line_start': 2, 'line_end': 2,
                        'candidate_id': 'C1', 'symbol': 'count'}

    def test_all_conditions_allow_automatic_route(self):
        finding = mod.route_finding(self.finding, self.snapshot, self.mapping)
        self.assertEqual(finding['route'], 'AUTO_FIX')
        self.assertEqual(finding['reviewed_head_sha'], 'b' * 40)

    def test_each_failed_condition_forces_human(self):
        cases = [{'severity': 'HIGH'}, {'confidence': .89}, {'protected_boundary': True}]
        cases += [{flag: False} for flag in mod.ELIGIBILITY]
        cases += [{'category': category} for category in mod.PROTECTED]
        for change in cases:
            with self.subTest(change=change):
                result = mod.route_finding({**self.finding, **change, 'route': 'AUTO_FIX'}, self.snapshot, self.mapping)
                self.assertEqual(result['route'], 'HUMAN_REQUIRED')
                self.assertTrue(result['routing_reasons'])

    def test_mapper_sensitive_boundary_overrides_reviewer(self):
        self.mapping['files'][0]['sensitive_boundaries'] = ['AUTHN_AUTHZ']
        self.assertEqual(mod.route_finding(self.finding, self.snapshot, self.mapping)['route'], 'HUMAN_REQUIRED')

    def test_ids_survive_line_movement_and_new_head(self):
        before = mod.route_finding(self.finding, self.snapshot, self.mapping)['stable_id']
        after = mod.route_finding({**self.finding, 'line_start': 20}, {**self.snapshot, 'head_sha': 'c'*40}, self.mapping)['stable_id']
        self.assertEqual(before, after)

    def test_finding_contract_rejects_invalid_and_out_of_scope_data(self):
        cases = [{'confidence': True}, {'confidence': float('nan')}, {'localized_and_bounded': 'true'},
                 {'file': '../outside.py'}, {'line_start': 0}, {'line_end': 3}, {'category': 'STYLE'},
                 {'introduced_by_pr': False}, {'file': 'missing.py'}, {'verification_method': ''}]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(mod.WorkflowContractError):
                mod.validate_finding({**self.finding, **change}, self.work, self.snapshot, self.mapping)

    def test_mapping_must_account_for_every_file(self):
        with self.assertRaises(mod.WorkflowContractError):
            mod.mapping_contract({**self.mapping, 'files': []}, self.snapshot)

    def test_validator_cannot_drop_candidates_or_invent_duplicates(self):
        for decisions in ([], [{'candidate_id': 'C1', 'disposition': 'DUPLICATE', 'reason': 'same', 'duplicate_of': 'missing'}]):
            with self.assertRaises(mod.WorkflowContractError):
                mod.adjudication_contract({'decisions': decisions, 'coverage_gaps': []}, [self.finding], self.work, self.snapshot, self.mapping)

    def test_validator_rejects_unsubstantiated_acceptance(self):
        decision = {'candidate_id': 'C1', 'disposition': 'ACCEPT', 'reason': 'unsure',
                    'finding': {**self.finding, 'evidence_sufficient': False}}
        with self.assertRaises(mod.WorkflowContractError):
            mod.adjudication_contract({'decisions': [decision], 'coverage_gaps': []}, [self.finding], self.work, self.snapshot, self.mapping)

    def test_guard_blocks_source_and_symlink_escape(self):
        mod.configure_workspace(self.work)
        guard = self.work / '.claude/hooks/source-review-write-guard.py'
        allowed = self.work / '.agentic-sdlc/runtime'
        (allowed / 'escape').symlink_to(self.root, target_is_directory=True)
        for name, denied in [(str(allowed / 'answer.json'), False), ('source/head/code.py', True),
                             (str(allowed / 'escape/leak'), True), (str(self.root / 'outside'), True)]:
            with self.subTest(path=name):
                result = subprocess.run([sys.executable, str(guard)], cwd=self.work,
                    input=json.dumps({'tool_input': {'file_path': name}}), text=True, capture_output=True, check=True)
                self.assertEqual(bool(result.stdout), denied)

    def fake_agent(self, **kwargs):
        role = kwargs['agent'].removeprefix('sdlc_source_')
        if role == 'mapper':
            value = copy.deepcopy(self.mapping)
        elif role == 'correctness':
            value = {'findings': [copy.deepcopy(self.finding)], 'coverage_gaps': []}
        elif role == 'security':
            value = {'findings': [], 'coverage_gaps': []}
        elif role == 'validator':
            value = {'decisions': [{'candidate_id': 'correctness:C1', 'disposition': 'ACCEPT',
                       'reason': 'Confirmed in source', 'finding': {**self.finding, 'candidate_id': 'correctness:C1'}}], 'coverage_gaps': []}
        else:
            routed = json.loads((self.work / 'routed-findings.json').read_text())
            value = {'summary': 'One defect', 'comments': [{'stable_id': f['stable_id'], 'explanation': 'Correct the count.'} for f in routed['findings']]}
        return kwargs['validator'](value)

    def test_five_agent_pipeline_and_feedback(self):
        with patch.object(mod, '_run_json_contract_step', side_effect=self.fake_agent) as run:
            report = mod.run_agents(self.work, self.snapshot)
        self.assertEqual(run.call_count, 5)
        self.assertEqual(report['coverage_status'], 'COMPLETE')
        self.assertEqual(len(report['queues']['AUTO_FIX']), 1)
        report['status'] = 'REVIEWED'
        comments = mod.render_comments(report)
        self.assertIn('AUTO_FIX', comments)
        self.assertIn('/blob/' + 'b'*40, comments)
        self.assertIn('Suggested verification:', comments)

    def test_gap_survives_empty_findings(self):
        def fake(**kwargs):
            role = kwargs['agent'].removeprefix('sdlc_source_')
            value = self.mapping if role == 'mapper' else {'findings': [], 'coverage_gaps': []}
            if role == 'security':
                value['coverage_gaps'] = ['caller unavailable']
            if role == 'validator':
                value = {'decisions': [], 'coverage_gaps': [{'gap': 'caller unavailable', 'covers': ['G1']}]}
            if role == 'feedback':
                value = {'summary': 'Incomplete', 'comments': []}
            return kwargs['validator'](value)
        with patch.object(mod, '_run_json_contract_step', side_effect=fake):
            report = mod.run_agents(self.work, self.snapshot)
        self.assertEqual(report['coverage_status'], 'INCOMPLETE')
        self.assertEqual(report['coverage_gaps'], ['caller unavailable'])

    def test_validator_merges_reworded_gaps_and_keeps_snapshot_gaps(self):
        snapshot = {**self.snapshot, 'coverage_gaps': ['Agent control file excluded: .claude/settings.json']}
        reported = {'mapper': ['Host HTTP layer is outside the repository'],
                    'correctness': ['Caller of handle() is not in the snapshot'],
                    'security': ['.claude/settings.json was excluded and not reviewed']}
        def fake(**kwargs):
            role = kwargs['agent'].removeprefix('sdlc_source_')
            if role == 'mapper':
                value = {**copy.deepcopy(self.mapping), 'coverage_gaps': reported['mapper']}
            elif role in ('correctness', 'security'):
                value = {'findings': [], 'coverage_gaps': reported[role]}
            elif role == 'validator':
                gaps = json.loads((self.work / 'reported-gaps.json').read_text())
                self.assertEqual([(g['id'], g['source']) for g in gaps],
                                 [('G1', 'mapper'), ('G2', 'correctness'), ('G3', 'security')])
                value = {'decisions': [], 'snapshot_restatements': ['G3'],
                         'coverage_gaps': [{'gap': 'The HTTP caller of handle() is outside the repository', 'covers': ['G1', 'G2']}]}
            else:
                value = {'summary': 'Incomplete', 'comments': []}
            return kwargs['validator'](value)
        with patch.object(mod, '_run_json_contract_step', side_effect=fake):
            report = mod.run_agents(self.work, snapshot)
        self.assertEqual(report['coverage_gaps'], ['Agent control file excluded: .claude/settings.json',
                                                   'The HTTP caller of handle() is outside the repository'])

    def test_validator_must_account_for_every_reported_gap_once(self):
        reported = [{'id': 'G1', 'source': 'mapper', 'gap': 'a'}, {'id': 'G2', 'source': 'security', 'gap': 'b'}]
        for value in ({'coverage_gaps': [{'gap': 'a', 'covers': ['G1']}]},
                      {'coverage_gaps': [{'gap': 'a', 'covers': ['G1', 'G2']}, {'gap': 'b', 'covers': ['G2']}]},
                      {'coverage_gaps': [{'gap': 'a', 'covers': ['G1', 'G9']}, {'gap': 'b', 'covers': ['G2']}]},
                      {'coverage_gaps': ['a', 'b']},
                      {'coverage_gaps': [{'gap': 'a', 'covers': ['G1']}], 'snapshot_restatements': ['G2']}):
            with self.subTest(value=value), self.assertRaises(mod.WorkflowContractError):
                mod.adjudication_contract({'decisions': [], **value}, [], self.work, self.snapshot, self.mapping, reported)
        merged = {'decisions': [], 'coverage_gaps': [{'gap': 'a and b', 'covers': ['G1', 'G2']}, {'gap': 'new', 'covers': []}]}
        self.assertIs(mod.adjudication_contract(merged, [], self.work, self.snapshot, self.mapping, reported), merged)

    def write_report(self):
        report = {'status': 'REVIEWED', 'snapshot': self.snapshot,
                  'comments_sha256': mod.hashlib.sha256(b'Review\n').hexdigest()}
        (self.root / 'code-review.json').write_text(json.dumps(report))
        (self.root / 'comments.md').write_text('Review\n')

    def test_publication_refuses_stale_head_and_tampered_comments(self):
        self.write_report()
        with patch.object(publisher, 'gh', return_value={'state': 'open', 'head': {'sha': 'c'*40}, 'base': {'sha': 'a'*40}}) as api:
            with self.assertRaisesRegex(ValueError, 'changed'):
                publisher.publish(self.root)
            self.assertEqual(api.call_count, 1)
        (self.root / 'comments.md').write_text('Tampered')
        with self.assertRaisesRegex(ValueError, 'differs'):
            publisher.build_payload(self.root)

    def test_publication_deduplicates_existing_review(self):
        self.write_report()
        _, payload, marker = publisher.build_payload(self.root)
        existing = {'id': 12, 'body': marker, 'commit_id': 'b'*40}
        metadata = {'state': 'open', 'head': {'sha': 'b'*40}, 'base': {'sha': 'a'*40}}
        with patch.object(publisher, 'gh', side_effect=[metadata, [[existing]]]) as api:
            self.assertEqual(publisher.publish(self.root)['id'], 12)
            self.assertEqual(api.call_count, 2)
        self.assertEqual(payload['event'], 'COMMENT')

    def test_publication_posts_only_comment_bound_to_head(self):
        self.write_report()
        metadata = {'state': 'open', 'head': {'sha': 'b'*40}, 'base': {'sha': 'a'*40}}
        with patch.object(publisher, 'gh', side_effect=[metadata, [[]], {'id': 13}]) as api:
            publisher.publish(self.root)
            payload = api.call_args.kwargs['payload']
            self.assertEqual(payload['event'], 'COMMENT')
            self.assertEqual(payload['commit_id'], 'b'*40)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        (self.repo / 'code.py').write_text('def count(x):\n    return x\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        self.base = self.git('rev-parse', 'HEAD')
        (self.repo / 'code.py').write_text('def count(x):\n    return x + 1\n')
        (self.repo / 'link.py').symlink_to('/etc/passwd')
        (self.repo / '.claude').mkdir()
        (self.repo / '.claude/settings.json').write_text('{"malicious":"settings"}')
        (self.repo / 'CLAUDE.local.md').write_text('Untrusted PR instructions')
        self.git('add', '.')
        self.git('commit', '-qm', 'head')
        self.head = self.git('rev-parse', 'HEAD')
        self.inputs = {'repository_root': str(self.root), 'source_repository': str(self.repo),
                       'base_sha': self.base, 'head_sha': self.head}

    def git(self, *args):
        return mod.git(self.repo, *args).decode().strip()

    def test_snapshot_ignores_dirty_tree_and_preserves_branch(self):
        branch = self.git('symbolic-ref', 'HEAD')
        (self.repo / 'code.py').write_text('uncommitted concurrent work\n')
        run = self.root / 'run'
        run.mkdir()
        snapshot = mod.prepare_snapshot(self.inputs, run)
        self.assertEqual(snapshot['head_sha'], self.head)
        self.assertIn('x + 1', (run / 'workspace/source/head/code.py').read_text())
        self.assertEqual((self.repo / 'code.py').read_text(), 'uncommitted concurrent work\n')
        self.assertEqual(self.git('symbolic-ref', 'HEAD'), branch)
        self.assertFalse((run / 'workspace/source/head/link.py').exists())
        self.assertFalse((run / 'workspace/source/head/.claude').exists())
        self.assertFalse((run / 'workspace/source/head/CLAUDE.local.md').exists())
        self.assertTrue(snapshot['coverage_gaps'])

    def test_github_fetches_pinned_head_without_touching_source_refs(self):
        self.git('update-ref', 'refs/pull/7/head', self.head)
        original_git = mod.git
        def redirected_git(repo, *args):
            args = tuple(str(self.repo) if a == 'https://github.com/owner/repo.git' else a for a in args)
            return original_git(repo, *args)
        run = self.root / 'github-run'
        run.mkdir()
        metadata = {'state': 'open', 'base': {'sha': self.base}, 'head': {'sha': self.head}}
        with patch.object(mod, 'github', return_value=metadata), patch.object(mod, 'git', side_effect=redirected_git):
            snapshot = mod.prepare_snapshot({'pr_url': 'https://github.com/owner/repo/pull/7'}, run)
        self.assertEqual(snapshot['head_sha'], self.head)
        self.assertEqual(snapshot['repository'], 'owner/repo')
        self.assertIn('x + 1', (run / 'workspace/source/head/code.py').read_text())

    def test_github_head_movement_during_fetch_is_rejected(self):
        self.git('update-ref', 'refs/pull/7/head', self.base)
        original_git = mod.git
        def redirected_git(repo, *args):
            return original_git(repo, *(str(self.repo) if a == 'https://github.com/owner/repo.git' else a for a in args))
        run = self.root / 'stale-run'
        run.mkdir()
        metadata = {'state': 'open', 'base': {'sha': self.base}, 'head': {'sha': self.head}}
        with patch.object(mod, 'github', return_value=metadata), patch.object(mod, 'git', side_effect=redirected_git):
            with self.assertRaisesRegex(mod.WorkflowContractError, 'moved'):
                mod.prepare_snapshot({'pr_url': 'https://github.com/owner/repo/pull/7'}, run)

    def test_diverged_base_uses_merge_base(self):
        self.git('checkout', '-q', '-b', 'base-drift', self.base)
        (self.repo / 'base-only.py').write_text('unrelated = 1\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Unrelated base work')
        base_tip = self.git('rev-parse', 'HEAD')
        run = self.root / 'diverged-run'
        run.mkdir()
        snapshot = mod.prepare_snapshot({**self.inputs, 'base_sha': base_tip}, run)
        self.assertEqual(snapshot['merge_base_sha'], self.base)
        self.assertNotIn('base-only.py', snapshot['changed_files'])

    def test_success_persists_exact_comment_hash_and_run_output(self):
        def review(work, snapshot):
            return {'snapshot': snapshot, 'summary': 'No confirmed defects', 'findings': [],
                    'coverage_gaps': ['Missing context\n'], 'coverage_status': 'INCOMPLETE',
                    'queues': {'AUTO_FIX': [], 'HUMAN_REQUIRED': []}}
        with patch.dict(os.environ, {'CAO_WORKFLOW_RUN_ID': 'success'}), patch.object(mod, 'get_inputs', return_value=self.inputs), patch.object(mod, 'run_agents', side_effect=review), patch.object(mod, 'emit_output') as emit:
            mod.main()
        run = self.root / '.agentic-sdlc/runtime/source-review/success'
        report = json.loads((run / 'code-review.json').read_text())
        self.assertEqual(report['status'], 'REVIEWED')
        self.assertEqual(report['comments_sha256'], mod.hashlib.sha256((run / 'comments.md').read_bytes()).hexdigest())
        self.assertEqual(emit.call_args.args[0]['coverage_status'], 'INCOMPLETE')

    def test_failed_agent_records_failure_not_clean_review(self):
        with patch.dict(os.environ, {'CAO_WORKFLOW_RUN_ID': 'test-failure'}), patch.object(mod, 'get_inputs', return_value=self.inputs), patch.object(mod, 'run_agents', side_effect=execution_error('missing answer')):
            with self.assertRaisesRegex(execution_error, 'missing answer'):
                mod.main()
        run = self.root / '.agentic-sdlc/runtime/source-review/test-failure'
        self.assertFalse((run / 'code-review.json').exists())
        self.assertEqual(json.loads((run / 'failure.json').read_text())['coverage_status'], 'INCOMPLETE')

    def test_reusing_run_id_never_overwrites_evidence(self):
        run = self.root / '.agentic-sdlc/runtime/source-review/existing'
        run.mkdir(parents=True)
        (run / 'marker').write_text('keep')
        with patch.dict(os.environ, {'CAO_WORKFLOW_RUN_ID': 'existing'}), patch.object(mod, 'get_inputs', return_value=self.inputs):
            with self.assertRaises(FileExistsError):
                mod.main()
        self.assertEqual((run / 'marker').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
