"""The plan reviewer sees earlier reviews and must account for each earlier blocking finding.

Covers the validator rules, what each review round's prompt contains, and the repair path,
using the real planning control flow in both the modular and bundled forms.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import tempfile
import unittest

import test_workflow_integration as harness

TICKET = 'T-1'


def finding(finding_id, disposition='PLAN_CHANGE_REQUIRED'):
    return {'id': finding_id, 'impact': 'LOW', 'category': 'TESTING', 'disposition': disposition,
            'plan_section': 'Verification', 'description': f'{finding_id} needs attention',
            'evidence': ['app/tests/test_value.py'], 'required_action': 'Resolve it', 'confidence': 1.0}


def base_review(status='CHANGES_REQUIRED', *, prior=None, findings=None):
    value = {'review_status': status, 'summary': 'summary',
             'findings': [finding('P9')] if findings is None else findings}
    if prior is not None:
        value['prior_findings'] = prior
    return value


def entry(ref, status='RESOLVED'):
    return {'ref': ref, 'status': status, 'note': 'checked'}


class ValidatorTest(unittest.TestCase):
    def test_rules(self):
        for modular in (False, True):
            planning, _ = harness.load('dev_plan', modular)
            error = planning.WorkflowContractError
            validate = planning.validate_review
            refs = ['r1:P1', 'r1:P2']
            with self.subTest(modular=modular):
                # Backward compatible: no history means no prior_findings.
                validate(base_review())
                validate(base_review(prior=[]))
                # Fully accounted for, each status allowed.
                validate(base_review(prior=[entry('r1:P1'), entry('r1:P2', 'UNRESOLVED')]), refs)
                validate(base_review('PASS', prior=[entry('r1:P1'), entry('r1:P2', 'NOT_APPLICABLE')], findings=[]), refs)
                validate(base_review(prior=[entry('r1:P1', 'RESOLVED_BY_GUIDANCE'), entry('r1:P2')]), refs, True)

                rejected = {
                    'is required and must account for': (base_review(), refs, False),
                    'does not account for: r1:P2': (base_review(prior=[entry('r1:P1')]), refs, False),
                    'unknown finding': (base_review(prior=[entry('r1:P1'), entry('r1:P2'), entry('r1:P7')]), refs, False),
                    'more than once': (base_review(prior=[entry('r1:P1'), entry('r1:P1'), entry('r1:P2')]), refs, False),
                    'invalid prior finding status': (base_review(prior=[entry('r1:P1', 'FIXED'), entry('r1:P2')]), refs, False),
                    'no developer guidance was supplied': (
                        base_review(prior=[entry('r1:P1', 'RESOLVED_BY_GUIDANCE'), entry('r1:P2')]), refs, False),
                    'conflicts with an UNRESOLVED prior finding': (
                        base_review('PASS', prior=[entry('r1:P1', 'UNRESOLVED'), entry('r1:P2')], findings=[]), refs, False),
                    'must be empty': (base_review(prior=[entry('r1:P1')]), [], False),
                    'missing required keys': (base_review(prior=[{'ref': 'r1:P1', 'status': 'RESOLVED'}, entry('r1:P2')]), refs, False),
                }
                for message, (value, required, guidance) in rejected.items():
                    with self.subTest(message=message):
                        with self.assertRaisesRegex(error, message):
                            validate(value, required, guidance)


class ReviewerHistoryFlowTest(unittest.TestCase):
    make_repo = harness.LifecycleIntegrationTest.make_repo
    drive = harness.LifecycleIntegrationTest.drive

    def run_planning(self, root, modular, review_for, *, max_review_rounds=3):
        repo, git = self.make_repo(root)
        source = root / 'sources'
        source.mkdir()
        (source / 'jira.md').write_text('Return three from value().')
        (source / 'context.json').write_text(json.dumps({'schema_version': '1.0', 'ticket': {
            'id': TICKET, 'source_id': 'JIRA:T-1', 'title': 'Request', 'file': 'jira.md', 'required': True},
            'confluence': []}))
        planning, transport = harness.load('dev_plan', modular)
        prompts = []

        def respond(agent, step_id, prompt):
            prompts.append((agent, step_id, prompt))
            if agent == planning.CONTEXT_NORMALIZER:
                return harness.context()
            if agent == planning.PLANNING_ANALYST:
                return 'Affected file: app/value.py'
            if agent == planning.PLAN_AUTHOR:
                return f'# Development Plan\nauthored by {step_id}\n'
            return review_for(step_id, prompt)

        inputs = {'repository_root': str(repo), 'ticket_id': TICKET, 'source_dir': str(source),
                  'baseline_sha': git('rev-parse', 'HEAD'), 'max_review_rounds': max_review_rounds}
        _, output = self.drive(planning, transport, inputs, respond, 'history-run')
        return repo, planning, prompts, output

    def reviewer_prompts(self, prompts):
        return [(s, p) for a, s, p in prompts if a == 'sdlc_plan_reviewer']

    def test_each_round_sees_the_earlier_reviews_and_is_asked_for_the_latest_blocking_refs(self):
        def review_for(step_id, prompt):
            round_number = int(re.search(r'-r(\d+)-c\d+', step_id).group(1))
            if round_number >= 3:
                return {'review_status': 'PASS', 'summary': 'ready', 'findings': [],
                        'prior_findings': harness.prior_findings_for(prompt)}
            findings = [finding(f'P{round_number}'), finding(f'A{round_number}', 'ADVISORY')]
            return {'review_status': 'CHANGES_REQUIRED', 'summary': 'changes', 'findings': findings,
                    'prior_findings': harness.prior_findings_for(prompt)}

        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, _, prompts, output = self.run_planning(Path(temp), modular, review_for)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_APPROVAL')
                self.assertEqual(output['review_rounds'], 3)
                (_, first), (_, second), (_, third) = self.reviewer_prompts(prompts)
                runtime = repo / '.agentic-sdlc/runtime' / TICKET / 'history-run/planning'

                self.assertNotIn('Previous reviews', first)
                self.assertNotIn('prior_findings', first)

                self.assertIn(f'- r1: {runtime}/review-r1-c1.json', second)
                self.assertNotIn('r2:', second)
                self.assertIn('exactly these refs: r1:P1.', second)  # blocking only, not the advisory A1

                self.assertIn(f'- r1: {runtime}/review-r1-c1.json', third)
                self.assertIn(f'- r2: {runtime}/review-r2-c1.json', third)
                self.assertIn('exactly these refs: r2:P2.', third)  # latest review only

    def test_a_reviewer_that_omits_prior_findings_gets_one_repair_turn(self):
        def review_for(step_id, prompt):
            round_number = int(re.search(r'-r(\d+)-c\d+', step_id).group(1))
            if round_number == 1:
                return {'review_status': 'CHANGES_REQUIRED', 'summary': 'changes', 'findings': [finding('P1')]}
            answer = {'review_status': 'PASS', 'summary': 'ready', 'findings': []}
            if step_id.endswith('-repair-1'):
                answer['prior_findings'] = harness.prior_findings_for(prompt) or [entry('r1:P1')]
            return answer

        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                _, _, prompts, output = self.run_planning(Path(temp), modular, review_for)
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_APPROVAL')
                steps = [step for step, _ in self.reviewer_prompts(prompts)]
                self.assertEqual(steps, ['plan-review-r1-c1', 'plan-review-r2-c1', 'plan-review-r2-c1-repair-1'])

    def test_a_reviewer_that_keeps_omitting_it_fails_the_contract(self):
        def review_for(step_id, prompt):
            round_number = int(re.search(r'-r(\d+)-c\d+', step_id).group(1))
            if round_number == 1:
                return {'review_status': 'CHANGES_REQUIRED', 'summary': 'changes', 'findings': [finding('P1')]}
            return {'review_status': 'PASS', 'summary': 'ready', 'findings': []}

        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                planning, transport = harness.load('dev_plan', modular)
                with self.assertRaisesRegex(planning.WorkflowContractError,
                                            'failed its JSON contract.*prior_findings is required'):
                    self.run_planning_with(Path(temp), planning, transport, review_for)

    def run_planning_with(self, root, planning, transport, review_for):
        """Same flow as run_planning but with an already loaded module (exception classes must match)."""
        repo, git = self.make_repo(root)
        source = root / 'sources'
        source.mkdir()
        (source / 'jira.md').write_text('Return three from value().')
        (source / 'context.json').write_text(json.dumps({'schema_version': '1.0', 'ticket': {
            'id': TICKET, 'source_id': 'JIRA:T-1', 'title': 'Request', 'file': 'jira.md', 'required': True},
            'confluence': []}))

        def respond(agent, step_id, prompt):
            if agent == planning.CONTEXT_NORMALIZER:
                return harness.context()
            if agent == planning.PLANNING_ANALYST:
                return 'Affected file: app/value.py'
            if agent == planning.PLAN_AUTHOR:
                return f'# Development Plan\nauthored by {step_id}\n'
            return review_for(step_id, prompt)

        inputs = {'repository_root': str(repo), 'ticket_id': TICKET, 'source_dir': str(source),
                  'baseline_sha': git('rev-parse', 'HEAD'), 'max_review_rounds': 3}
        self.drive(planning, transport, inputs, respond, 'history-run')


if __name__ == '__main__':
    unittest.main()
