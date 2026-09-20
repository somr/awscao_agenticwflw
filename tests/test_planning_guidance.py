"""Developer guidance: validation, delivery to the right agents, and binding to the plan and approval.

Runs the real planning control flow (real Git repo; agent answers and CAO transport
simulated) in both the modular and bundled forms.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import test_workflow_integration as harness

ROOT = harness.ROOT
TICKET = 'T-1'
GUIDANCE_TEXT = 'Decision: keep value() a pure function.\nConstraint: do not add a dependency.\n'


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def finding(disposition='PLAN_CHANGE_REQUIRED'):
    return {'id': 'P1', 'impact': 'LOW', 'category': 'TESTING', 'disposition': disposition,
            'plan_section': 'Verification', 'description': 'P1 needs attention',
            'evidence': ['app/tests/test_value.py'], 'required_action': 'Resolve it', 'confidence': 1.0}


def approve(repo, *extra):
    return subprocess.run(
        [sys.executable, str(ROOT / '.agentic-sdlc/scripts/approve_plan.py'), '--repository-root', str(repo),
         '--ticket-id', TICKET, '--decision', 'APPROVED', '--approved-by', 'test-fixture', *extra],
        capture_output=True, text=True)


class GuidanceTest(unittest.TestCase):
    make_repo = harness.LifecycleIntegrationTest.make_repo
    drive = harness.LifecycleIntegrationTest.drive

    def run_planning(self, root, modular, *, guidance=None, converge_at=2, max_review_rounds=3, guidance_text=GUIDANCE_TEXT):
        """Run planning; converge_at is the review round that passes (None: never)."""
        repo, git = self.make_repo(root)
        source = root / 'sources'
        source.mkdir()
        (source / 'jira.md').write_text('Return three from value().')
        (source / 'context.json').write_text(json.dumps({'schema_version': '1.0', 'ticket': {
            'id': TICKET, 'source_id': 'JIRA:T-1', 'title': 'Request', 'file': 'jira.md', 'required': True},
            'confluence': []}))
        if guidance is not None:
            target = repo / guidance
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(guidance_text)
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
            round_number = int(re.search(r'-r(\d+)-c\d+', step_id).group(1))
            if converge_at is not None and round_number >= converge_at:
                return {'review_status': 'PASS', 'summary': 'ready', 'findings': [],
                        'prior_findings': harness.prior_findings_for(prompt)}
            return {'review_status': 'CHANGES_REQUIRED', 'summary': 'changes', 'findings': [finding()],
                    'prior_findings': harness.prior_findings_for(prompt, 'UNRESOLVED')}

        inputs = {'repository_root': str(repo), 'ticket_id': TICKET, 'source_dir': str(source),
                  'baseline_sha': git('rev-parse', 'HEAD'), 'max_review_rounds': max_review_rounds}
        if guidance is not None:
            inputs['guidance_file'] = guidance
        _, output = self.drive(planning, transport, inputs, respond, 'guided-run')
        return repo, planning, prompts, output

    def test_guidance_reaches_analyst_author_and_reviewer_but_not_normalizer(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, _, prompts, output = self.run_planning(
                    Path(temp), modular, guidance='agentic-sdlc-records/T-1/guidance.md')
                frozen = repo / '.agentic-sdlc/runtime' / TICKET / 'guided-run/guidance/developer-guidance.md'
                self.assertEqual(frozen.read_text(), GUIDANCE_TEXT)
                seen = {}
                for agent, step_id, prompt in prompts:
                    has = f'Developer guidance: {frozen}' in prompt
                    seen.setdefault(agent, set()).add(has)
                    if agent == 'sdlc_context_normalizer':
                        self.assertNotIn('Developer guidance', prompt)
                    else:
                        self.assertTrue(has, f'{agent}/{step_id} was not given the guidance')
                self.assertEqual(sorted(seen), ['sdlc_context_normalizer', 'sdlc_plan_author',
                                                'sdlc_plan_reviewer', 'sdlc_planning_analyst'])
                # The revision round (r2) is included, not just the first authoring round.
                self.assertIn('plan-author-r2-c1', [s for a, s, _ in prompts])
                reviewer_prompt = next(p for a, s, p in prompts if a == 'sdlc_plan_reviewer')
                self.assertIn('HUMAN_DECISION_REQUIRED', reviewer_prompt)

    def test_guidance_is_bound_to_the_published_plan_and_the_approval(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, _, _, output = self.run_planning(Path(temp), modular, guidance='agentic-sdlc-records/T-1/guidance.md')
                digest = sha256_bytes(GUIDANCE_TEXT.encode())
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_APPROVAL')
                self.assertEqual(output['guidance_sha256'], digest)
                records = repo / 'agentic-sdlc-records' / TICKET
                self.assertEqual((records / 'plan-guidance.md').read_text(), GUIDANCE_TEXT)
                manifest = json.loads((records / 'execution-manifest.json').read_text())
                self.assertEqual(manifest['guidance_sha256'], digest)

                # Editing the recorded guidance after review invalidates the approval.
                (records / 'plan-guidance.md').write_text(GUIDANCE_TEXT + 'Decision: also skip verification.\n')
                refused = approve(repo)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn('guidance', refused.stderr)
                self.assertFalse((records / 'plan-approval-record.json').exists())

                (records / 'plan-guidance.md').write_text(GUIDANCE_TEXT)
                self.assertEqual(approve(repo).returncode, 0)
                approval = json.loads((records / 'plan-approval-record.json').read_text())
                self.assertEqual(approval['guidance_sha256'], digest)

    def test_a_run_without_guidance_is_unchanged_and_unbound(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, _, prompts, output = self.run_planning(Path(temp), modular)
                self.assertTrue(all('Developer guidance' not in p for _, _, p in prompts))
                self.assertIsNone(output['guidance_sha256'])
                records = repo / 'agentic-sdlc-records' / TICKET
                self.assertFalse((records / 'plan-guidance.md').exists())
                self.assertIsNone(json.loads((records / 'execution-manifest.json').read_text())['guidance_sha256'])
                # A stray guidance file next to an unguided plan must not be approvable.
                (records / 'plan-guidance.md').write_text(GUIDANCE_TEXT)
                self.assertNotEqual(approve(repo).returncode, 0)

    def test_non_converged_candidate_keeps_the_guidance_that_was_used(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, _, _, output = self.run_planning(
                    Path(temp), modular, guidance='agentic-sdlc-records/T-1/guidance.md', converge_at=None, max_review_rounds=1)
                self.assertEqual(output['reason'], 'review_convergence_limit_reached')
                candidate = Path(output['candidate_dir'])
                self.assertEqual((candidate / 'guidance.md').read_text(), GUIDANCE_TEXT)
                manifest = json.loads((candidate / 'candidate-manifest.json').read_text())
                self.assertEqual(manifest['guidance_sha256'], sha256_bytes(GUIDANCE_TEXT.encode()))
                self.assertEqual(manifest['files']['guidance.md'], manifest['guidance_sha256'])

    def test_invalid_guidance_stops_the_run_before_any_agent_starts(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                planning, transport = harness.load('dev_plan', modular)
                with self.assertRaisesRegex(planning.WorkflowContractError, 'guidance_file does not exist'):
                    self._run_with_missing_guidance(Path(temp), planning, transport)

    def _run_with_missing_guidance(self, root, planning, transport):
        repo, git = self.make_repo(root)
        source = root / 'sources'
        source.mkdir()
        (source / 'context.json').write_text('{}')
        inputs = {'repository_root': str(repo), 'ticket_id': TICKET, 'source_dir': str(source),
                  'baseline_sha': git('rev-parse', 'HEAD'), 'guidance_file': 'agentic-sdlc-records/T-1/missing.md'}

        def respond(agent, step_id, prompt):
            raise AssertionError('no agent may start when guidance_file is invalid')
        self.drive(planning, transport, inputs, respond, 'guided-run')

    def test_resolve_guidance_validation(self):
        for modular in (False, True):
            planning, _ = harness.load('dev_plan', modular)
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo = Path(temp, 'repo').resolve()
                (repo / 'agentic-sdlc-records/T-1').mkdir(parents=True)
                (repo / '.agentic-sdlc/runtime/T-1').mkdir(parents=True)
                outside = Path(temp, 'outside.md')
                outside.write_text('outside guidance')
                good = repo / 'agentic-sdlc-records/T-1/guidance.md'
                good.write_text(GUIDANCE_TEXT)
                (repo / 'agentic-sdlc-records/T-1/link.md').symlink_to(outside)
                (repo / 'agentic-sdlc-records/T-1/binary.md').write_bytes(b'\xff\xfe\x00bad')
                (repo / 'agentic-sdlc-records/T-1/empty.md').write_text('  \n')
                (repo / 'agentic-sdlc-records/T-1/huge.md').write_text('x' * (planning.MAX_GUIDANCE_BYTES + 1))
                (repo / '.agentic-sdlc/runtime/T-1/agent-written.md').write_text('agent wrote this')

                self.assertIsNone(planning.resolve_guidance(repo, None))
                self.assertIsNone(planning.resolve_guidance(repo, ''))
                self.assertEqual(planning.resolve_guidance(repo, 'agentic-sdlc-records/T-1/guidance.md'), good)
                self.assertEqual(planning.resolve_guidance(repo, str(good)), good)

                rejected = {
                    'agentic-sdlc-records/T-1/nope.md': 'does not exist',
                    'agentic-sdlc-records/T-1': 'not a regular file',
                    '../outside.md': 'inside repository_root',
                    str(outside): 'inside repository_root',
                    'agentic-sdlc-records/T-1/link.md': 'inside repository_root',
                    '.agentic-sdlc/runtime/T-1/agent-written.md': 'agent-writable',
                    'agentic-sdlc-records/T-1/huge.md': 'exceeds',
                    'agentic-sdlc-records/T-1/binary.md': 'not valid UTF-8',
                    'agentic-sdlc-records/T-1/empty.md': 'is empty',
                }
                for value, message in rejected.items():
                    with self.subTest(value=value):
                        with self.assertRaisesRegex(planning.WorkflowContractError, message):
                            planning.resolve_guidance(repo, value)


if __name__ == '__main__':
    unittest.main()
