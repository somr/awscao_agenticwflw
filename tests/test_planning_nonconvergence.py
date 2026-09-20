"""Planning that stops without a passing review must leave durable, non-approvable evidence.

Drives the real planning control flow (real Git repo, only agent answers and CAO
transport simulated) for every stop cause, in both the modular and bundled forms.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_workflow_integration as harness

ROOT = harness.ROOT
TICKET = 'T-1'


def finding(disposition, finding_id='P1', impact='LOW'):
    return {'id': finding_id, 'impact': impact, 'category': 'TESTING', 'disposition': disposition,
            'plan_section': 'Verification', 'description': f'{finding_id} needs attention',
            'evidence': ['app/tests/test_value.py'], 'required_action': 'Resolve it', 'confidence': 1.0}


def review(status, *findings, prompt=''):
    # Still blocked, so any earlier finding the prompt asks about is still unresolved.
    return {'review_status': status, 'summary': f'review is {status}', 'findings': list(findings),
            'prior_findings': harness.prior_findings_for(prompt, 'UNRESOLVED')}


def context_with_blocking_question():
    value = harness.context()
    value['open_questions'].append({
        'id': 'Q-1', 'question': 'Which behaviour?', 'reason': 'Not specified', 'blocking': True,
        'sources': [{'source_id': 'JIRA:T-1', 'location': 'AC-1'}]})
    return value


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class NonConvergedPlanningTest(unittest.TestCase):
    make_repo = harness.LifecycleIntegrationTest.make_repo
    drive = harness.LifecycleIntegrationTest.drive

    def run_planning(self, root, modular, responder, *, max_review_rounds=3, jira_available=True, run_id='nc-run-1'):
        repo, git = self.make_repo(root)
        source = root / 'sources'
        source.mkdir()
        if jira_available:
            (source / 'jira.md').write_text('Return three from value().')
        (source / 'context.json').write_text(json.dumps({'schema_version': '1.0', 'ticket': {
            'id': TICKET, 'source_id': 'JIRA:T-1', 'title': 'Request', 'file': 'jira.md', 'required': True},
            'confluence': []}))
        planning, transport = harness.load('dev_plan', modular)
        inputs = {'repository_root': str(repo), 'ticket_id': TICKET, 'source_dir': str(source),
                  'baseline_sha': git('rev-parse', 'HEAD'), 'max_review_rounds': max_review_rounds}
        calls, output = self.drive(planning, transport, inputs, responder(planning), run_id)
        return repo, planning, calls, output

    def assert_stopped(self, repo, output, stop_cause, run_id='nc-run-1'):
        """Common invariants of every non-converged stop; returns (candidate_dir, manifest, human_needed)."""
        self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_CLARIFICATION')
        self.assertEqual(output['reason'], stop_cause)
        candidate = repo / 'sdlc-records' / TICKET / 'candidates' / run_id
        self.assertEqual(Path(output['candidate_dir']), candidate)

        manifest = json.loads((candidate / 'candidate-manifest.json').read_text())
        self.assertEqual(manifest['state'], 'NOT_CONVERGED')
        self.assertEqual(manifest['stop_cause'], stop_cause)
        self.assertEqual((manifest['ticket_id'], manifest['run_id']), (TICKET, run_id))
        self.assertRegex(manifest['sources_sha256'], r'^[0-9a-f]{64}$')
        self.assertIsNone(manifest['guidance_sha256'])
        # Every recorded digest matches the file actually stored.
        self.assertIn('human-needed.json', manifest['files'])
        for relative, digest in manifest['files'].items():
            self.assertEqual(sha256(candidate / relative), digest, relative)

        human = json.loads((candidate / 'human-needed.json').read_text())
        self.assertEqual(human['stop_cause'], stop_cause)
        self.assertEqual(human['blockers'], output['blockers'])
        self.assertTrue(human['next_steps'])
        runtime_copy = repo / '.agentic-sdlc/runtime' / TICKET / run_id / 'human-needed.json'
        self.assertEqual(json.loads(runtime_copy.read_text()), human)

        # Nothing approvable was published, and the approval script refuses this ticket.
        records = repo / 'sdlc-records' / TICKET
        self.assertFalse((records / 'development-plan.md').exists())
        self.assertFalse((records / 'execution-manifest.json').exists())
        approval = subprocess.run(
            [sys.executable, str(ROOT / '.agentic-sdlc/scripts/approve_plan.py'), '--repository-root', str(repo),
             '--ticket-id', TICKET, '--decision', 'APPROVED', '--approved-by', 'test-fixture'],
            capture_output=True, text=True)
        self.assertNotEqual(approval.returncode, 0)
        self.assertFalse((records / 'plan-approval-record.json').exists())
        return candidate, manifest, human

    def test_convergence_limit_keeps_last_plan_and_every_review(self):
        def responder(planning):
            def respond(agent, step_id, prompt):
                if agent == planning.CONTEXT_NORMALIZER:
                    return harness.context()
                if agent == planning.PLANNING_ANALYST:
                    return 'Affected file: app/value.py'
                if agent == planning.PLAN_AUTHOR:
                    return f'# Development Plan\nauthored by {step_id}\n'
                return review('CHANGES_REQUIRED', finding('PLAN_CHANGE_REQUIRED'), finding('ADVISORY', 'P2'), prompt=prompt)
            return respond

        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, _, calls, output = self.run_planning(Path(temp), modular, responder, max_review_rounds=2)
                candidate, manifest, human = self.assert_stopped(repo, output, 'review_convergence_limit_reached')
                self.assertEqual(manifest['review_rounds'], 2)
                self.assertEqual(manifest['context_versions'], 1)
                self.assertIn('plan-author-r2-c1', (candidate / 'candidate-plan.md').read_text())
                self.assertEqual(manifest['plan_sha256'], sha256(candidate / 'candidate-plan.md'))
                self.assertEqual(sorted(p.name for p in (candidate / 'reviews').iterdir()),
                                 ['01-review-r1-c1.json', '02-review-r2-c1.json'])
                self.assertTrue((candidate / 'planning-context.json').is_file())
                self.assertTrue((candidate / 'planning-analysis.md').is_file())
                # Only blocking findings are listed; the advisory one is not.
                self.assertEqual([f['id'] for f in human['blocking_findings']], ['P1'])
                self.assertEqual(human['blocking_findings'][0]['required_action'], 'Resolve it')

    def test_human_decision_stops_immediately_with_the_deciding_finding(self):
        def responder(planning):
            def respond(agent, step_id, prompt):
                if agent == planning.CONTEXT_NORMALIZER:
                    return harness.context()
                if agent == planning.PLANNING_ANALYST:
                    return 'Affected file: app/value.py'
                if agent == planning.PLAN_AUTHOR:
                    return '# Development Plan\ndraft\n'
                return review('HUMAN_DECISION_REQUIRED', finding('HUMAN_DECISION_REQUIRED', 'D1', 'HIGH'))
            return respond

        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, _, calls, output = self.run_planning(Path(temp), modular, responder)
                candidate, manifest, human = self.assert_stopped(repo, output, 'plan_review_requires_human_decision')
                self.assertEqual(manifest['review_rounds'], 1)
                self.assertEqual([f['id'] for f in human['blocking_findings']], ['D1'])
                self.assertEqual(len([c for c in calls if c[0] == 'sdlc_plan_author']), 1)

    def test_unavailable_required_source_leaves_context_only_evidence(self):
        def responder(planning):
            def respond(agent, step_id, prompt):
                self.assertEqual(agent, planning.CONTEXT_NORMALIZER)
                return harness.context()
            return respond

        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, _, calls, output = self.run_planning(Path(temp), modular, responder, jira_available=False)
                candidate, manifest, human = self.assert_stopped(repo, output, 'context_not_ready')
                self.assertEqual(manifest['review_rounds'], 0)
                self.assertIsNone(manifest['plan_sha256'])
                self.assertFalse((candidate / 'candidate-plan.md').exists())
                self.assertFalse((candidate / 'reviews').exists())
                self.assertFalse((candidate / 'planning-analysis.md').exists())
                self.assertEqual(human['blockers'], ['required source unavailable: JIRA:T-1'])
                sources = json.loads((candidate / 'sources.json').read_text())
                self.assertEqual([(s['source_id'], s['status'], s['content_digest']) for s in sources],
                                 [('JIRA:T-1', 'UNAVAILABLE', None)])

    def test_renormalized_context_that_is_not_ready_keeps_the_plan_and_review(self):
        def responder(planning):
            def respond(agent, step_id, prompt):
                if agent == planning.CONTEXT_NORMALIZER:
                    return context_with_blocking_question() if step_id == 'context-normalize-v2' else harness.context()
                if agent == planning.PLANNING_ANALYST:
                    return 'Affected file: app/value.py'
                if agent == planning.PLAN_AUTHOR:
                    return '# Development Plan\ndraft\n'
                return review('CONTEXT_RENORMALIZATION_REQUIRED',
                              finding('CONTEXT_RENORMALIZATION_REQUIRED', 'C1', 'MEDIUM'))
            return respond

        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, _, calls, output = self.run_planning(Path(temp), modular, responder)
                candidate, manifest, human = self.assert_stopped(repo, output, 'renormalized_context_not_ready')
                self.assertEqual(manifest['context_versions'], 2)
                self.assertEqual(manifest['review_rounds'], 1)
                runtime = repo / '.agentic-sdlc/runtime' / TICKET / 'nc-run-1'
                self.assertEqual(manifest['planning_context_sha256'],
                                 sha256(runtime / 'context/normalized/planning-context-v2.json'))
                self.assertTrue((candidate / 'candidate-plan.md').is_file())
                # The analysis predates the re-normalized context, so it is deliberately not kept.
                self.assertFalse((candidate / 'planning-analysis.md').exists())
                self.assertIn('blocking open question Q-1', human['blockers'])

    def test_existing_candidate_is_never_overwritten(self):
        def responder(planning):
            def respond(agent, step_id, prompt):
                return harness.context()
            return respond

        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, planning, _, output = self.run_planning(Path(temp), modular, responder, jira_available=False)
                retrieval = {'sources': [{'source_id': 'JIRA:T-1', 'title': 'Request', 'type': 'JIRA',
                                          'required': True, 'status': 'UNAVAILABLE', 'content_digest': None}]}
                with self.assertRaisesRegex(planning.WorkflowContractError, 'candidate snapshot already exists'):
                    planning._snapshot_candidate(
                        repo=repo, ticket_id=TICKET, run_id='nc-run-1', base_branch='main', baseline_sha='0' * 40,
                        stop_cause='context_not_ready', blockers=[], retrieval=retrieval, runtime_dir=repo / 'scratch',
                        context_json=None, context_version=1, analysis_path=None, plan_path=None,
                        review_paths=[], last_review=None)


if __name__ == '__main__':
    unittest.main()
