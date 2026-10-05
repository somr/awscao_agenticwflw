"""Resuming a BLOCKED Delivery run from its manifest (agentic-sdlc-docs/plans/delivery-resume.md).

The delivery manifest and the commits on sdlc/<ticket> are the only input a resume may read:
in a container, .agentic-sdlc/runtime/ of the earlier run is gone. These tests therefore check
what each BLOCKED exit leaves in the manifest, in both the deployed bundle and the source modules.
"""
from __future__ import annotations
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workflow_integration import DeliveryHarness, load

TWO_TASKS = {'tasks': [
    {'id': 'T1', 'worker': 'developer', 'plan_reference': 'T1', 'instructions': 'Write value',
     'depends_on': [], 'skills': [], 'owns': ['app/value.py']},
    {'id': 'T2', 'worker': 'developer', 'plan_reference': 'T2', 'instructions': 'Write extra',
     'depends_on': ['T1'], 'skills': [], 'owns': ['app/extra.py']},
]}
ONE_TASK = {'tasks': TWO_TASKS['tasks'][:1]}
GOOD_VALUE = 'def value():\n    return 3\n'


def completion(*tasks, files=()):
    return {'tasks_completed': list(tasks), 'files_changed': list(files), 'assumptions': [], 'deviations': []}


class BlockedManifestTest(DeliveryHarness, unittest.TestCase):
    def deliver(self, temp, modular, responder, run_id):
        """responder(repo, prompts) returns the simulated agents' answer function."""
        repo, git, records = self.plan(Path(temp), modular)
        delivery, transport = load('deliver', modular)
        prompts = {}
        calls, output = self.drive(delivery, transport, {'repository_root': str(repo), 'ticket_id': 'T-1'},
                                   responder(repo, prompts), run_id)
        manifest = json.loads((records / 'delivery-manifest.json').read_text())
        return git, prompts, output, manifest

    def assert_resumable(self, manifest, output, git, reason, resume_point):
        self.assertEqual(output['workflow_outcome'], 'BLOCKED')
        self.assertEqual(output['resume_point'], resume_point)
        self.assertEqual(manifest['schema_version'], '1.1')
        self.assertEqual(manifest['state'], 'BLOCKED')
        self.assertTrue(manifest['reason'].startswith(reason), manifest['reason'])
        self.assertEqual(output['reason'], manifest['reason'])
        self.assertEqual(manifest['resume_point'], resume_point)
        self.assertEqual(manifest['branch_head_sha'], git('rev-parse', 'HEAD'))
        self.assertEqual(manifest['base_sha'], git('rev-parse', 'main'))

    def test_verification_failure_records_progress_identity_and_output(self):
        def responder(repo, prompts):
            def respond(agent, step_id, prompt):
                prompts[step_id] = prompt
                if agent == 'sdlc_code_supervisor':
                    return TWO_TASKS
                name = 'extra.py' if step_id == 'worker-T2' else 'value.py'
                (repo / 'app' / name).write_text(f'broken python {step_id} !!!\n')
                return completion(step_id, files=[name])
            return respond
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                git, prompts, output, manifest = self.deliver(temp, modular, responder, 'resume-verify-1')
                self.assert_resumable(manifest, output, git, 'verification_failed_after_one_repair_attempt', 'verification')
                # Bundle identity: the module manifest exists only in the generated bundle.
                self.assertEqual(manifest['bundle']['manifest_sha256'] is None, modular)
                self.assertRegex(manifest['bundle']['registry_sha256'], '^[0-9a-f]{64}$')
                # Hybrid progress: the dispatch and each task's commit.
                self.assertEqual(manifest['hybrid']['dispatch'], TWO_TASKS)
                self.assertEqual(manifest['hybrid']['waves'], [['T1'], ['T2']])
                self.assertEqual([t['task'] for t in manifest['hybrid']['tasks']], ['T1', 'T2'])
                for task in manifest['hybrid']['tasks']:
                    self.assertEqual(git('cat-file', '-t', task['commit']), 'commit')
                self.assertIsNotNone(manifest['hybrid']['integration'])
                # Failed commands keep the end of their output, and the repair turn saw it.
                failed = [c for c in manifest['verification']['commands'] if not c['passed']]
                self.assertTrue(failed)
                self.assertIn('broken python', failed[0]['output_tail'])
                self.assertIn('output_tail', prompts['implement-v1-repair-1'])
                self.assertIn('broken python worker-T', prompts['implement-v1-repair-1'])
                self.assertEqual(manifest['totals'], {'runs': 1, 'repair_turns': 1, 'remediation_rounds': 0})

    def test_a_repair_that_changes_nothing_ends_blocked_not_failed(self):
        def responder(repo, prompts):
            def respond(agent, step_id, prompt):
                if agent == 'sdlc_code_supervisor':
                    return ONE_TASK
                if step_id != 'implement-v1-repair-1':
                    (repo / 'app/value.py').write_text('broken !!!\n')
                return completion('T1')
            return respond
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                git, prompts, output, manifest = self.deliver(temp, modular, responder, 'resume-repair-1')
                self.assert_resumable(manifest, output, git, 'repair_changed_nothing', 'verification')
                self.assertFalse(manifest['verification']['passed'])
                self.assertEqual(manifest['totals']['repair_turns'], 1)

    def test_a_failed_worker_keeps_the_finished_tasks_in_the_manifest(self):
        def responder(repo, prompts):
            def respond(agent, step_id, prompt):
                if agent == 'sdlc_code_supervisor':
                    return TWO_TASKS
                if step_id.startswith('worker-T2'):
                    return 'not json'  # breaks the contract, also on its repair turn
                (repo / 'app/value.py').write_text(GOOD_VALUE)
                return completion('T1', files=['app/value.py'])
            return respond
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                git, prompts, output, manifest = self.deliver(temp, modular, responder, 'resume-impl-1')
                self.assert_resumable(manifest, output, git, 'hybrid_implementation_failed', 'implementation')
                self.assertEqual(manifest['hybrid']['dispatch'], TWO_TASKS)
                [t1] = manifest['hybrid']['tasks']
                self.assertEqual((t1['task'], t1['status']), ('T1', 'committed'))
                self.assertEqual(t1['commit'], git('rev-parse', 'HEAD'))
                self.assertIsNone(manifest['hybrid']['integration'])

    def test_a_reviewer_that_breaks_its_contract_ends_blocked(self):
        def responder(repo, prompts):
            def respond(agent, step_id, prompt):
                if agent == 'sdlc_code_supervisor':
                    return ONE_TASK
                if step_id.startswith('pr-review'):
                    return {'summary': 'Invalid', 'findings': [{'id': 'X1', 'category': 'NOT_A_CATEGORY'}]}
                (repo / 'app/value.py').write_text(GOOD_VALUE)
                return completion('T1', files=['app/value.py'])
            return respond
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                git, prompts, output, manifest = self.deliver(temp, modular, responder, 'resume-review-1')
                self.assert_resumable(manifest, output, git, 'review_failed', 'verification')
                self.assertTrue(manifest['verification']['passed'])



