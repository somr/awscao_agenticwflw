"""Module/deployment parity, reproducible snapshots and safe installation."""
from __future__ import annotations
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CAO = ROOT / '.agentic-sdlc/cao'
sys.path.insert(0, str(CAO))
shim = types.ModuleType('cao_workflow')
shim.step = lambda *args, **kwargs: None
shim.get_inputs = lambda: {}
shim.emit_output = lambda value: None
sys.modules.setdefault('cao_workflow', shim)
import build_workflow as builder
import install_workflow as installer
from sdlc_workflows import planning, delivery, source_review, runtime


def bundle(name):
    module = types.ModuleType(name + '_test_bundle')
    exec(compile(builder.build_source(name), '<bundle>', 'exec'), module.__dict__)
    return module


class BundleTest(unittest.TestCase):
    def test_workflows_share_one_runtime_implementation(self):
        for module in (planning, delivery, source_review):
            self.assertIs(module._run_json_contract_step, runtime._run_json_contract_step)
        self.assertIs(planning._write_json, delivery._write_json)
        self.assertIs(delivery._write_json, source_review._write_json)
        self.assertIs(planning.WorkflowContractError, source_review.WorkflowContractError)

    def test_builds_are_deterministic_and_capture_all_module_digests(self):
        for name in builder.WORKFLOWS:
            with self.subTest(workflow=name):
                first = builder.build_source(name)
                self.assertEqual(first, builder.build_source(name))
                self.assertNotIn(str(ROOT), first)
                artifact = bundle(name)
                self.assertIn('runtime.py', artifact.SDLC_BUNDLE_MANIFEST)
                for filename, digest in artifact.SDLC_BUNDLE_MANIFEST.items():
                    self.assertEqual(digest, hashlib.sha256((builder.PACKAGE / filename).read_bytes()).hexdigest())
                tree = ast.parse(first)
                self.assertFalse(any(isinstance(n, ast.ImportFrom) and n.level for n in ast.walk(tree)))
                self.assertEqual(artifact.INPUTS, importlib.import_module('sdlc_workflows.' + builder.WORKFLOWS[name]).INPUTS)

    def test_dependency_change_alters_new_bundle_but_not_frozen_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / 'package'
            shutil.copytree(builder.PACKAGE, package)
            frozen = builder.build_source('dev_plan', package)
            path = package / 'runtime.py'
            path.write_text(path.read_text().replace('COMPLETION_STABLE_POLLS = 2', 'COMPLETION_STABLE_POLLS = 9'))
            changed = builder.build_source('dev_plan', package)
            self.assertNotEqual(frozen, changed)
            shutil.rmtree(package)
            namespace = {'__name__': 'frozen'}
            exec(frozen, namespace)
            self.assertEqual(namespace['COMPLETION_STABLE_POLLS'], 2)

    def test_relocated_bundles_execute_without_repository_or_import_path(self):
        # Simulates CAO's frozen-source resume location, not merely import-time
        # syntax. Each bundle actually performs a two-attempt JSON repair over
        # replayed answer files in an isolated Python interpreter.
        program = r'''
import json, pathlib, runpy, sys, types
root = pathlib.Path.cwd()
calls = []
def step(*args, **kwargs):
    calls.append(kwargs['step_id'])
    path = root / (kwargs['step_id'] + '.answer.json')
    path.write_text('{bad' if len(calls) == 1 else '{"ok": true}')
    return types.SimpleNamespace(replayed=True, terminal_id='already-cleaned')
shim = types.ModuleType('cao_workflow')
shim.step = step
shim.get_inputs = lambda: {}
shim.emit_output = lambda value: None
sys.modules['cao_workflow'] = shim
ns = runpy.run_path(sys.argv[1], run_name='frozen_workflow')
value = ns['_run_json_contract_step'](agent='test', prompt='test', label='test',
          step_id='repair', repo=root, evidence_dir=root)
assert value == {'ok': True}
assert calls == ['repair', 'repair-repair-1']
assert (root / 'repair-repair-1.raw.txt').exists()
assert not any(k.startswith('sdlc_workflows') for k in sys.modules)
print(json.dumps({'calls': calls, 'inputs': ns['INPUTS']}))
'''
        for name in builder.WORKFLOWS:
            with self.subTest(workflow=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                snapshot = root / 'snapshot.py'
                snapshot.write_text(builder.build_source(name))
                result = subprocess.run([sys.executable, '-I', '-c', program, str(snapshot)],
                                        cwd=root, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['calls'], ['repair', 'repair-repair-1'])

    def test_repair_policy_preserves_delivery_and_planning_instructions(self):
        for name in builder.WORKFLOWS:
            mod = bundle(name)
            calls = []
            def execute(**kwargs):
                calls.append(kwargs)
                return '{bad' if len(calls) == 1 else '{"ok":true}'
            with tempfile.TemporaryDirectory() as temp, patch.object(mod, '_run_delivered_step', side_effect=execute):
                mod._run_json_contract_step(agent='test', prompt='original', label='test',
                    step_id='s', repo=Path(temp), evidence_dir=Path(temp), preserve_source=name != 'deliver')
            self.assertEqual('Preserve the meaning and provenance' in calls[1]['prompt'], name != 'deliver')
            self.assertIn('Correct JSON syntax', calls[1]['prompt'])
            self.assertEqual(calls[1]['step_id'], 's-repair-1')
        # Every delivery invocation explicitly retains its existing repair policy.
        tree = ast.parse((builder.PACKAGE / 'delivery.py').read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == '_run_json_contract_step']
        self.assertTrue(calls)
        for call in calls:
            self.assertTrue(any(k.arg == 'preserve_source' and isinstance(k.value, ast.Constant) and k.value.value is False for k in call.keywords))

    def test_errors_cleanup_the_exact_worker_and_do_not_repair(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(runtime, 'step', return_value=types.SimpleNamespace(replayed=False, terminal_id='owned')), patch.object(runtime, '_wait_for_answer_file', side_effect=runtime.IncompleteAgentExecutionError('worker failed')), patch.object(runtime, '_cleanup_step_terminal') as cleanup:
            with self.assertRaises(runtime.IncompleteAgentExecutionError):
                runtime._run_json_contract_step(agent='test', prompt='p', label='t', step_id='s', repo=Path(temp), evidence_dir=Path(temp))
            cleanup.assert_called_once_with('owned', Path(temp), 's')

    def test_package_initializer_cannot_hide_unbundled_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            (package / 'planning.py').write_text('INPUTS = {}\n')
            (package / '__init__.py').write_text('import os\n')
            with self.assertRaisesRegex(builder.BuildError, 'only documentation'):
                builder.build_source('dev_plan', package)

    def test_builder_rejects_unsafe_module_composition(self):
        cases = [
            ('from .support import X\nX = 2\n', 'X = 1\n', 'Conflicting binding'),
            ('from .support import *\n', 'X = 1\n', 'wildcard'),
            ('from .support import X as Y\n', 'X = 1\n', 'aliased'),
            ('from .support import MISSING\n', 'X = 1\n', 'does not export'),
            ('from .support import X\n', 'from .planning import X\nX = 1\n', 'Circular'),
            ('import sdlc_workflows.support\n', 'X = 1\n', 'relative named imports'),
            ('SDLC_BUNDLE_MANIFEST = {}\n', '', 'reserved'),
            ('ROOT = __file__\n', '', 'module-relative'),
        ]
        for source, dependency, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp:
                package = Path(temp)
                (package / 'planning.py').write_text(source)
                (package / 'support.py').write_text(dependency)
                with self.assertRaisesRegex(builder.BuildError, message):
                    builder.build_source('dev_plan', package)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name)
        self.calls = []

    def fake_cao(self, *args, check=True):
        self.calls.append(args)
        code = 1 if args[:2] == ('profile', 'show') else 0
        return subprocess.CompletedProcess(args, code, 'ok', '')

    def test_all_installers_keep_names_and_install_self_contained_code(self):
        with patch.object(installer, 'cao', side_effect=self.fake_cao):
            for name, registered in installer.REGISTERED_NAMES.items():
                target = installer.install(name, ROOT, self.destination)
                self.assertEqual(target.name, registered + '.py')
                self.assertEqual(target.read_text(), builder.build_source(name))
        self.assertFalse(list(self.destination.glob('*candidate*')))
        self.assertFalse(list(self.destination.glob('.*.lock')))
        self.assertEqual(len([c for c in self.calls if c[0] == 'install']), 5)

    def test_validation_failure_preserves_installed_bytes_and_cleans_candidate(self):
        target = self.destination / 'sdlc_dev_plan.py'
        target.write_text('previous')
        def fail(*args, **kwargs):
            if args[:2] == ('workflow', 'validate'):
                raise RuntimeError('validation failed')
            return self.fake_cao(*args, **kwargs)
        with patch.object(installer, 'cao', side_effect=fail), self.assertRaisesRegex(RuntimeError, 'validation failed'):
            installer.install('dev_plan', ROOT, self.destination)
        self.assertEqual(target.read_text(), 'previous')
        self.assertEqual([p.name for p in self.destination.iterdir()], ['sdlc_dev_plan.py'])

    def test_planning_delivery_upgrades_keep_backup(self):
        for name in ('dev_plan', 'deliver'):
            target = self.destination / (installer.REGISTERED_NAMES[name] + '.py')
            target.write_text('previous')
            with patch.object(installer, 'cao', side_effect=self.fake_cao):
                installer.install(name, ROOT, self.destination)
            self.assertEqual(Path(str(target) + '.bak').read_text(), 'previous')

    def test_source_install_refuses_existing_workflow_and_profiles(self):
        target = self.destination / 'source_review.py'
        target.write_text('previous')
        with patch.object(installer, 'cao', side_effect=self.fake_cao), self.assertRaises(FileExistsError):
            installer.install('source_review', ROOT, self.destination)
        self.assertEqual(target.read_text(), 'previous')
        target.unlink()
        with patch.object(installer, 'cao', return_value=subprocess.CompletedProcess([], 0)), self.assertRaisesRegex(FileExistsError, 'profile'):
            installer.install('source_review', ROOT, self.destination)
        self.assertFalse(target.exists())

    def test_validation_only_never_installs_or_replaces_profiles(self):
        for name in installer.REGISTERED_NAMES:
            target = self.destination / (installer.REGISTERED_NAMES[name] + '.py')
            target.write_text('previous')
            with patch.object(installer, 'cao', side_effect=self.fake_cao):
                installer.install(name, ROOT, self.destination, validate_only=True)
            self.assertEqual(target.read_text(), 'previous')
        self.assertTrue(all(c[0] == 'workflow' for c in self.calls))

    def test_collisions_and_concurrent_installer_fail_closed(self):
        for suffix in ('.yaml', '.yml', '.install.lock'):
            filename = ('sdlc_dev_plan' if suffix != '.install.lock' else '.sdlc_dev_plan') + suffix
            path = self.destination / filename
            path.write_text('owned by another process')
            with patch.object(installer, 'cao', side_effect=self.fake_cao), self.assertRaises(FileExistsError):
                installer.install('dev_plan', ROOT, self.destination)
            self.assertEqual(path.read_text(), 'owned by another process')
            path.unlink()


if __name__ == '__main__':
    unittest.main()
