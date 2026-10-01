"""Registry, task graph and dispatch checks in source and deployed bundles."""
import contextlib
import copy
import importlib
import json
import shutil
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


@contextlib.contextmanager
def registry_only(mod, registry):
    # Load exactly this registry, as if the repository had no agentic-sdlc-project.json.
    with patch.object(mod, '_read_json', return_value=registry), patch.object(mod, 'load_project_file', return_value=None):
        yield


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
                    ('workers', 'developer', 'verification', ['undefined-suite']),
                    ('skills', 'sdlc-angularjs-ui', 'verification', []),
                    ('workers', 'developer', 'skills', ['unknown'])]:
                registry = copy.deepcopy(original)
                registry[section][key][field] = value
                with registry_only(mod, registry), self.assertRaises(mod.WorkflowContractError):
                    mod.load_specialists(ROOT)

    def test_a_worker_whose_profile_may_not_write_is_a_registry_error(self):
        for mod in modules():
            original = mod.load_specialists(ROOT)
            # Default write profiles cover the implementer, so this is accepted.
            self.assertEqual(original['workers']['developer']['profile'], 'sdlc_implementer')
            registry = copy.deepcopy(original)
            registry['workers']['java'] = dict(registry['workers']['developer'], profile='sdlc_java_persistence')
            with registry_only(mod, registry), self.assertRaisesRegex(
                    mod.WorkflowContractError, 'not listed in write_profiles'):
                mod.load_specialists(ROOT)
            # Listing the profile (limited to part of a source root) makes the same registry valid.
            registry['write_profiles'] = {'sdlc_implementer': None, 'sdlc_java_persistence': ['app/payment_service']}
            with registry_only(mod, registry):
                self.assertIn('java', mod.load_specialists(ROOT)['workers'])

    def test_invalid_source_root_config_stops_the_registry_from_loading(self):
        for mod in modules():
            for extra in ({'source_roots': ['.git']}, {'write_profiles': {'sdlc_code_supervisor': None}},
                          {'source_roots': ['app'], 'write_profiles': {'sdlc_implementer': ['elsewhere']}}):
                registry = dict(copy.deepcopy(mod.load_specialists(ROOT)), **extra)
                with registry_only(mod, registry), self.assertRaises(mod.WorkflowContractError):
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


class ProjectFileTest(unittest.TestCase):
    """Per-project settings in agentic-sdlc-project.json, merged with the common registry."""

    def repo(self, project=None, registry_extra=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        shutil.copytree(ROOT / '.agentic-sdlc/cao/skills', repo / '.agentic-sdlc/cao/skills')
        registry = json.loads((ROOT / '.agentic-sdlc/cao/specialists.json').read_text())
        registry.update(registry_extra or {})
        (repo / '.agentic-sdlc/cao/specialists.json').write_text(json.dumps(registry))
        if project is not None:
            (repo / 'agentic-sdlc-project.json').write_text(json.dumps(project))
        return repo

    def test_this_repository_keeps_per_project_settings_out_of_the_common_registry(self):
        registry = json.loads((ROOT / '.agentic-sdlc/cao/specialists.json').read_text())
        self.assertFalse({'source_roots', 'write_profiles', 'verification'} & set(registry))

    def test_the_project_file_supplies_roots_profiles_and_verification(self):
        for mod in modules():
            project = {'version': 1, 'source_roots': ['billing/src'],
                       'write_profiles': {'sdlc_implementer': None, 'sdlc_remediator': None},
                       'verification': {'application': [['mvn', 'verify']], 'spark': [['spark-submit', 'x.py']]}}
            registry = mod.load_specialists(self.repo(project))
            self.assertEqual(registry['source_roots'], ['billing/src'])
            self.assertEqual(registry['verification']['application'], [['mvn', 'verify']])
            # AngularJS has no suite in this project, so the skill is unavailable rather than an error.
            self.assertEqual(list(registry['skills']), ['sdlc-spark-workflows'])
            self.assertEqual(registry['workers']['developer']['skills'], ['sdlc-spark-workflows'])
            self.assertEqual(registry['unavailable_skills'], {'sdlc-angularjs-ui': ['angularjs']})

    def test_a_key_in_both_files_is_an_error(self):
        for mod in modules():
            project = {'version': 1, 'verification': {'application': [['make', 'test']]}}
            repo = self.repo(project, {'source_roots': ['app']})
            with self.assertRaisesRegex(mod.WorkflowContractError, 'source_roots set in both'):
                mod.load_specialists(repo)

    def test_a_worker_suite_missing_from_the_project_names_the_project_file(self):
        for mod in modules():
            repo = self.repo({'version': 1, 'verification': {'unit': [['make', 'test']]}})
            with self.assertRaisesRegex(mod.WorkflowContractError, 'application, not defined in agentic-sdlc-project.json'):
                mod.load_specialists(repo)

    def test_without_a_project_file_the_registry_keys_still_apply(self):
        for mod in modules():
            repo = self.repo(None, {'source_roots': ['legacy'], 'verification': {'application': [['make']]}})
            registry = mod.load_specialists(repo)
            self.assertEqual(registry['source_roots'], ['legacy'])
            self.assertEqual(registry['skills'], {})

    def test_an_invalid_project_file_is_an_error(self):
        for mod in modules():
            for project in ({'source_roots': ['app']}, {'version': 2}, {'version': 1, 'workers': {}}, ['app']):
                with self.subTest(project=project), self.assertRaises(mod.WorkflowContractError):
                    mod.load_specialists(self.repo(project))


if __name__ == '__main__':
    unittest.main()