class ResumeFromVerificationTest(DeliveryHarness, unittest.TestCase):
    """A BLOCKED verification, a hand fix committed on the branch, then resume=true."""

    def blocked(self, temp, modular):
        """First run: the worker writes broken code and the repair turn changes nothing."""
        repo, git, records = self.plan(Path(temp), modular)
        def respond(agent, step_id, prompt):
            if agent == 'sdlc_code_supervisor':
                return ONE_TASK
            if step_id == 'worker-T1':
                (repo / 'app/value.py').write_text('broken python !!!\n')
            return completion('T1')
        output = self.run_delivery(repo, modular, respond, 'first-run')[1]
        self.assertEqual(output['reason'], 'repair_changed_nothing')
        return repo, git, records

    def run_delivery(self, repo, modular, respond, run_id, resume=False):
        delivery, transport = load('deliver', modular)
        inputs = {'repository_root': str(repo), 'ticket_id': 'T-1', 'resume': resume}
        return self.drive(delivery, transport, inputs, respond, run_id)

    def hand_fix(self, repo, git):
        """What a developer does: fix the code and a build file outside the source roots, and commit."""
        (repo / 'app/value.py').write_text(GOOD_VALUE)
        (repo / 'pom.xml').write_text('<project/>\n')
        git('add', 'app/value.py', 'pom.xml')
        git('commit', '-qm', 'Fix value and pom.xml by hand')
        return git('rev-parse', 'HEAD')

    def reviewer(self, prompts):
        def respond(agent, step_id, prompt):
            prompts[step_id] = prompt
            if agent == 'sdlc_pr_reviewer':
                return {'summary': 'Looks fine', 'findings': []}
            raise AssertionError(f'{agent} {step_id} must not run on a resume from verification')
        return respond

    def test_resume_verifies_and_reviews_the_hand_fix_without_implementing_again(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.blocked(temp, modular)
                first = json.loads((records / 'delivery-manifest.json').read_text())
                fix = self.hand_fix(repo, git)
                prompts = {}
                calls, output = self.run_delivery(repo, modular, self.reviewer(prompts), 'second-run', resume=True)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW', output)
                self.assertEqual([c[1] for c in calls], ['pr-review-r1'])
                self.assertEqual(output['resumed_from'], 'first-run')
                manifest = json.loads((records / 'delivery-manifest.json').read_text())
                self.assertEqual(manifest['workflow_run_id'], 'second-run')
                self.assertEqual(manifest['resumed_from']['run_id'], 'first-run')
                self.assertEqual(manifest['resumed_from']['reason'], 'repair_changed_nothing')
                self.assertEqual([c['sha'] for c in manifest['hand_commits']], [fix])
                self.assertEqual(manifest['hand_commits'][0]['paths'], ['app/value.py', 'pom.xml'])
                self.assertEqual(manifest['base_drift']['commits_behind'], 0)
                self.assertEqual(manifest['delivery_commits'], first['delivery_commits'])
                self.assertEqual(manifest['base_sha'], first['base_sha'])
                self.assertEqual(manifest['totals'], {'runs': 2, 'repair_turns': 1, 'remediation_rounds': 0})
                self.assertTrue(manifest['verification']['passed'])
                self.assertEqual(manifest['pr_head_sha'], fix)
                for text in (prompts['pr-review-r1'], (records / 'pr-body.md').read_text(),
                             (records / 'human-review-brief.md').read_text()):
                    self.assertIn(f'Commit {fix[:12]} was added by hand', text)
                    self.assertIn('pom.xml', text)
                self.assertIn('pom.xml', (records / 'pr-diff.patch').read_text())

    def test_a_refused_resume_leaves_the_blocked_manifest_for_the_next_attempt(self):
        cases = {
            'uncommitted': ('resume needs every change committed',
                            lambda repo, git: (repo / '.gitignore').write_text((repo / '.gitignore').read_text() + '# not committed\n')),
            'reset': ('no longer in the delivery branch',
                      lambda repo, git: git('reset', '-q', '--hard', 'main')),
            'roots': ('outside the current source roots',
                      lambda repo, git: self.narrow_roots(repo, git)),
        }
        for modular in (False, True):
            for name, (message, change) in cases.items():
                with self.subTest(modular=modular, case=name), tempfile.TemporaryDirectory() as temp:
                    repo, git, records = self.blocked(temp, modular)
                    before = (records / 'delivery-manifest.json').read_text()
                    change(repo, git)
                    with self.assertRaisesRegex(ValueError, message):
                        self.run_delivery(repo, modular, self.reviewer({}), 'refused', resume=True)
                    self.assertEqual((records / 'delivery-manifest.json').read_text(), before)

    def narrow_roots(self, repo, git):
        def change(config):
            config['source_roots'] = ['app/tests']
        self.edit_config(repo, change)
        git('commit', '-qam', 'Narrow the source roots')

    def test_resume_needs_a_blocked_manifest(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.plan(Path(temp), modular)
                with self.assertRaisesRegex(ValueError, 'none found'):
                    self.run_delivery(repo, modular, self.reviewer({}), 'no-manifest', resume=True)
                manifest_path = records / 'delivery-manifest.json'
                manifest_path.write_text(json.dumps({'schema_version': '1.0', 'state': 'BLOCKED'}))
                with self.assertRaisesRegex(ValueError, 'only 1.1 records enough'):
                    self.run_delivery(repo, modular, self.reviewer({}), 'old-manifest', resume=True)

    def test_base_branch_changes_to_the_same_files_stop_until_merged_by_hand(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.blocked(temp, modular)
                self.hand_fix(repo, git)
                git('checkout', '-q', 'main')
                (repo / 'app/value.py').write_text('def value():\n    return 4\n')
                git('commit', '-qam', 'Someone changed value on main')
                git('checkout', '-q', 'sdlc/T-1')
                calls, output = self.run_delivery(repo, modular, self.reviewer({}), 'drift-run', resume=True)
                self.assertEqual(calls, [])
                self.assertEqual(output['reason'], 'base_drift_overlap: app/value.py')
                self.assertEqual(output['resume_point'], 'verification')
                manifest = json.loads((records / 'delivery-manifest.json').read_text())
                self.assertEqual(manifest['base_drift']['commits_behind'], 1)
                # The human merges main (resolving the conflict); the merge keeps the recorded commits.
                git('merge', '-q', 'main', '-X', 'ours', '-m', 'Merge main into the delivery')
                prompts = {}
                calls, output = self.run_delivery(repo, modular, self.reviewer(prompts), 'merged-run', resume=True)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW', output)
                manifest = json.loads((records / 'delivery-manifest.json').read_text())
                self.assertEqual(manifest['base_drift']['commits_behind'], 0)
                self.assertEqual(manifest['resumed_from']['reason'], 'base_drift_overlap: app/value.py')
                self.assertEqual([c['subject'] for c in manifest['hand_commits']],
                                 ['Fix value and pom.xml by hand', 'Merge main into the delivery'])
                self.assertEqual(manifest['totals']['runs'], 3)

    def test_base_branch_changes_elsewhere_are_recorded_and_the_resume_continues(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.blocked(temp, modular)
                self.hand_fix(repo, git)
                git('checkout', '-q', 'main')
                (repo / 'README.md').write_text('changed on main\n')
                git('add', 'README.md')
                git('commit', '-qm', 'Unrelated change on main')
                git('checkout', '-q', 'sdlc/T-1')
                prompts = {}
                calls, output = self.run_delivery(repo, modular, self.reviewer(prompts), 'drift-ok', resume=True)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW', output)
                manifest = json.loads((records / 'delivery-manifest.json').read_text())
                self.assertEqual((manifest['base_drift']['commits_behind'], manifest['base_drift']['overlapping_paths']), (1, []))
                self.assertIn('1 commit(s) ahead of the delivery branch', (records / 'pr-body.md').read_text())

    def test_review_records_continue_the_earlier_numbering(self):
        finding = {'id': 'A1', 'file': 'app/value.py', 'location': 'value', 'category': 'CORRECTNESS',
                   'impact': 'LOW', 'confidence': .9, 'failure_scenario': 'Two, expected three',
                   'consequence': 'Wrong value', 'remediation_direction': 'Return three',
                   'automation_eligibility': 'AUTO_FIX', 'reason': 'Local fix',
                   'localized_and_bounded': True, 'deterministically_verifiable': True}
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.plan(Path(temp), modular)
                def respond(agent, step_id, prompt):
                    if agent == 'sdlc_code_supervisor':
                        return ONE_TASK
                    if agent == 'sdlc_pr_reviewer':
                        if step_id == 'pr-review-r1':
                            return {'summary': 'One fix', 'findings': [finding]}
                        return {'summary': 'Invalid', 'findings': [{'id': 'X1', 'category': 'NOT_A_CATEGORY'}]}
                    if agent == 'sdlc_remediator':
                        (repo / 'app/value.py').write_text(GOOD_VALUE)
                        return {'findings_addressed': ['A1'], 'files_changed': ['app/value.py'], 'assumptions': [], 'deviations': []}
                    (repo / 'app/value.py').write_text('def value():\n    return 2\n')
                    return completion('T1', files=['app/value.py'])
                output = self.run_delivery(repo, modular, respond, 'review-blocked')[1]
                self.assertTrue(output['reason'].startswith('review_failed'), output)
                first = json.loads((records / 'delivery-manifest.json').read_text())
                self.assertEqual(first['review_rounds'], 1)
                self.assertEqual(first['totals']['remediation_rounds'], 1)
                self.assertEqual([c['step'] for c in first['delivery_commits']], ['task T1', 'remediate-r1'])
                calls, output = self.run_delivery(repo, modular, self.reviewer({}), 'review-resumed', resume=True)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW', output)
                self.assertEqual(output['review_round'], 2)
                self.assertEqual(json.loads((records / 'pr-review-r1.json').read_text())['findings'][0]['id'], 'A1')
                self.assertEqual(json.loads((records / 'pr-review-r2.json').read_text())['findings'], [])
                manifest = json.loads((records / 'delivery-manifest.json').read_text())
                self.assertEqual(manifest['latest_review_path'], 'agentic-sdlc-records/T-1/pr-review-r2.json')
                self.assertEqual(manifest['totals'], {'runs': 2, 'repair_turns': 0, 'remediation_rounds': 1})




class ResumeFromImplementationTest(DeliveryHarness, unittest.TestCase):
    """Implementation stopped part-way: a resume skips finished tasks and runs the rest."""

    def run_delivery(self, repo, modular, respond, run_id, resume=False, max_parallel=4):
        delivery, transport = load('deliver', modular)
        inputs = {'repository_root': str(repo), 'ticket_id': 'T-1', 'resume': resume, 'hybrid_max_parallel': max_parallel}
        return self.drive(delivery, transport, inputs, respond, run_id, worktrees=True)

    def agents(self, repo, prompts, dispatch, failing=()):
        """Workers write app/<task>.py; tasks in failing break their contract; reviews are clean."""
        def respond(agent, step_id, prompt):
            prompts[step_id] = prompt
            if agent == 'sdlc_code_supervisor':
                return dispatch
            if agent == 'sdlc_pr_reviewer':
                return {'summary': 'Fine', 'findings': []}
            task = step_id.split('-')[1] if step_id.startswith('worker-') else None
            if task in failing:
                return 'not json'
            if task:
                tree = re.search(r'isolated working copy of the repository at (\S+)\. ', prompt)
                root = Path(tree.group(1)) if tree else repo
                (root / f'app/{task.lower()}.py').write_text(f'NAME = "{task}"\n')
            else:  # integration
                (repo / 'app/value.py').write_text(GOOD_VALUE)
            return completion(task or 'integration', files=[f'app/{(task or "value").lower()}.py'])
        return respond

    def test_finished_tasks_are_skipped_and_the_rest_run(self):
        dispatch = {'tasks': [dict(t, owns=[f"app/{t['id'].lower()}.py"]) for t in TWO_TASKS['tasks']]}
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.plan(Path(temp), modular)
                output = self.run_delivery(repo, modular, self.agents(repo, {}, dispatch, failing=('T2',)), 'impl-blocked')[1]
                self.assertTrue(output['reason'].startswith('hybrid_implementation_failed'), output)
                t1_commit = git('rev-parse', 'HEAD')
                prompts = {}
                calls, output = self.run_delivery(repo, modular, self.agents(repo, prompts, {'tasks': []}), 'impl-resumed', resume=True)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW', output)
                self.assertEqual([c[1] for c in calls], ['worker-T2', 'integrate-v1', 'pr-review-r1'])
                # The dependent task sees the finished task's recorded result.
                self.assertIn('"task": "T1"', prompts['worker-T2'])
                manifest = json.loads((records / 'delivery-manifest.json').read_text())
                self.assertEqual([(t['task'], t['status']) for t in manifest['hybrid']['tasks']],
                                 [('T1', 'committed'), ('T2', 'committed')])
                self.assertEqual(manifest['hybrid']['tasks'][0]['commit'], t1_commit)
                self.assertEqual([c['task'] for c in manifest['implementation_commits']], ['T1', 'T2', 'integration'])
                shas = [c['sha'] for c in manifest['delivery_commits']]
                self.assertEqual(len(shas), len(set(shas)))
                self.assertEqual(len(shas), 3)
                self.assertEqual(git('log', '--format=%s', 'main..HEAD').split('\n'),
                                 ['[T-1] Integrate hybrid assignments', '[T-1] T2: implement hybrid task', '[T-1] T1: implement hybrid task'])
                self.assertEqual(manifest['resumed_from']['resume_point'], 'implementation')

    def test_a_failed_parallel_wave_runs_again_completely(self):
        dispatch = {'tasks': [
            {'id': 'T1', 'worker': 'developer', 'plan_reference': 'T1', 'instructions': 'One', 'depends_on': [], 'skills': [], 'owns': ['app/t1.py']},
            {'id': 'T2', 'worker': 'developer', 'plan_reference': 'T2', 'instructions': 'Two', 'depends_on': [], 'skills': [], 'owns': ['app/t2.py']},
        ]}
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.plan(Path(temp), modular)
                output = self.run_delivery(repo, modular, self.agents(repo, {}, dispatch, failing=('T2',)), 'wave-blocked')[1]
                self.assertTrue(output['reason'].startswith('hybrid_implementation_failed'), output)
                manifest = json.loads((records / 'delivery-manifest.json').read_text())
                self.assertEqual(manifest['hybrid']['tasks'], [])  # nothing from a failed wave is merged
                calls, output = self.run_delivery(repo, modular, self.agents(repo, {}, {'tasks': []}), 'wave-resumed', resume=True)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW', output)
                self.assertEqual(sorted(c[1] for c in calls if c[1].startswith('worker-')), ['worker-T1', 'worker-T2'])
                self.assertNotIn('dispatch-v1', [c[1] for c in calls])

    def test_saved_tasks_that_the_current_registry_rejects_are_refused(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.plan(Path(temp), modular)
                self.run_delivery(repo, modular, self.agents(repo, {}, TWO_TASKS, failing=('T2',)), 'impl-blocked')
                before = (records / 'delivery-manifest.json').read_text()
                def rename_worker(config):
                    config['workers'] = {'engineer': config['workers']['developer']}
                self.edit_config(repo, rename_worker)
                git('commit', '-qam', 'Rename the developer worker')
                with self.assertRaisesRegex(ValueError, 'Unregistered worker'):
                    self.run_delivery(repo, modular, self.agents(repo, {}, {'tasks': []}), 'impl-refused', resume=True)
                self.assertEqual((records / 'delivery-manifest.json').read_text(), before)

    def test_without_saved_tasks_the_supervisor_runs_again(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.plan(Path(temp), modular)
                output = self.run_delivery(repo, modular, self.agents(repo, {}, {'tasks': 'invalid'}), 'dispatch-blocked')[1]
                self.assertTrue(output['reason'].startswith('hybrid_implementation_failed'), output)
                calls, output = self.run_delivery(repo, modular, self.agents(repo, {}, ONE_TASK), 'dispatch-resumed', resume=True)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW', output)
                self.assertEqual([c[1] for c in calls][:2], ['dispatch-v1', 'worker-T1'])


    def blocked_then_renamed(self, temp, modular):
        """T1 finished, T2 failed; then the registry renames the worker the saved tasks use."""
        repo, git, records = self.plan(Path(temp), modular)
        self.run_delivery(repo, modular, self.agents(repo, {}, TWO_TASKS, failing=('T2',)), 'impl-blocked')
        def rename_worker(config):
            config['workers'] = {'engineer': config['workers']['developer']}
        self.edit_config(repo, rename_worker)
        git('commit', '-qam', 'Rename the developer worker')
        return repo, git, records

    def redispatch_run(self, repo, modular, respond, run_id):
        delivery, transport = load('deliver', modular)
        inputs = {'repository_root': str(repo), 'ticket_id': 'T-1', 'resume': True, 'resume_redispatch': True}
        return self.drive(delivery, transport, inputs, respond, run_id, worktrees=True)

    def test_redispatch_assigns_only_the_remaining_work(self):
        # The new dispatch reuses the id T1 on purpose: ids of an earlier dispatch must not count as finished.
        remaining = {'tasks': [{'id': 'T1', 'worker': 'engineer', 'plan_reference': 'T2', 'instructions': 'Write extra',
                                'depends_on': [], 'skills': [], 'owns': ['app/t1.py']}]}
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.blocked_then_renamed(temp, modular)
                old_t1 = json.loads((records / 'delivery-manifest.json').read_text())['hybrid']['tasks'][0]
                prompts = {}
                calls, output = self.redispatch_run(repo, modular, self.agents(repo, prompts, remaining), 'redispatched')
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW', output)
                self.assertEqual([c[1] for c in calls], ['dispatch-v1', 'worker-T1', 'integrate-v1', 'pr-review-r1'])
                self.assertIn('assign ONLY the remaining approved work', prompts['dispatch-v1'])
                self.assertIn('"task": "T1"', prompts['dispatch-v1'])
                manifest = json.loads((records / 'delivery-manifest.json').read_text())
                tasks = manifest['hybrid']['tasks']
                self.assertEqual(tasks[0], dict(old_t1, earlier_dispatch=True))
                self.assertEqual([t['task'] for t in tasks], ['T1', 'T1'])
                self.assertNotIn('earlier_dispatch', tasks[1])
                self.assertEqual(manifest['hybrid']['dispatch'], remaining)
                self.assertEqual(manifest['implementation_commits'][0]['commit'], old_t1['commit'])

    def test_a_failed_redispatch_keeps_the_saved_tasks_for_the_next_resume(self):
        remaining = {'tasks': [{'id': 'T9', 'worker': 'engineer', 'plan_reference': 'T2', 'instructions': 'Write extra',
                                'depends_on': [], 'skills': [], 'owns': ['app/t9.py']}]}
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.blocked_then_renamed(temp, modular)
                saved = json.loads((records / 'delivery-manifest.json').read_text())['hybrid']
                output = self.redispatch_run(repo, modular, self.agents(repo, {}, {'tasks': 'invalid'}), 'redispatch-failed')[1]
                self.assertTrue(output['reason'].startswith('hybrid_implementation_failed'), output)
                manifest = json.loads((records / 'delivery-manifest.json').read_text())
                self.assertEqual(manifest['hybrid']['dispatch'], saved['dispatch'])
                self.assertEqual(manifest['hybrid']['tasks'], saved['tasks'])
                calls, output = self.redispatch_run(repo, modular, self.agents(repo, {}, remaining), 'redispatch-again')
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW', output)
                self.assertEqual([c[1] for c in calls][:2], ['dispatch-v1', 'worker-T9'])

    def test_a_resume_after_a_redispatch_does_not_mistake_an_old_task_for_a_new_one(self):
        remaining = {'tasks': [{'id': 'T1', 'worker': 'engineer', 'plan_reference': 'T2', 'instructions': 'Write extra',
                                'depends_on': [], 'skills': [], 'owns': ['app/t1.py']}]}
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.blocked_then_renamed(temp, modular)
                output = self.redispatch_run(repo, modular, self.agents(repo, {}, remaining, failing=('T1',)), 'new-t1-failed')[1]
                self.assertTrue(output['reason'].startswith('hybrid_implementation_failed'), output)
                # The old T1 finished, the new T1 did not: a plain resume must run the new T1.
                calls, output = self.run_delivery(repo, modular, self.agents(repo, {}, {'tasks': []}), 'new-t1-resumed', resume=True)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW', output)
                self.assertIn('worker-T1', [c[1] for c in calls])

    def test_redispatch_needs_resume(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.plan(Path(temp), modular)
                delivery, transport = load('deliver', modular)
                inputs = {'repository_root': str(repo), 'ticket_id': 'T-1', 'resume_redispatch': True}
                with self.assertRaisesRegex(ValueError, 'needs resume=true'):
                    self.drive(delivery, transport, inputs, self.agents(repo, {}, ONE_TASK), 'no-resume')


if __name__ == '__main__':
    unittest.main()
