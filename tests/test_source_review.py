"""Source review contracts, routing, isolation and orchestration regression tests."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
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
mod = importlib.import_module("sdlc_workflows.source_review")
execution_error = importlib.import_module("sdlc_workflows.errors").IncompleteAgentExecutionError
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

    def test_correctness_and_security_run_at_the_same_time_and_merge_in_fixed_order(self):
        both_running = threading.Barrier(2, timeout=10)
        security_done = threading.Event()
        def fake(**kwargs):
            role = kwargs['agent'].removeprefix('sdlc_source_')
            if role in ('correctness', 'security'):
                both_running.wait()  # BrokenBarrierError unless the two run concurrently
                if role == 'correctness':
                    self.assertTrue(security_done.wait(10))  # finishes last, still merged first
                    value = {'findings': [copy.deepcopy(self.finding)], 'coverage_gaps': ['c gap']}
                else:
                    value = {'findings': [{**copy.deepcopy(self.finding), 'candidate_id': 'S1'}], 'coverage_gaps': ['s gap']}
                result = kwargs['validator'](value)
                if role == 'security':
                    security_done.set()
                return result
            if role == 'validator':
                self.assertEqual([c['candidate_id'] for c in json.loads((self.work / 'candidates.json').read_text())],
                                 ['correctness:C1', 'security:S1'])
                self.assertEqual([g['source'] for g in json.loads((self.work / 'reported-gaps.json').read_text())],
                                 ['correctness', 'security'])
                return kwargs['validator']({'decisions': [
                    {'candidate_id': 'correctness:C1', 'disposition': 'ACCEPT', 'reason': 'Confirmed',
                     'finding': {**self.finding, 'candidate_id': 'correctness:C1'}},
                    {'candidate_id': 'security:S1', 'disposition': 'DUPLICATE', 'reason': 'Same', 'duplicate_of': 'correctness:C1'}],
                    'coverage_gaps': [{'gap': 'merged', 'covers': ['G1', 'G2']}]})
            return self.fake_agent(**kwargs)
        with patch.object(mod, '_run_json_contract_step', side_effect=fake):
            report = mod.run_agents(self.work, self.snapshot, 4)
        self.assertEqual(report['review_max_parallel'], 4)
        self.assertEqual(len(report['findings']), 1)

    def test_review_max_parallel_one_keeps_the_original_order(self):
        order = []
        def fake(**kwargs):
            order.append((kwargs['step_id'], threading.current_thread() is threading.main_thread()))
            return self.fake_agent(**kwargs)
        with patch.object(mod, '_run_json_contract_step', side_effect=fake):
            report = mod.run_agents(self.work, self.snapshot, 1)
        self.assertEqual(order, [(s, True) for s in ('source-mapper', 'source-correctness', 'source-security',
                                                     'source-validator', 'source-feedback')])
        self.assertEqual(report['review_max_parallel'], 1)

    def test_a_failed_reviewer_fails_the_review_after_the_other_finishes(self):
        finished = []
        def fake(**kwargs):
            role = kwargs['agent'].removeprefix('sdlc_source_')
            if role == 'security':
                raise mod.WorkflowContractError('security answer broke the contract')
            result = self.fake_agent(**kwargs)
            finished.append(role)
            return result
        with patch.object(mod, '_run_json_contract_step', side_effect=fake):
            with self.assertRaisesRegex(mod.WorkflowContractError, 'security answer'):
                mod.run_agents(self.work, self.snapshot, 4)
        self.assertEqual(finished, ['mapper', 'correctness'])
        self.assertFalse((self.work / 'candidates.json').exists())

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


def git_in(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class PublicationTests(unittest.TestCase):
    """Draft rendering/parsing, placement and the single COMMENT review request."""

    PATCH = '@@ -1,2 +1,3 @@\n def count(x):\n-    return x\n+    y = x\n+    return y + 1\n@@ -10,3 +11,4 @@\n a\n+b\n c\n d\n'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = {'repository': 'owner/repo', 'pr_number': '7', 'pr_url': 'https://github.com/owner/repo/pull/7',
                         'base_sha': 'a' * 40, 'head_sha': 'b' * 40, 'merge_base_sha': 'a' * 40,
                         'changed_files': ['code.py'], 'coverage_gaps': []}
        mapping = {'files': [{'file': 'code.py', 'scope': 'SOURCE', 'sensitive_boundaries': [], 'related_files': []}]}
        hunks = {'code.py': mod.hunk_ranges(self.PATCH)}
        self.findings = []
        for index, (severity, lines, side) in enumerate([('LOW', (2, 3), 'head'), ('HIGH', (12, 12), 'head'),
                                                         ('MEDIUM', (20, 21), 'head')]):
            finding = {**mod.FINDING_SHAPE, 'candidate_id': f'C{index}', 'file': 'code.py', 'side': side,
                       'line_start': lines[0], 'line_end': lines[1], 'severity': severity, 'confidence': 0.95,
                       'title': f'Defect {index}', 'failure_scenario': f'Scenario {index}', 'symbol': 'count',
                       'comment': f'Explanation {index}'}
            routed = mod.route_finding(finding, self.snapshot, mapping)
            routed['placement'] = mod.place_finding(routed, hunks)
            self.findings.append(routed)
        self.report = {'status': 'REVIEWED', 'run_id': 'review-run-1', 'snapshot': self.snapshot, 'summary': 'Three defects.',
                       'findings': self.findings, 'coverage_gaps': ['Caller outside the snapshot'],
                       'coverage_status': 'INCOMPLETE',
                       'queues': {r: [f['stable_id'] for f in self.findings if f['route'] == r] for r in ('AUTO_FIX', 'HUMAN_REQUIRED')}}
        self.report['comments_sha256'] = mod.hashlib.sha256(b'Review\n').hexdigest()
        self.draft = mod.render_draft(self.report)
        self.report['draft_sha256'] = mod.hashlib.sha256(self.draft.encode()).hexdigest()
        (self.root / 'code-review.json').write_text(json.dumps(self.report))
        (self.root / 'comments.md').write_text('Review\n')
        (self.root / 'review-draft.md').write_text(self.draft)
        self.metadata = {'state': 'open', 'head': {'sha': 'b' * 40}, 'base': {'sha': 'a' * 40}}
        self.files = [{'filename': 'code.py', 'status': 'modified', 'patch': self.PATCH}]

    def plan(self, draft=None, metadata=None, files=None, **kwargs):
        return publisher.plan_review(self.root, self.report, draft if draft is not None else self.draft,
                                     metadata or self.metadata, files if files is not None else self.files, **kwargs)

    def test_hunk_ranges_match_github_rules_and_parity(self):
        corpus = [self.PATCH, '@@ -0,0 +1,2 @@\n+a\n+b\n', '@@ -1,2 +0,0 @@\n-a\n-b\n', '@@ -3 +3 @@\n-a\n+b\n\\ No newline at end of file\n']
        for patch_text in corpus:
            self.assertEqual(mod.hunk_ranges(patch_text), publisher.hunk_ranges(patch_text))
        self.assertEqual(mod.hunk_ranges(self.PATCH), {'LEFT': [(1, 2), (10, 12)], 'RIGHT': [(1, 3), (11, 14)]})
        diff = ('diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+a\n+b\n'
                'diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n'
                'diff --git "a/odd\\tname" "b/odd\\tname"\n--- "a/odd\\tname"\n+++ "b/odd\\tname"\n@@ -1 +1 @@\n-a\n+b\n')
        self.assertEqual(mod.diff_hunks(diff), {'new.py': {'LEFT': [], 'RIGHT': [(1, 2)]},
                                                'gone.py': {'LEFT': [(1, 2)], 'RIGHT': []}})

    def test_placement_requires_one_hunk_on_the_findings_side(self):
        hunks = {'code.py': mod.hunk_ranges(self.PATCH)}
        cases = [((2, 3), 'head', 'INLINE'), ((3, 12), 'head', 'GENERAL'), ((10, 10), 'base', 'INLINE'),
                 ((4, 4), 'head', 'GENERAL'), ((13, 14), 'head', 'INLINE')]
        for (start, end), side, mode in cases:
            with self.subTest(lines=(start, end), side=side):
                self.assertEqual(mod.place_finding({'file': 'code.py', 'side': side, 'line_start': start, 'line_end': end}, hunks)['mode'], mode)
        self.assertEqual(mod.place_finding({'file': 'other.py', 'side': 'head', 'line_start': 1, 'line_end': 1}, hunks)['mode'], 'GENERAL')

    def test_draft_orders_by_attention_and_round_trips(self):
        high = self.findings[1]
        self.assertEqual(high['route'], 'HUMAN_REQUIRED')
        self.assertLess(self.draft.index(high['stable_id']), self.draft.index(self.findings[0]['stable_id']))
        self.assertIn('## Needs your decision (1)', self.draft)
        self.assertIn('## Placed in the general comment, not beside the code (1)', self.draft)
        parsed = publisher.parse_draft(self.draft, self.report)
        self.assertIn('Three defects.', parsed['general'])
        by_id = {f['id']: f for f in parsed['findings']}
        for finding in self.findings:
            self.assertEqual(by_id[finding['stable_id']]['body'], mod.draft_comment(finding))
            self.assertTrue(by_id[finding['stable_id']]['publish'])
        self.assertIsNone(by_id[self.findings[2]['stable_id']]['anchor'])

    def test_unedited_draft_builds_one_comment_review(self):
        plan = self.plan()
        request = plan['request']
        self.assertEqual((request['event'], request['commit_id']), ('COMMENT', 'b' * 40))
        self.assertEqual(sorted((c['line'], c.get('start_line')) for c in request['comments']), [(3, 2), (12, None)])
        self.assertTrue(all(c['side'] == 'RIGHT' and c['path'] == 'code.py' for c in request['comments']))
        self.assertIn('## Findings not placed beside the code', request['body'])
        self.assertIn('Caller outside the snapshot', request['body'])
        self.assertTrue(request['body'].endswith(publisher.marker(self.report)))
        self.assertIn('review-run-1', publisher.marker(self.report))
        self.assertFalse(plan['draft_edited'])
        for text in ('AUTO_FIX', 'HUMAN_REQUIRED'):
            self.assertNotIn(text, request['comments'][0]['body'].split('<details>')[0])

    def test_reviewer_edits_are_published_and_recorded(self):
        low, high, medium = (f['stable_id'] for f in self.findings)
        draft = self.draft.replace('Explanation 1', 'Reworded by the reviewer')
        draft = draft.replace(f'id="{low}" publish="yes"', f'id="{low}" publish="no"').replace(
            f'id="{low}" publish="no" anchor="code.py:RIGHT:2-3" reason=""', f'id="{low}" publish="no" anchor="code.py:RIGHT:2-3" reason="false positive"')
        draft = draft.replace(f'id="{high}" publish="yes" anchor="code.py:RIGHT:12-12"', f'id="{high}" publish="yes" anchor="code.py:RIGHT:13-14"')
        draft = draft.replace('Three defects.', 'Three defects. Reviewer note: please add tests.')
        plan = self.plan(draft)
        self.assertTrue(plan['draft_edited'])
        self.assertEqual(plan['omitted'], [{'id': low, 'reason': 'false positive'}])
        [comment] = plan['request']['comments']
        self.assertEqual((comment['start_line'], comment['line']), (13, 14))
        self.assertIn('Reworded by the reviewer', comment['body'])
        self.assertIn('Reviewer note', plan['request']['body'])
        self.assertNotIn('Explanation 0', json.dumps(plan['request']))

    def test_anchor_outside_current_diff_moves_to_general_not_failure(self):
        high = self.findings[1]['stable_id']
        draft = self.draft.replace(f'anchor="code.py:RIGHT:12-12"', 'anchor="code.py:RIGHT:5-6"')
        plan = self.plan(draft)
        self.assertEqual(len(plan['request']['comments']), 1)
        self.assertIn({'id': high, 'outcome': 'GENERAL', 'reason': 'lines 5-6 are not within one changed section of the current diff'}, plan['decisions'])
        without_patch = self.plan(files=[{'filename': 'code.py', 'status': 'modified'}])
        self.assertEqual(without_patch['request']['comments'], [])

    def test_draft_errors_are_line_numbered(self):
        high = self.findings[1]['stable_id']
        broken = {
            'header': self.draft.replace('head="' + 'b' * 40, 'head="' + 'c' * 40),
            'unknown id': self.draft.replace(f'id="{high}"', 'id="SR-0000000000000000"'),
            'removed block': self.draft[:self.draft.index(f'<!-- finding id="{high}"')] + self.draft[self.draft.index('<!-- end finding -->', self.draft.index(high)) + 21:],
            'bad publish': self.draft.replace(f'id="{high}" publish="yes"', f'id="{high}" publish="maybe"'),
            'bad anchor': self.draft.replace('anchor="code.py:RIGHT:12-12"', 'anchor="code.py:12"'),
            'unclosed': self.draft.rstrip().removesuffix('<!-- end finding -->'),
            'empty body': self.draft.replace(mod.draft_comment(self.findings[1]), ''),
            'extra attribute': self.draft.replace(f'id="{high}"', f'id="{high}" route="AUTO_FIX"'),
            'no general': self.draft.replace('<!-- general -->', '').replace('<!-- end general -->', ''),
        }
        for name, text in broken.items():
            with self.subTest(case=name), self.assertRaises(publisher.DraftError):
                publisher.parse_draft(text, self.report)

    def test_closed_pr_tampered_audit_and_missing_draft_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.plan(metadata={**self.metadata, 'state': 'closed'})
        (self.root / 'comments.md').write_text('Tampered\n')
        with self.assertRaisesRegex(ValueError, 'differs'):
            publisher.load(self.root)
        (self.root / 'comments.md').write_text('Review\n')
        (self.root / 'review-draft.md').unlink()
        with self.assertRaisesRegex(ValueError, 'review-draft.md'):
            publisher.load(self.root)

    def test_gh_allows_only_reads_and_comment_reviews(self):
        with patch.object(publisher.subprocess, 'run') as run:
            for endpoint, payload in [('repos/o/r/pulls/7/comments', {'event': 'COMMENT'}),
                                      ('repos/o/r/pulls/7/reviews', {'event': 'APPROVE'}),
                                      ('repos/o/r/pulls/7/reviews', {'event': 'REQUEST_CHANGES'}),
                                      ('repos/o/r/pulls/7/reviews', {'body': 'pending review'}),
                                      ('repos/o/r/pulls/7/reviews/9/events', {'event': 'COMMENT'}),
                                      ('repos/o/r/pulls/7/merge', {'event': 'COMMENT'})]:
                with self.subTest(endpoint=endpoint, payload=payload), self.assertRaises(ValueError):
                    publisher.gh(endpoint, payload=payload)
            run.assert_not_called()

    def fake_github(self, reviews, calls):
        stored = [{'id': 300, 'pull_request_review_id': 5, 'path': 'other.py', 'body': 'someone else'},
                  {'id': 301, 'pull_request_review_id': 21, 'path': 'code.py', 'side': 'RIGHT', 'start_line': 2, 'line': 3,
                   'commit_id': 'b' * 40, 'html_url': 'https://github.com/owner/repo/pull/7#discussion_r301', 'body': 'Explanation'}]

        def fake(endpoint, payload=None, paginate=False):
            calls.append((endpoint, payload))
            if endpoint.endswith('/files?per_page=100'):
                return [self.files]
            if endpoint.endswith('/reviews?per_page=100'):
                return [reviews]
            if endpoint.endswith('/pulls/7/comments?per_page=100'):
                return [stored]
            if payload is not None:
                reviews.append({'id': 21, 'body': payload['body']})
                return {'id': 21, 'body': payload['body'], 'state': 'COMMENTED'}
            return self.metadata
        return fake

    def test_publish_posts_once_records_request_and_reuses_on_retry(self):
        calls = []
        with patch.object(publisher, 'gh', side_effect=self.fake_github([{'id': 5, 'body': 'someone else'}], calls)):
            first = publisher.publish(self.root)
            posted = next(p for _, p in calls if p is not None)
            second = publisher.publish(self.root)
        self.assertEqual(sum(p is not None for _, p in calls), 1)
        self.assertEqual(posted['event'], 'COMMENT')
        self.assertEqual(first['request'], posted)
        self.assertEqual([c['id'] for c in first['comments']], [301])
        self.assertTrue(second['reused_existing'])
        self.assertEqual((second['request'], second['decisions']), (posted, first['decisions']))
        receipt = json.loads((self.root / 'publication.json').read_text())
        self.assertEqual((receipt['review']['id'], receipt['comments'][0]['html_url'][-4:]), (21, 'r301'))
        self.assertFalse((self.root / 'publication.lock').exists())

    def test_receipt_maps_finding_by_appended_marker_not_editable_prose(self):
        fake = self.fake_github([], [])
        low, high = self.findings[0]['stable_id'], self.findings[1]['stable_id']
        def github(endpoint, payload=None, paginate=False):
            result = fake(endpoint, payload=payload, paginate=paginate)
            if endpoint.endswith('/pulls/7/comments?per_page=100'):
                result[0][1]['body'] = ('Edited prose with a quoted marker: ' + publisher.finding_comment_marker(self.report, high)
                    + '\n\n' + publisher.finding_comment_marker(self.report, low))
            return result
        with patch.object(publisher, 'gh', side_effect=github):
            receipt = publisher.publish(self.root)
        self.assertEqual(set(receipt['finding_comments']), {low})
        self.assertEqual(receipt['finding_comments'][low]['id'], 301)

    def test_new_run_on_same_pr_state_posts_its_own_review(self):
        other_run = {**self.report, 'run_id': 'review-run-0'}
        existing = [{'id': 20, 'body': 'older run ' + publisher.marker(other_run)}]
        with patch.object(publisher, 'gh', side_effect=self.fake_github(existing, calls := [])):
            receipt = publisher.publish(self.root)
        self.assertFalse(receipt['reused_existing'])
        self.assertEqual(sum(p is not None for _, p in calls), 1)

    def test_edits_after_posting_are_reported_not_published(self):
        with patch.object(publisher, 'gh', side_effect=self.fake_github([], calls := [])):
            publisher.publish(self.root)
            (self.root / 'review-draft.md').write_text(self.draft.replace('Explanation 1', 'Too late'))
            again = publisher.publish(self.root)
        self.assertTrue(again['reused_existing'])
        self.assertTrue(again['unpublished_draft_edits'])
        self.assertEqual(sum(p is not None for _, p in calls), 1)
        self.assertNotIn('Too late', json.dumps(again['request']))

    def test_stale_run_is_publishable_but_failed_or_unknown_is_not(self):
        for status, allowed in (('STALE', True), ('FAILED', False), (None, False)):
            report = {**self.report, 'status': status}
            (self.root / 'code-review.json').write_text(json.dumps(report))
            with self.subTest(status=status):
                if allowed:
                    self.assertEqual(publisher.load(self.root)[0]['status'], 'STALE')
                else:
                    with self.assertRaisesRegex(ValueError, 'REVIEWED or STALE'):
                        publisher.load(self.root)

    def test_cli_reports_errors_without_tracebacks(self):
        (self.root / 'publication.lock').write_text('')
        for argv, side_effect, expected in (
                (['--publish'], None, 'publication.lock exists'),
                ([], ValueError('GitHub request failed (x)'), 'Not published: GitHub request failed'),
                ([], publisher.DraftError('line 9: bad anchor'), 'review-draft.md: line 9: bad anchor')):
            with self.subTest(expected=expected), patch.object(sys, 'argv', ['publish', str(self.root), *argv]):
                with patch.object(publisher, 'prepare', side_effect=side_effect,
                                  return_value=(self.report, self.plan(), 'repos/owner/repo/pulls/7')):
                    with self.assertRaises(SystemExit) as raised:
                        publisher.main()
                self.assertIn(expected, str(raised.exception.code))

    def split_run(self):
        """Lay the run out as the workflow does: results in records, working files in runtime."""
        repo = self.root / 'host'
        results = repo / 'agentic-sdlc-records/source-review/repo-pr7/review-run-1'
        working = repo / '.agentic-sdlc/runtime/source-review/review-run-1'
        results.mkdir(parents=True)
        (working / 'workspace').mkdir(parents=True)
        for name in ('code-review.json', 'comments.md', 'review-draft.md'):
            (self.root / name).rename(results / name)
        return results, working

    def test_records_run_publishes_receipt_beside_results_and_locks_in_runtime(self):
        results, working = self.split_run()
        self.assertEqual(publisher.review_run_directories(results), (results, working))
        fake, locked = self.fake_github([], []), []
        def github(endpoint, payload=None, paginate=False):
            locked.append((working / 'publication.lock').exists())
            return fake(endpoint, payload=payload, paginate=paginate)
        with patch.object(publisher, 'gh', side_effect=github):
            receipt = publisher.publish(results)
        self.assertIn(True, locked)
        self.assertEqual(json.loads((results / 'publication.json').read_text())['review']['id'], receipt['review']['id'])
        self.assertEqual(sorted(p.name for p in working.iterdir()), ['workspace'])

    def test_records_run_refuses_working_directory_and_foreign_report(self):
        results, working = self.split_run()
        with self.assertRaisesRegex(ValueError, f'use its results directory {results}'):
            publisher.prepare(working)
        report = json.loads((results / 'code-review.json').read_text())
        (results / 'code-review.json').write_text(json.dumps({**report, 'run_id': 'other-run'}))
        with self.assertRaisesRegex(ValueError, 'belongs to run other-run'):
            publisher.load(*publisher.review_run_directories(results))
        (working / 'workspace').rename(working.parent / 'gone')
        working.rmdir()
        with self.assertRaisesRegex(ValueError, 'working directory .* is missing'):
            publisher.prepare(results)

    def test_reused_review_without_receipt_never_claims_todays_plan(self):
        existing = [{'id': 21, 'body': 'earlier ' + publisher.marker(self.report)}]
        with patch.object(publisher, 'gh', side_effect=self.fake_github(existing, calls := [])):
            receipt = publisher.publish(self.root)
        self.assertTrue(receipt['reused_existing'])
        self.assertIsNone(receipt['request'])
        self.assertNotIn('decisions', receipt)
        self.assertIn('read back from GitHub', receipt['note'])
        self.assertEqual([c['id'] for c in receipt['comments']], [301])
        self.assertFalse(any(p is not None for _, p in calls))


class CurrencyTests(unittest.TestCase):
    """Per-finding check against a moved PR head, using real Git commits."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'objects.git'
        self.repo.mkdir()
        git_in(self.repo, 'init', '-q')
        git_in(self.repo, 'config', 'user.name', 'Test')
        git_in(self.repo, 'config', 'user.email', 'test@example.com')
        self.lines = [f'line {i}' for i in range(1, 21)]
        self.old = self.commit({'code.py': self.lines, 'helper.py': ['h = 1']})

    def commit(self, files, remove=()):
        for name, lines in files.items():
            (self.repo / name).write_text('\n'.join(lines) + '\n')
        for name in remove:
            (self.repo / name).unlink()
        git_in(self.repo, 'add', '-A')
        git_in(self.repo, 'commit', '-qm', 'change')
        return git_in(self.repo, 'rev-parse', 'HEAD')

    def status(self, new, start=10, end=12):
        return publisher.map_range(self.repo, self.old, new, 'code.py', start, end)

    def test_unchanged_lines_follow_shifts(self):
        new = self.commit({'code.py': ['inserted a', 'inserted b'] + self.lines[:15] + self.lines[16:]})
        self.assertEqual(self.status(new), ('CURRENT', (12, 14)))

    def test_edited_or_split_range_is_changed(self):
        edited = self.commit({'code.py': self.lines[:10] + ['line 11 edited'] + self.lines[11:]})
        self.assertEqual(self.status(edited)[0], 'CHANGED')
        git_in(self.repo, 'checkout', '-q', self.old)
        split = self.commit({'code.py': self.lines[:10] + ['wedge'] + self.lines[10:]})
        self.assertEqual(self.status(split)[0], 'CHANGED')

    def test_deleted_file_is_gone(self):
        self.assertEqual(self.status(self.commit({}, remove=['code.py']))[0], 'GONE')

    def test_moved_pr_places_current_holds_changed_and_context(self):
        new = self.commit({'code.py': ['top'] + self.lines, 'helper.py': ['h = 2']})
        snapshot = {'repository': 'owner/repo', 'pr_number': '7', 'pr_url': 'x', 'base_sha': 'a' * 40,
                    'head_sha': self.old, 'merge_base_sha': 'a' * 40, 'changed_files': ['code.py'], 'coverage_gaps': []}
        mapping = {'files': [{'file': 'code.py', 'scope': 'SOURCE', 'sensitive_boundaries': [], 'related_files': ['helper.py']}]}
        (self.root / 'workspace').mkdir()
        (self.root / 'workspace' / 'mapping.json').write_text(json.dumps(mapping))
        finding = {**mod.FINDING_SHAPE, 'candidate_id': 'C1', 'file': 'code.py', 'side': 'head', 'line_start': 10,
                   'line_end': 12, 'severity': 'LOW', 'confidence': 0.95, 'comment': 'Explain'}
        routed = mod.route_finding(finding, snapshot, mapping)
        routed['placement'] = mod.place_finding(routed, {'code.py': {'LEFT': [], 'RIGHT': [(1, 21)]}})
        report = {'status': 'REVIEWED', 'run_id': 'run-1', 'snapshot': snapshot, 'summary': 'One', 'findings': [routed],
                  'coverage_gaps': [], 'coverage_status': 'COMPLETE', 'draft_sha256': ''}
        draft = mod.render_draft(report)
        metadata = {'state': 'open', 'head': {'sha': new}, 'base': {'sha': 'a' * 40}}
        files = [{'filename': 'code.py', 'status': 'modified', 'patch': '@@ -0,0 +1,21 @@\n'}]
        with patch.object(publisher, 'fetch_current', return_value='a' * 40):
            held = publisher.plan_review(self.root, report, draft, metadata, files)
            placed = publisher.plan_review(self.root, report, draft, metadata, files, include_context_changed=True)
        self.assertEqual(held['held'][0]['status'], 'CONTEXT_CHANGED')
        self.assertIn('Not published because the code changed', held['request']['body'])
        [comment] = placed['request']['comments']
        self.assertEqual((comment['start_line'], comment['line'], placed['request']['commit_id']), (11, 13, new))
        self.assertIn('these lines are unchanged', comment['body'])


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
        def review(work, snapshot, max_parallel, standards):
            self.assertEqual((max_parallel, standards), (4, None))
            return {'snapshot': snapshot, 'summary': 'No confirmed defects', 'findings': [],
                    'coverage_gaps': ['Missing context\n'], 'coverage_status': 'INCOMPLETE',
                    'queues': {'AUTO_FIX': [], 'HUMAN_REQUIRED': []}}
        with patch.dict(os.environ, {'CAO_WORKFLOW_RUN_ID': 'success'}), patch.object(mod, 'get_inputs', return_value=self.inputs), patch.object(mod, 'run_agents', side_effect=review), patch.object(mod, 'emit_output') as emit:
            mod.main()
        run = self.root / f'agentic-sdlc-records/source-review/local-{self.head[:12]}/success'
        working = self.root / '.agentic-sdlc/runtime/source-review/success'
        self.assertEqual(sorted(p.name for p in run.iterdir()), ['code-review.json', 'comments.md', 'review-draft.md'])
        self.assertEqual(sorted(p.name for p in working.iterdir()), ['objects.git', 'workspace'])
        self.assertEqual((emit.call_args.args[0]['results'], emit.call_args.args[0]['draft']),
                         (str(run), str(run / 'review-draft.md')))
        report = json.loads((run / 'code-review.json').read_text())
        self.assertEqual(report['status'], 'REVIEWED')
        self.assertEqual(report['comments_sha256'], mod.hashlib.sha256((run / 'comments.md').read_bytes()).hexdigest())
        self.assertEqual(emit.call_args.args[0]['coverage_status'], 'INCOMPLETE')
        self.assertEqual(report['draft_sha256'], mod.hashlib.sha256((run / 'review-draft.md').read_bytes()).hexdigest())
        self.assertEqual(emit.call_args.args[0]['attention'], {'human_required': 0, 'general_only': 0, 'coverage_gaps': 1})

    def test_an_invalid_review_max_parallel_stops_before_any_work(self):
        for value in (0, 5, True, '2', 1.5):
            with self.subTest(value=value), patch.dict(os.environ, {'CAO_WORKFLOW_RUN_ID': f'bad-{value}'}), \
                    patch.object(mod, 'get_inputs', return_value={**self.inputs, 'review_max_parallel': value}), \
                    patch.object(mod, 'run_agents') as run:
                with self.assertRaisesRegex(mod.WorkflowContractError, 'review_max_parallel'):
                    mod.main()
                run.assert_not_called()
        self.assertFalse((self.root / '.agentic-sdlc/runtime/source-review').exists())

    def test_failed_agent_records_failure_not_clean_review(self):
        with patch.dict(os.environ, {'CAO_WORKFLOW_RUN_ID': 'test-failure'}), patch.object(mod, 'get_inputs', return_value=self.inputs), patch.object(mod, 'run_agents', side_effect=execution_error('missing answer')):
            with self.assertRaisesRegex(execution_error, 'missing answer'):
                mod.main()
        run = self.root / '.agentic-sdlc/runtime/source-review/test-failure'
        self.assertFalse((run / 'code-review.json').exists())
        self.assertFalse((self.root / 'agentic-sdlc-records').exists())
        self.assertEqual(json.loads((run / 'failure.json').read_text())['coverage_status'], 'INCOMPLETE')

    def test_records_folder_names_repository_and_pr(self):
        name = mod.review_records_name
        self.assertEqual(name({'pr_url': 'u', 'repository': 'Owner/My_Payments.Service', 'pr_number': '42'}),
                         'my-payments-service-pr42')
        self.assertEqual(name({'pr_url': 'u', 'repository': 'o/' + 'a' * 29 + '_b' * 5, 'pr_number': '3'}),
                         'a' * 29 + '-pr3')
        self.assertEqual(name({'pr_url': 'u', 'repository': 'o/___', 'pr_number': '1'}), 'repo-pr1')
        self.assertEqual(name({'pr_url': None, 'head_sha': 'c' * 40}), 'local-' + 'c' * 12)
        with self.assertRaises(mod.WorkflowContractError):
            name({'pr_url': 'u', 'repository': 'o/r', 'pr_number': '../1'})

    def test_reusing_run_id_never_overwrites_evidence(self):
        run = self.root / '.agentic-sdlc/runtime/source-review/existing'
        run.mkdir(parents=True)
        (run / 'marker').write_text('keep')
        with patch.dict(os.environ, {'CAO_WORKFLOW_RUN_ID': 'existing'}), patch.object(mod, 'get_inputs', return_value=self.inputs):
            with self.assertRaises(FileExistsError):
                mod.main()
        self.assertEqual((run / 'marker').read_text(), 'keep')



