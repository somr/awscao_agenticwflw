"""Warm start: continue a non-converged planning run from its candidate instead of starting over.

Runs the real planning control flow (real Git repo; only agent answers and CAO transport
simulated) in both the modular and bundled forms.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_workflow_integration as harness

ROOT = harness.ROOT
TICKET = 'T-1'
GUIDANCE = 'Decision D1: keep value() a pure function.\n'
GUIDANCE_2 = 'Decision D1: value() may cache its result.\n'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finding(finding_id, disposition='PLAN_CHANGE_REQUIRED', impact='LOW'):
    return {'id': finding_id, 'impact': impact, 'category': 'TESTING', 'disposition': disposition,
            'plan_section': 'Verification', 'description': f'{finding_id} needs attention',
            'evidence': ['app/tests/test_value.py'], 'required_action': 'Resolve it', 'confidence': 1.0}


class WarmStartBase(unittest.TestCase):
    make_repo = harness.LifecycleIntegrationTest.make_repo
    drive = harness.LifecycleIntegrationTest.drive

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prompts = []

    def prepare(self, modular, jira_available=True):
        self.repo, self.git = self.make_repo(self.root)
        self.source = self.root / 'sources'
        self.source.mkdir()
        if jira_available:
            (self.source / 'jira.md').write_text('Return three from value().')
        (self.source / 'context.json').write_text(json.dumps({'schema_version': '1.0', 'ticket': {
            'id': TICKET, 'source_id': 'JIRA:T-1', 'title': 'Request', 'file': 'jira.md', 'required': True},
            'confluence': []}))
        self.planning, self.transport = harness.load('dev_plan', modular)
        self.baseline = self.git('rev-parse', 'HEAD')

    def run_planning(self, run_id, reviewer, *, max_review_rounds=1, allow_early_agents=True, **extra):
        """Drive one planning run; `reviewer(step_id, prompt)` answers plan-review steps."""
        planning = self.planning

        def respond(agent, step_id, prompt):
            self.prompts.append((run_id, agent, step_id, prompt))
            if agent in (planning.CONTEXT_NORMALIZER, planning.PLANNING_ANALYST) and not allow_early_agents:
                raise AssertionError(f'{agent} must not run on a warm start')
            if agent == planning.CONTEXT_NORMALIZER:
                return harness.context()
            if agent == planning.PLANNING_ANALYST:
                return 'Affected file: app/value.py'
            if agent == planning.PLAN_AUTHOR:
                return f'# Development Plan\nauthored by {step_id}\n'
            return reviewer(step_id, prompt)

        inputs = {'repository_root': str(self.repo), 'ticket_id': TICKET, 'source_dir': str(self.source),
                  'baseline_sha': self.baseline, 'max_review_rounds': max_review_rounds, **extra}
        return self.drive(planning, self.transport, inputs, respond, run_id)

    @staticmethod
    def blocked(step_id, prompt, disposition='PLAN_CHANGE_REQUIRED'):
        status = 'HUMAN_DECISION_REQUIRED' if disposition == 'HUMAN_DECISION_REQUIRED' else 'CHANGES_REQUIRED'
        return {'review_status': status, 'summary': 'not yet', 'findings': [finding('P1', disposition)],
                'prior_findings': harness.prior_findings_for(prompt, 'UNRESOLVED')}

    @staticmethod
    def passing(step_id, prompt, status='RESOLVED'):
        return {'review_status': 'PASS', 'summary': 'ready', 'findings': [],
                'prior_findings': harness.prior_findings_for(prompt, status)}

    def cold_candidate(self, run_id='cold-run', disposition='PLAN_CHANGE_REQUIRED', **extra):
        _, output = self.run_planning(run_id, lambda s, p: self.blocked(s, p, disposition), **extra)
        self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_CLARIFICATION')
        return Path(output['candidate_dir'])

    def write_guidance(self, text=GUIDANCE, name='guidance.md'):
        path = self.repo / 'agentic-sdlc-records' / TICKET / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return str(path.relative_to(self.repo))

    def prompt_for(self, run_id, step_id):
        return next(p for r, _, s, p in self.prompts if r == run_id and s == step_id)


class WarmStartFlowTest(WarmStartBase):
    def test_continues_from_the_candidate_without_repeating_normalize_or_analysis(self):
        for modular in (False, True):
            with self.subTest(modular=modular):
                self.setUp()
                self.prepare(modular)
                candidate = self.cold_candidate()
                guidance = self.write_guidance()
                calls, output = self.run_planning(
                    'warm-run', lambda s, p: self.passing(s, p, 'RESOLVED_BY_GUIDANCE'),
                    allow_early_agents=False, resume_from=str(candidate), guidance_file=guidance)

                # Only a revision author round and one review ran.
                self.assertEqual(calls, [('sdlc_plan_author', 'plan-author-warm-c1'), ('sdlc_plan_reviewer', 'plan-review-r1-c1')])
                runtime = self.repo / '.agentic-sdlc/runtime' / TICKET / 'warm-run'
                history = runtime / 'planning/history'
                author = self.prompt_for('warm-run', 'plan-author-warm-c1')
                self.assertIn(f'Previous plan: {history}/previous-plan.md', author)
                self.assertIn(f'Reviewer findings: {history}/01-review-r1-c1.json', author)
                self.assertIn(f'Developer guidance: {runtime}/guidance/developer-guidance.md', author)
                self.assertEqual((history / 'previous-plan.md').read_bytes(), (candidate / 'candidate-plan.md').read_bytes())
                self.assertEqual((history / '01-review-r1-c1.json').read_bytes(),
                                 (candidate / 'reviews/01-review-r1-c1.json').read_bytes())

                # The reviewer sees the candidate's review as history (r1) and is asked about its blocking finding.
                reviewer = self.prompt_for('warm-run', 'plan-review-r1-c1')
                self.assertIn(f'- r1: {history}/01-review-r1-c1.json', reviewer)
                self.assertIn('exactly these refs: r1:P1.', reviewer)

                # Published with lineage that binds the candidate it continued from.
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_APPROVAL')
                lineage = output['resumed_from']
                self.assertEqual(lineage['run_id'], 'cold-run')
                self.assertEqual(lineage['candidate_manifest_sha256'], sha256(candidate / 'candidate-manifest.json'))
                self.assertEqual(lineage['prior_review_rounds'], 1)
                records = self.repo / 'agentic-sdlc-records' / TICKET
                manifest = json.loads((records / 'execution-manifest.json').read_text())
                self.assertEqual((manifest['review_rounds'], manifest['total_review_rounds']), (1, 2))
                self.assertEqual(manifest['resumed_from'], lineage)
                self.assertEqual(manifest['guidance_sha256'], hashlib.sha256(GUIDANCE.encode()).hexdigest())
                self.assertIn('plan-author-warm-c1', (records / 'development-plan.md').read_text())
                # The candidate itself is left untouched.
                self.assertTrue((candidate / 'candidate-manifest.json').is_file())
                approval = subprocess.run(
                    [sys.executable, str(ROOT / '.agentic-sdlc/scripts/approve_plan.py'), '--repository-root', str(self.repo),
                     '--ticket-id', TICKET, '--decision', 'APPROVED', '--approved-by', 'test-fixture'],
                    capture_output=True, text=True)
                self.assertEqual(approval.returncode, 0, approval.stderr)

    def test_a_round_limit_stop_can_simply_continue_without_guidance(self):
        for modular in (False, True):
            with self.subTest(modular=modular):
                self.setUp()
                self.prepare(modular)
                candidate = self.cold_candidate()
                relative = str(candidate.relative_to(self.repo))  # relative paths resolve against the repository
                _, output = self.run_planning('warm-run', self.passing, resume_from=relative)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_APPROVAL')
                self.assertIsNone(output['guidance_sha256'])

    def test_a_human_decision_stop_needs_guidance_that_changed(self):
        for modular in (False, True):
            with self.subTest(modular=modular):
                self.setUp()
                self.prepare(modular)
                first = self.write_guidance()
                candidate = self.cold_candidate(disposition='HUMAN_DECISION_REQUIRED', guidance_file=first)
                self.assertEqual(json.loads((candidate / 'candidate-manifest.json').read_text())['stop_cause'],
                                 'plan_review_requires_human_decision')
                run = lambda run_id, **extra: self.run_planning(  # noqa: E731
                    run_id, lambda s, p: self.passing(s, p, 'RESOLVED_BY_GUIDANCE'), allow_early_agents=False,
                    resume_from=str(candidate), **extra)
                with self.assertRaisesRegex(self.planning.WorkflowContractError, 'supply guidance_file'):
                    run('warm-none')
                with self.assertRaisesRegex(self.planning.WorkflowContractError, 'unchanged since the candidate'):
                    run('warm-same', guidance_file=first)
                _, output = run('warm-changed', guidance_file=self.write_guidance(GUIDANCE_2, 'guidance-2.md'))
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_APPROVAL')
                self.assertEqual(output['resumed_from']['prior_guidance_sha256'], hashlib.sha256(GUIDANCE.encode()).hexdigest())

    def test_history_keeps_growing_across_consecutive_warm_starts(self):
        for modular in (False, True):
            with self.subTest(modular=modular):
                self.setUp()
                self.prepare(modular)
                first = self.cold_candidate('run-1')
                second_output = self.run_planning('run-2', lambda s, p: self.blocked(s, p), resume_from=str(first))[1]
                second = Path(second_output['candidate_dir'])
                self.assertEqual(second_output['reason'], 'review_convergence_limit_reached')
                names = sorted(p.name for p in (second / 'reviews').iterdir())
                self.assertEqual(names, ['01-review-r1-c1.json', '02-review-r1-c1.json'])
                manifest = json.loads((second / 'candidate-manifest.json').read_text())
                self.assertEqual(manifest['review_rounds'], 2)
                self.assertEqual(manifest['resumed_from']['run_id'], 'run-1')

                self.run_planning('run-3', self.passing, resume_from=str(second))
                reviewer = self.prompt_for('run-3', 'plan-review-r1-c1')
                history = self.repo / '.agentic-sdlc/runtime' / TICKET / 'run-3/planning/history'
                self.assertIn(f'- r1: {history}/01-review-r1-c1.json', reviewer)
                self.assertIn(f'- r2: {history}/02-review-r1-c1.json', reviewer)
                self.assertIn('exactly these refs: r2:P1.', reviewer)  # the latest review's refs, indexed by position


class WarmStartRefusalTest(WarmStartBase):
    def refuse(self, message, mutate, *, modular, cold_kwargs=None, run_kwargs=None):
        self.setUp()
        self.prepare(modular, jira_available=(cold_kwargs or {}).pop('jira_available', True))
        candidate = self.cold_candidate(**(cold_kwargs or {}))
        target = mutate(candidate) or candidate
        kwargs = {'resume_from': str(target), **(run_kwargs or {})}
        with self.assertRaisesRegex(self.planning.WorkflowContractError, message):
            self.run_planning('warm-run', self.passing, allow_early_agents=False, **kwargs)
        # Nothing was published or snapshotted by the refused run.
        self.assertFalse((self.repo / 'agentic-sdlc-records' / TICKET / 'development-plan.md').exists())
        self.assertFalse((self.repo / 'agentic-sdlc-records' / TICKET / 'candidates' / 'warm-run').exists())

    def test_fails_closed(self):
        def tamper_plan(candidate):
            (candidate / 'candidate-plan.md').write_text('a different plan')

        def tamper_review(candidate):
            (candidate / 'reviews/01-review-r1-c1.json').write_text('{}')

        def change_state(candidate):
            path = candidate / 'candidate-manifest.json'
            manifest = json.loads(path.read_text())
            manifest['state'] = 'APPROVED'
            path.write_text(json.dumps(manifest))

        def unsafe_file(candidate):
            path = candidate / 'candidate-manifest.json'
            manifest = json.loads(path.read_text())
            manifest['files']['../outside.md'] = '0' * 64
            path.write_text(json.dumps(manifest))

        def drop_manifest(candidate):
            (candidate / 'candidate-manifest.json').unlink()

        def other_ticket(candidate):
            target = self.repo / 'agentic-sdlc-records/OTHER/candidates' / candidate.name
            shutil.copytree(candidate, target)
            return target

        def not_a_candidate(candidate):
            return candidate.parent.parent

        def legacy_review_names(candidate):
            # A candidate written before reviews were named by history position: rewrite it consistently.
            path = candidate / 'candidate-manifest.json'
            manifest = json.loads(path.read_text())
            old, new = 'reviews/01-review-r1-c1.json', 'reviews/review-r1-c1.json'
            (candidate / old).rename(candidate / new)
            manifest['files'][new] = manifest['files'].pop(old)
            path.write_text(json.dumps(manifest))

        def change_sources(candidate):
            (self.source / 'jira.md').write_text('Return four from value().')

        cases = [
            ('was modified since it was written: candidate-plan.md', tamper_plan, {}, {}),
            ('was modified since it was written: reviews/01-review-r1-c1.json', tamper_review, {}, {}),
            ('is not a NOT_CONVERGED candidate', change_state, {}, {}),
            ('missing or unsafe', unsafe_file, {}, {}),
            ('no candidate-manifest.json', drop_manifest, {}, {}),
            ('must be a candidate directory of this ticket', other_ticket, {}, {}),
            ('must be a candidate directory of this ticket', not_a_candidate, {}, {}),
            ('not named by history position', legacy_review_names, {}, {}),
            ('retrieved sources differ', change_sources, {}, {}),
            ('baseline_sha differs', lambda c: None, {}, {'baseline_sha': '0' * 40}),
            ("cannot warm start from stop cause 'context_not_ready'", lambda c: None, {'jira_available': False}, {}),
        ]
        for modular in (False, True):
            for message, mutate, cold_kwargs, run_kwargs in cases:
                with self.subTest(modular=modular, case=message):
                    self.refuse(message, mutate, modular=modular, cold_kwargs=dict(cold_kwargs), run_kwargs=run_kwargs)


if __name__ == '__main__':
    unittest.main()
