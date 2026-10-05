"""Resuming a BLOCKED Delivery run from its manifest (agentic-sdlc-docs/plans/delivery-resume.md).

The delivery manifest and the commits on sdlc/<ticket> are the only input a resume may read:
in a container, .agentic-sdlc/runtime/ of the earlier run is gone. These tests therefore check
what each BLOCKED exit leaves in the manifest, in both the deployed bundle and the source modules.
"""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
