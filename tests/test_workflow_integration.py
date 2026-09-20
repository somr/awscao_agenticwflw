"""Exercise full planning/delivery control flow with real Git and verification.

Only agent responses and CAO terminal transport are simulated. Both modular
source and deployment bundles run the same orchestration and artifact checks.
"""
from __future__ import annotations
from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic-sdlc/cao'))
shim = types.ModuleType('cao_workflow')
shim.step = lambda *args, **kwargs: None
shim.get_inputs = lambda: {}
shim.emit_output = lambda value: None
sys.modules.setdefault('cao_workflow', shim)
from build_workflow import build_source


def load(name, modular):
    if modular:
        domain = importlib.import_module('sdlc_workflows.' + {'dev_plan': 'planning', 'deliver': 'delivery'}[name])
        return domain, importlib.import_module('sdlc_workflows.runtime')
    mod = types.ModuleType(name + '_integration')
    exec(build_source(name), mod.__dict__)
    return mod, mod


def prior_findings_for(prompt, status='RESOLVED'):
    """Answer the reviewer's prior_findings request that the prompt makes (empty on a first review)."""
    match = re.search(r'exactly these refs: (.+?)\. Use status', prompt)
    refs = match.group(1).split(', ') if match else []
    return [{'ref': ref, 'status': status, 'note': 'checked against the plan'} for ref in refs]


def context():
    ref = [{'source_id': 'JIRA:T-1', 'location': 'AC-1'}]
    return {'schema_version': '1.0', 'ticket': {'id': 'T-1', 'summary': 'Return a value'},
            'sources': [{'source_id': 'JIRA:T-1', 'type': 'JIRA', 'title': 'Request', 'status': 'RETRIEVED', 'content_digest': None}],
            'problem_statement': {'text': 'Return the requested value', 'sources': ref},
            'scope': {'in': [{'text': 'value function', 'sources': ref}], 'out': [], 'uncertain': []},
            'acceptance_criteria': [{'id': 'AC-1', 'text': 'Return three', 'origin': 'EXPLICIT', 'sources': ref}],
            'functional_requirements': [], 'non_functional_requirements': [], 'constraints': [],
            'dependencies': [], 'open_questions': [], 'contradictions': [], 'retrieval_warnings': []}