RULE_TEXT = """# Rules

## {id} - [{level}] Readability. Rule {id}

### Applicable files

- `{glob}`

### Rule description

Text.

### Why we have the rule

Text.

### Examples: bad

Text.

### Examples: good

Text.
"""


class StandardsTests(unittest.TestCase):
    """Project rules in Source review: diff lines, the standards contract, routing by level and the pipeline."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name) / 'work'
        (self.work / 'source/base').mkdir(parents=True)
        (self.work / 'source/head').mkdir(parents=True)
        (self.work / 'source/base/code.py').write_text('def count(x):\n    return x\n')
        (self.work / 'source/head/code.py').write_text('def count(x):\n    return x + 1\n')
        (self.work / 'diff.patch').write_text('diff --git a/code.py b/code.py\n--- a/code.py\n+++ b/code.py\n'
                                              '@@ -1,2 +1,2 @@\n def count(x):\n-    return x\n+    return x + 1\n')
        self.snapshot = {'repository': None, 'pr_number': None, 'pr_url': None, 'base_sha': 'a' * 40,
                         'head_sha': 'b' * 40, 'merge_base_sha': 'a' * 40, 'changed_files': ['code.py'],
                         'coverage_gaps': []}
        self.mapping = {'summary': 'One changed function', 'files': [{'file': 'code.py', 'scope': 'SOURCE',
                        'reason': 'Python code', 'related_files': [], 'sensitive_boundaries': []}], 'coverage_gaps': []}
        rules = []
        for rule_id, level in (('A-1', 'MANDATORY'), ('A-2', 'MANDATORY'), ('B-1', 'NICE-TO-HAVE'), ('Z-1', 'MANDATORY')):
            glob = 'other/*.py' if rule_id == 'Z-1' else '*.py'
            rules += importlib.import_module('sdlc_workflows.review_knowledge').parse_rules(RULE_TEXT.format(id=rule_id, level=level, glob=glob), 'kb/rules.md', 8192)
        self.knowledge = {'files': [{'path': 'agentic-sdlc-pr-review-kb/rules.md', 'sha256': 'f' * 64}],
                          'rules': rules, 'settings': {'files': ['agentic-sdlc-pr-review-kb/rules.md'],
                          'batch_max_rules': 15, 'batch_max_kb': 32, 'batch_max_files': 5, 'max_files': 32, 'max_rules': 200, 'max_rule_kb': 8}}
        self.rule_finding = {**mod.STANDARDS_FINDING_SHAPE, 'candidate_id': 'N1', 'file': 'code.py',
                             'line_start': 2, 'line_end': 2, 'symbol': 'count', 'standard_ref': 'A-1',
                             'title': 'Abbreviated name'}

    def batch(self):
        return [{**rule, 'covers': ['code.py']} for rule in self.knowledge['rules'] if rule['id'] != 'Z-1']

    def test_added_lines_count_only_lines_the_diff_adds(self):
        diff = ('diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,4 +1,5 @@\n keep\n-old\n+new\n keep\n+added\n keep\n'
                '@@ -10,2 +11,2 @@\n ctx\n+late\n'
                'diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n'
                'diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+--- looks like a header\n+b\n')
        self.assertEqual(mod.added_lines(diff), {'a.py': {2, 4, 12}, 'new.py': {1, 2}})

    def test_standards_contract_accepts_a_complete_answer(self):
        value = {'rules': [verdict('A-1', 'VIOLATED', ['N1']),
                           verdict('A-2', 'NOT_VIOLATED', []),
                           verdict('B-1', 'NOT_ASSESSED', [])],
                 'findings': [self.rule_finding], 'coverage_gaps': ['B-1 needs the caller']}
        checked = mod.standards_contract(value, self.work, self.snapshot, self.mapping, self.batch())
        self.assertEqual(checked['file_verdicts'], {'A-1': {'code.py': 'VIOLATED'}, 'A-2': {'code.py': 'NOT_VIOLATED'},
                                                    'B-1': {'code.py': 'NOT_ASSESSED'}})
        self.assertEqual(checked['coverage_gaps'], ['B-1 needs the caller'])
        missing = {**value, 'rules': value['rules'][:2]}
        with self.assertRaisesRegex(mod.WorkflowContractError, 'B-1'):
            mod.standards_contract(missing, self.work, self.snapshot, self.mapping, self.batch())
        filled = mod.standards_contract(missing, self.work, self.snapshot, self.mapping, self.batch(), fill_missing=True)
        self.assertEqual(filled['coverage_gaps'], ['B-1 needs the caller',
                                                   'The reviewer gave rule B-1 no verdict for code.py: not assessed'])

    def test_standards_contract_rejects_incomplete_or_inconsistent_answers(self):
        ok = [verdict('A-2', 'NOT_VIOLATED', []), verdict('B-1', 'NOT_VIOLATED', [])]
        violated = verdict('A-1', 'VIOLATED', ['N1'])
        cases = {
            'a rule without verdict': ([violated, ok[0]], [self.rule_finding]),
            'old per-rule shape': ([{'id': 'A-1', 'status': 'VIOLATED', 'finding_ids': ['N1']}] + ok, [self.rule_finding]),
            'a file outside the batch': ([{**violated, 'files': violated['files'] + [{'path': 'other.py', 'status': 'NOT_VIOLATED', 'finding_ids': []}]}] + ok,
                                         [self.rule_finding]),
            'a rule twice': ([violated, violated] + ok, [self.rule_finding]),
            'a rule outside the batch': ([violated, *ok, verdict('Z-1', 'NOT_VIOLATED', [])], [self.rule_finding]),
            'violated without findings': ([verdict('A-1', 'VIOLATED', [])] + ok, []),
            'findings but not violated': ([verdict('A-1', 'NOT_VIOLATED', [])] + ok, [self.rule_finding]),
            'finding ids do not match': ([verdict('A-1', 'VIOLATED', ['N9'])] + ok, [self.rule_finding]),
            'unknown status': ([verdict('A-1', 'MAYBE', ['N1'])] + ok, [self.rule_finding]),
            'no standard_ref': ([violated] + ok, [{**self.rule_finding, 'standard_ref': None}]),
            'ref outside the batch': ([violated] + ok, [{**self.rule_finding, 'standard_ref': 'Z-1'}]),
            'base side': ([violated] + ok, [{**self.rule_finding, 'side': 'base'}]),
            'malformed ref': ([violated] + ok, [{**self.rule_finding, 'standard_ref': 'a-1'}]),
        }
        for name, (rules, findings) in cases.items():
            with self.subTest(case=name), self.assertRaises(mod.WorkflowContractError):
                mod.standards_contract({'rules': rules, 'findings': findings, 'coverage_gaps': []},
                                       self.work, self.snapshot, self.mapping, self.batch())
        batch = [{**rule, 'covers': []} if rule['id'] == 'A-1' else rule for rule in self.batch()]
        with self.assertRaisesRegex(mod.WorkflowContractError, 'does not apply'):
            mod.standards_contract({'rules': [violated] + ok, 'findings': [self.rule_finding], 'coverage_gaps': []},
                                   self.work, self.snapshot, self.mapping, batch)

    def test_maintainability_needs_a_rule(self):
        with self.assertRaisesRegex(mod.WorkflowContractError, 'MAINTAINABILITY requires'):
            mod.validate_finding({**self.rule_finding, 'standard_ref': None}, self.work, self.snapshot, self.mapping)

    def test_severity_stands_and_nice_to_have_is_a_suggestion_unless_rated_high(self):
        levels = {'A-1': 'MANDATORY', 'B-1': 'NICE-TO-HAVE'}
        mandatory = mod.route_finding(self.rule_finding, self.snapshot, self.mapping, levels)
        self.assertEqual((mandatory['severity'], mandatory['route'], mandatory['rule_level']), ('MEDIUM', 'AUTO_FIX', 'MANDATORY'))
        high = mod.route_finding({**self.rule_finding, 'severity': 'HIGH'}, self.snapshot, self.mapping, levels)
        self.assertEqual((high['severity'], high['route']), ('HIGH', 'HUMAN_REQUIRED'))
        nice = mod.route_finding({**self.rule_finding, 'standard_ref': 'B-1', 'severity': 'MEDIUM'}, self.snapshot, self.mapping, levels)
        self.assertEqual((nice['severity'], nice['route']), ('MEDIUM', 'SUGGESTION'))
        exception = mod.route_finding({**self.rule_finding, 'standard_ref': 'B-1', 'severity': 'HIGH'}, self.snapshot, self.mapping, levels)
        self.assertEqual((exception['severity'], exception['route']), ('HIGH', 'HUMAN_REQUIRED'))
        sensitive = {**self.mapping, 'files': [{**self.mapping['files'][0], 'sensitive_boundaries': ['BUSINESS_RULES']}]}
        self.assertEqual(mod.route_finding(self.rule_finding, self.snapshot, sensitive, levels)['route'], 'HUMAN_REQUIRED')
        with self.assertRaisesRegex(mod.WorkflowContractError, 'Unknown project rule'):
            mod.route_finding(self.rule_finding, self.snapshot, self.mapping, {})
        plain = {**self.rule_finding, 'category': 'CORRECTNESS', 'standard_ref': None}
        self.assertNotEqual(mod.route_finding(plain, self.snapshot, self.mapping)['stable_id'], mandatory['stable_id'])
        self.assertNotIn('rule_level', mod.route_finding(plain, self.snapshot, self.mapping))

    def test_the_validator_may_not_change_the_cited_rule(self):
        candidate = {**self.rule_finding, 'candidate_id': 'standards-b1:N1'}
        decision = {'candidate_id': 'standards-b1:N1', 'disposition': 'ACCEPT', 'reason': 'Confirmed',
                    'finding': {**candidate, 'standard_ref': 'A-2'}}
        with self.assertRaisesRegex(mod.WorkflowContractError, 'standard_ref'):
            mod.adjudication_contract({'decisions': [decision], 'coverage_gaps': []}, [candidate], self.work, self.snapshot, self.mapping)

    def test_pipeline_with_rules(self):
        standards = mod.prepare_standards(self.work, self.snapshot, self.knowledge)
        self.assertEqual(standards['selection']['skipped'], ['Z-1'])
        self.assertEqual([[r['id'] for r in b] for b in standards['batches']], [['A-1', 'A-2', 'B-1']])
        self.assertEqual((self.work / 'knowledge/diff-b1.patch').read_text(), (self.work / 'diff.patch').read_text())
        self.assertTrue((self.work / 'knowledge/rules/A-1.md').is_file())
        prompts = {}
        outside = {**self.rule_finding, 'candidate_id': 'N2', 'line_start': 1, 'line_end': 1, 'title': 'Header name'}
        nice = {**self.rule_finding, 'candidate_id': 'N3', 'standard_ref': 'B-1', 'title': 'Could be clearer'}
        def fake(**kwargs):
            role = kwargs['step_id'].removeprefix('source-')
            prompts[role] = kwargs['prompt']
            if role == 'mapper':
                value = copy.deepcopy(self.mapping)
            elif role in ('correctness', 'security'):
                value = {'findings': [], 'coverage_gaps': []}
            elif role == 'standards-b1':
                self.assertEqual(kwargs['agent'], 'sdlc_source_standards')
                value = {'rules': [verdict('A-1', 'VIOLATED', ['N1', 'N2']), verdict('A-2', 'NOT_ASSESSED', []),
                                   verdict('B-1', 'VIOLATED', ['N3'])],
                         'findings': [copy.deepcopy(self.rule_finding), outside, nice], 'coverage_gaps': []}
            elif role == 'validator':
                candidates = json.loads((self.work / 'candidates.json').read_text())
                self.assertEqual([c['candidate_id'] for c in candidates], ['standards-b1:N1', 'standards-b1:N3'])
                gaps = json.loads((self.work / 'reported-gaps.json').read_text())
                self.assertEqual([g['gap'] for g in gaps], ['Project rule A-2 was not assessed in code.py'])
                value = {'decisions': [{'candidate_id': c['candidate_id'], 'disposition': 'ACCEPT', 'reason': 'Confirmed',
                                        'finding': c} for c in candidates],
                         'coverage_gaps': [{'gap': 'Rule A-2 was not assessed', 'covers': ['G1']}]}
            else:
                routed = json.loads((self.work / 'routed-findings.json').read_text())
                value = {'summary': 'Rules', 'comments': [{'stable_id': f['stable_id'], 'explanation': 'Rename it.'}
                                                          for f in routed['findings'] + routed['suggestions']]}
            return kwargs['validator'](value)
        with patch.object(mod, '_run_json_contract_step', side_effect=fake):
            report = mod.run_agents(self.work, self.snapshot, 4, standards)
        self.assertIn('knowledge/ holds the project', prompts['mapper'])
        self.assertTrue(prompts['standards-b1'].rstrip().endswith(standards['indexes'][0].rstrip()))
        self.assertIn('Candidates with a standard_ref', prompts['validator'])
        by_ref = {f['standard_ref']: f for f in report['findings']}
        self.assertEqual((by_ref['A-1']['severity'], by_ref['A-1']['route']), ('MEDIUM', 'AUTO_FIX'))
        self.assertEqual((by_ref['B-1']['severity'], by_ref['B-1']['route']), ('MEDIUM', 'SUGGESTION'))
        self.assertEqual(report['queues']['SUGGESTION'], [by_ref['B-1']['stable_id']])
        self.assertEqual([(s['standard_ref'], s['severity'], s['outside_changed_lines']) for s in report['suggestions']],
                         [('A-1', 'MEDIUM', True)])
        self.assertEqual(report['standards']['verdicts'], {'A-1': 'VIOLATED', 'A-2': 'NOT_ASSESSED', 'B-1': 'VIOLATED'})
        self.assertEqual(report['standards']['not_assessed'], ['A-2 in code.py'])
        self.assertEqual(report['standards']['batches'], [{'rules': ['A-1', 'A-2', 'B-1'], 'files': ['code.py']}])
        self.assertIn('knowledge/diff-b1.patch', prompts['standards-b1'])
        self.assertEqual((report['standards']['applicable'], report['standards']['skipped']), (['A-1', 'A-2', 'B-1'], ['Z-1']))
        self.assertEqual(report['coverage_status'], 'INCOMPLETE')
        report['status'] = 'REVIEWED'
        for finding in report['findings']:
            finding['placement'] = mod.place_finding(finding, mod.diff_hunks((self.work / 'diff.patch').read_text()))
        comments = mod.render_comments(report)
        self.assertIn('Project rule: A-1 (MANDATORY)', comments)
        self.assertIn('## Suggestions outside the changed lines', comments)
        draft = mod.render_draft(report)
        general = draft.split('<!-- general -->')[1].split('<!-- end general -->')[0]
        self.assertIn('code.py:1-1 (A-1): Rename it.', general)

    def test_without_rules_no_standards_step_runs_and_the_report_is_unchanged(self):
        steps = []
        def fake(**kwargs):
            steps.append(kwargs['step_id'])
            role = kwargs['step_id'].removeprefix('source-')
            value = {'mapper': copy.deepcopy(self.mapping), 'validator': {'decisions': [], 'coverage_gaps': []},
                     'feedback': {'summary': 'None', 'comments': []}}.get(role, {'findings': [], 'coverage_gaps': []})
            return kwargs['validator'](value)
        with patch.object(mod, '_run_json_contract_step', side_effect=fake):
            report = mod.run_agents(self.work, self.snapshot, 4)
        self.assertEqual(steps, ['source-mapper', 'source-correctness', 'source-security', 'source-validator', 'source-feedback'])
        self.assertEqual(set(report['queues']), {'AUTO_FIX', 'HUMAN_REQUIRED'})
        self.assertNotIn('standards', report)
        self.assertNotIn('suggestions', report)

    def test_rules_that_match_no_changed_file_cost_no_agent_call(self):
        knowledge = {**self.knowledge, 'rules': [r for r in self.knowledge['rules'] if r['id'] == 'Z-1']}
        standards = mod.prepare_standards(self.work, self.snapshot, knowledge)
        self.assertEqual((standards['batches'], standards['selection']['skipped']), ([], ['Z-1']))


def verdict(rule_id, status, ids, path='code.py'):
    return {'id': rule_id, 'files': [{'path': path, 'status': status, 'finding_ids': ids}]}


class ParallelStepsTest(unittest.TestCase):
    """runtime.run_parallel_steps, as bundled into Source review."""

    def test_results_come_back_in_call_order_and_the_cap_is_respected(self):
        lock, running, peak = threading.Lock(), [0], [0]
        release = threading.Event()
        def call(value, delay):
            def run():
                with lock:
                    running[0] += 1
                    peak[0] = max(peak[0], running[0])
                release.wait(delay)
                with lock:
                    running[0] -= 1
                return value
            return run
        threading.Timer(0.3, release.set).start()
        results = mod.run_parallel_steps([call(index, 0.05 * (6 - index)) for index in range(6)], 2)
        self.assertEqual(results, list(range(6)))
        self.assertEqual(peak[0], 2)

    def test_one_means_sequential_on_the_calling_thread(self):
        threads = mod.run_parallel_steps([lambda: threading.current_thread()] * 3, 1)
        self.assertEqual(set(threads), {threading.current_thread()})

    def test_every_call_finishes_and_the_first_failure_in_call_order_is_raised(self):
        done = []
        def fail(message, delay):
            def run():
                threading.Event().wait(delay)
                done.append(message)
                raise mod.WorkflowContractError(message)
            return run
        with self.assertRaisesRegex(mod.WorkflowContractError, 'first'):
            mod.run_parallel_steps([fail('first', 0.2), fail('second', 0), lambda: done.append('ok')], 4)
        self.assertEqual(sorted(done), ['first', 'ok', 'second'])

    def test_the_cap_is_validated(self):
        for value in (0, 5):
            with self.subTest(value=value), self.assertRaises(mod.WorkflowContractError):
                mod.run_parallel_steps([lambda: 1], value)


if __name__ == '__main__':
    unittest.main()
