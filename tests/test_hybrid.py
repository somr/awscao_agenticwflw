"""Registry, task graph and dispatch checks in source and deployed bundles."""
import copy
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic-sdlc/cao'))
stub = types.ModuleType('cao_workflow')
stub.step = lambda *a, **k: None
stub.emit_output = lambda value: None
stub.get_inputs = lambda: {}
sys.modules.setdefault('cao_workflow', stub)
from build_workflow import build_source


def modules():
    bundle = types.ModuleType('hybrid_bundle')
    exec(compile(build_source('deliver'), '<deliver>', 'exec'), bundle.__dict__)
    return [importlib.import_module('sdlc_workflows.hybrid'), bundle]


def task(name='T1', **changes):
    return dict(id=name, worker='developer', instructions='Implement assigned feature',
                plan_reference='T1', depends_on=[], skills=[], **changes)


class HybridTest(unittest.TestCase):
    def test_registry_and_skill_assets(self):
        for mod in modules():
            registry = mod.load_specialists(ROOT)
            text = mod.hybrid_skill_context(ROOT, registry, ['sdlc-angularjs-ui', 'sdlc-spark-workflows'])
            self.assertIn('sha256=', text)
            self.assertIn('AngularJS', text)
            self.assertIn('Spark', text)
            with self.assertRaises(mod.WorkflowContractError):
                mod._hybrid_asset(ROOT, '../../../README.md')

    def test_rejects_invalid_graph_before_dispatch(self):
        for mod in modules():
            registry = mod.load_specialists(ROOT)
            bad_values = [{'tasks': []}, {'tasks': [task(), task()]}, {'tasks': [task()] * 17}]
            for key, value in [('worker', 'unknown'), ('skills', ['unknown']), ('depends_on', ['T2']),
                               ('depends_on', ['T1']), ('id', '../escape'), ('plan_reference', '')]:
                entry = task()
                entry[key] = value
                bad_values.append({'tasks': [entry]})
            for value in bad_values:
                with self.subTest(value=value), self.assertRaises(mod.WorkflowContractError):
                    mod.validate_dispatch(value, registry)

    def test_dispatch_order_skills_evidence_and_verification_union(self):
        for mod in modules():
            registry = mod.load_specialists(ROOT)
            first, second = task(), task('T2')
            first['skills'] = ['sdlc-angularjs-ui']
            second['depends_on'] = ['T1']
            second['skills'] = ['sdlc-spark-workflows']
            dispatch = {'tasks': [first, second]}
            calls = []
            def run(**kwargs):
                calls.append(kwargs)
                if kwargs['agent'] == mod.SUPERVISOR:
                    return kwargs['validator'](dispatch)
                return {'tasks_completed': [kwargs['step_id']], 'files_changed': ['app/x'],
                        'assumptions': [], 'deviations': ['disclosed limitation']}
            with tempfile.TemporaryDirectory() as temp, patch.object(mod, '_run_json_contract_step', side_effect=run):
                result, commands, context = mod.run_hybrid(repo=ROOT, prompt='Approved scope',
                    evidence_dir=Path(temp), completion_validator=lambda value: value)
                self.assertEqual([c['step_id'] for c in calls], ['dispatch-v1', 'worker-1', 'worker-2', 'integrate-v1'])
                self.assertIn('Required skill sdlc-angularjs-ui', calls[1]['prompt'])
                self.assertIn('Required skill sdlc-spark-workflows', calls[2]['prompt'])
                self.assertIn('worker-1', calls[2]['prompt'])
                self.assertEqual(len(commands), 4)
                self.assertEqual(result['deviations'], ['disclosed limitation'])
                self.assertEqual(json.loads((Path(temp) / 'dispatch.json').read_text()), dispatch)
                self.assertIn('sdlc-angularjs-ui', context)

    def test_invalid_registry_fails_closed(self):
        for mod in modules():
            original = mod.load_specialists(ROOT)
            for section, key, field, value in [('workers', 'developer', 'verification', []),
                    ('skills', 'sdlc-angularjs-ui', 'verification', ['missing']),
                    ('workers', 'developer', 'skills', ['unknown'])]:
                registry = copy.deepcopy(original)
                registry[section][key][field] = value
                with patch.object(mod, '_read_json', return_value=registry), self.assertRaises(mod.WorkflowContractError):
                    mod.load_specialists(ROOT)

    def test_worker_failure_stops_dependents_and_integration(self):
        for mod in modules():
            calls = []
            first, second = task(), task('T2')
            second['depends_on'] = ['T1']
            def run(**kwargs):
                calls.append(kwargs['step_id'])
                if kwargs['agent'] == mod.SUPERVISOR:
                    return kwargs['validator']({'tasks': [first, second]})
                raise mod.WorkflowContractError('worker failed')
            with tempfile.TemporaryDirectory() as temp, patch.object(mod, '_run_json_contract_step', side_effect=run):
                with self.assertRaisesRegex(mod.WorkflowContractError, 'worker failed'):
                    mod.run_hybrid(repo=ROOT, prompt='scope', evidence_dir=Path(temp), completion_validator=lambda x: x)
            self.assertEqual(calls, ['dispatch-v1', 'worker-1'])


if __name__ == '__main__':
    unittest.main()