class LifecycleIntegrationTest(unittest.TestCase):
    def make_repo(self, root):
        repo = root / 'repo'
        repo.mkdir()
        sdlc = repo / '.agentic-sdlc'
        for directory in ('contracts', 'policies', 'schemas', 'templates', 'cao'):
            shutil.copytree(ROOT / '.agentic-sdlc' / directory, sdlc / directory)
        (repo / 'app/tests').mkdir(parents=True)
        (repo / 'app/tests/__init__.py').write_text('')
        (repo / 'app/value.py').write_text('def value():\n    return 0\n')
        (repo / 'app/tests/test_value.py').write_text('import unittest\nfrom value import value\nclass ValueTest(unittest.TestCase):\n    def test_value(self):\n        self.assertGreaterEqual(value(), 2)\n')
        (repo / '.gitignore').write_text('__pycache__/\n.agentic-sdlc/runtime/\n')
        def git(*args):
            return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
        git('init', '-q', '-b', 'main')
        git('config', 'user.email', 'fixture@example.invalid')
        git('config', 'user.name', 'Fixture')
        git('add', '.')
        git('commit', '-qm', 'Baseline')
        return repo, git

    def drive(self, domain, transport, inputs, responder, run_id):
        calls, outputs = [], []
        def step(provider, agent, prompt, **kwargs):
            calls.append((agent, kwargs['step_id']))
            self.assertEqual(kwargs['recovery'], 'idempotent')
            self.assertFalse(kwargs['teardown'])
            self.assertEqual(kwargs['working_directory'], inputs['repository_root'])
            answer = responder(agent, kwargs['step_id'], prompt)
            path = Path(re.findall(r'\n(/[^\n]+\.answer\.(?:json|md))\n', prompt)[-1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(answer) if isinstance(answer, dict) else answer)
            return types.SimpleNamespace(terminal_id=kwargs['step_id'], replayed=False)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'CAO_WORKFLOW_RUN_ID': run_id}))
            stack.enter_context(patch.object(domain, 'get_inputs', return_value=inputs))
            stack.enter_context(patch.object(domain, 'emit_output', side_effect=outputs.append))
            stack.enter_context(patch.object(transport, 'step', side_effect=step))
            stack.enter_context(patch.object(transport, '_wait', return_value=None))
            stack.enter_context(patch.object(transport, '_cao_terminal_status', return_value='completed'))
            cleanup = stack.enter_context(patch.object(transport, '_cleanup_step_terminal'))
            domain.main()
            self.assertEqual(cleanup.call_count, len(calls))
        return calls, outputs[-1]

    def plan(self, root, modular):
        repo, git = self.make_repo(root)
        source = root / 'sources'
        source.mkdir()
        (source / 'jira.md').write_text('Return three from value().')
        (source / 'context.json').write_text(json.dumps({'schema_version': '1.0', 'ticket': {
            'id': 'T-1', 'source_id': 'JIRA:T-1', 'title': 'Request', 'file': 'jira.md', 'required': True}, 'confluence': []}))
        planning, transport = load('dev_plan', modular)
        inputs = {'repository_root': str(repo), 'ticket_id': 'T-1', 'source_dir': str(source), 'baseline_sha': git('rev-parse', 'HEAD')}
        def respond(agent, step_id, prompt):
            if agent == planning.CONTEXT_NORMALIZER:
                return '{invalid' if step_id == 'context-normalize-v1' else context()
            if agent == planning.PLANNING_ANALYST:
                return 'Affected file: app/value.py'
            if agent == planning.PLAN_AUTHOR:
                return '# Development Plan\nImplement value() returning three.\n'
            if step_id == 'plan-review-r1-c1':
                return {'review_status': 'CHANGES_REQUIRED', 'summary': 'Clarify verification', 'findings': [{
                    'id': 'P1', 'impact': 'LOW', 'category': 'TESTING', 'disposition': 'PLAN_CHANGE_REQUIRED',
                    'plan_section': 'Verification', 'description': 'Add verification detail', 'evidence': ['app/tests/test_value.py'],
                    'required_action': 'State expected result', 'confidence': 1.0}],
                    'prior_findings': prior_findings_for(prompt)}
            return {'review_status': 'PASS', 'summary': 'Ready for human review', 'findings': [],
                    'prior_findings': prior_findings_for(prompt)}
        calls, output = self.drive(planning, transport, inputs, respond, 'plan-integration')
        self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_APPROVAL')
        self.assertEqual(output['review_rounds'], 2)
        self.assertIn((planning.CONTEXT_NORMALIZER, 'context-normalize-v1-repair-1'), calls)
        records = repo / 'agentic-sdlc-records/T-1'
        review = json.loads((records / 'plan-review.json').read_text())
        self.assertEqual(review['reviewed_plan_sha256'], output['plan_sha256'])
        # This is synthetic approval in an isolated test repo, not a user approval.
        subprocess.run([sys.executable, str(ROOT / '.agentic-sdlc/scripts/approve_plan.py'),
                        '--repository-root', str(repo), '--ticket-id', 'T-1', '--decision', 'APPROVED',
                        '--approved-by', 'test-fixture', '--reference', 'synthetic integration test'],
                       check=True, capture_output=True)
        return repo, git, records

    def test_plan_delivery_repair_remediation_and_human_handoff(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.plan(Path(temp), modular)
                delivery, transport = load('deliver', modular)
                # Exercise skill-selected verification with an actual local command;
                # the integration fixture has no AngularJS application or npm setup.
                registry_path = repo / '.agentic-sdlc/cao/specialists.json'
                registry = json.loads(registry_path.read_text())
                registry['verification']['angularjs'] = [[sys.executable, '-c', 'import runpy; assert runpy.run_path("app/value.py")["value"]() >= 2']]
                registry_path.write_text(json.dumps(registry))
                human = {'id': 'H1', 'file': 'app/value.py', 'location': 'value', 'category': 'AUTHN_AUTHZ',
                    'impact': 'HIGH', 'confidence': .99, 'failure_scenario': 'Synthetic protected concern',
                    'consequence': 'Needs human review', 'remediation_direction': 'Human must assess',
                    'automation_eligibility': 'AUTO_FIX', 'reason': 'Agent claim must be overridden',
                    'localized_and_bounded': True, 'deterministically_verifiable': True}
                auto = {**human, 'id': 'A1', 'category': 'CORRECTNESS', 'impact': 'MEDIUM',
                        'failure_scenario': 'Value is two, expected three'}
                def respond(agent, step_id, prompt):
                    if agent == 'sdlc_code_supervisor':
                        return {'tasks': [{'id': 'T1', 'worker': 'developer', 'plan_reference': 'T1',
                            'instructions': 'Implement value', 'depends_on': [], 'skills': ['sdlc-angularjs-ui']}]}
                    if agent == delivery.IMPLEMENTER:
                        # Real compile/test subprocesses reject the first implementation.
                        text = 'def value():\n    return 2\n' if step_id == 'implement-v1-repair-1' else 'invalid python !!!\n'
                        (repo / 'app/value.py').write_text(text)
                        return {'tasks_completed': ['T1'], 'files_changed': ['app/value.py'], 'assumptions': [], 'deviations': []}
                    if agent == delivery.REMEDIATOR:
                        self.assertIn('"id": "A1"', prompt)
                        self.assertNotIn('"id": "H1"', prompt)
                        (repo / 'app/value.py').write_text('def value():\n    return 3\n')
                        return {'findings_addressed': ['A1'], 'files_changed': ['app/value.py'], 'assumptions': [], 'deviations': []}
                    return {'summary': 'Synthetic review', 'findings': [auto, human] if step_id == 'pr-review-r1' else [human]}
                calls, output = self.drive(delivery, transport, {'repository_root': str(repo), 'ticket_id': 'T-1'}, respond, 'delivery-integration')
                self.assertEqual(output['workflow_outcome'], 'AWAITING_HUMAN_REVIEW',
                    str(output) + '\n' + '\n'.join(p.read_text() for p in (repo / '.agentic-sdlc/runtime/T-1/delivery-integration/verification').glob('*repair*.log')))
                self.assertTrue(output['has_developer_required_findings'])
                self.assertFalse(output['has_auto_fix_findings'])
                self.assertEqual(output['remediation_rounds'], 1)
                self.assertEqual(output['pr_head_sha'], git('rev-parse', 'HEAD'))
                self.assertEqual(git('branch', '--show-current'), 'sdlc/T-1')
                self.assertIn((delivery.IMPLEMENTER, 'implement-v1-repair-1'), calls)
                self.assertIn(('sdlc_code_supervisor', 'dispatch-v1'), calls)
                self.assertIn((delivery.IMPLEMENTER, 'worker-1'), calls)
                self.assertIn((delivery.IMPLEMENTER, 'integrate-v1'), calls)
                self.assertTrue((records / 'human-review-brief.md').exists())
                self.assertIn(output['pr_head_sha'], (records / 'pr-body.md').read_text())
                review = json.loads((records / 'pr-review-r2.json').read_text())
                self.assertEqual(review['findings'][0]['automation_eligibility'], 'DEVELOPER_REQUIRED')
                delivery_manifest = json.loads((records / 'delivery-manifest.json').read_text())
                self.assertEqual(len(delivery_manifest['verification']['commands']), 3)

    def test_delivery_rejects_changed_plan_before_any_agent_runs(self):
        for modular in (False, True):
            with self.subTest(modular=modular), tempfile.TemporaryDirectory() as temp:
                repo, git, records = self.plan(Path(temp), modular)
                (records / 'development-plan.md').write_text('Modified after approval')
                delivery, transport = load('deliver', modular)
                with patch.object(delivery, 'get_inputs', return_value={'repository_root': str(repo), 'ticket_id': 'T-1'}), patch.object(transport, 'step') as step:
                    with self.assertRaisesRegex(delivery.WorkflowContractError, 'changed since'):
                        delivery.main()
                    step.assert_not_called()
                self.assertEqual(git('branch', '--show-current'), 'main')


if __name__ == '__main__':
    unittest.main()
