"""Frozen library and entry scripts: reproducible builds, tamper refusal, relocation and safe installation."""
from __future__ import annotations
import ast
import hashlib
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
CAO = ROOT / '.agentic-sdlc/cao'
sys.path.insert(0, str(CAO))
shim = types.ModuleType('cao_workflow')
shim.step = lambda *args, **kwargs: None
shim.get_inputs = lambda: {}
shim.emit_output = lambda value: None
sys.modules.setdefault('cao_workflow', shim)
import build_library as builder
import install_workflow as installer
from sdlc_workflows import planning, delivery, source_review, runtime

# CAO 2.5.4 refuses a workflow script over 131,072 characters; entries must stay far below it.
ENTRY_MAX_CHARS = 32 * 1024
# CAO's WORKFLOW_NAME_RE, which its linter applies to literal step ids in the installed script only.
STEP_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')


def make_writable(root: Path) -> None:
    if root.exists():
        for path in [root, *root.rglob('*')]:
            path.chmod(0o755 if path.is_dir() else 0o644)


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(lambda: make_writable(Path(self.temp.name)))
        self.root = Path(self.temp.name)

    def installed_library(self, package=builder.PACKAGE):
        files, manifest, library_id = builder.build_library(package)
        library = self.root / 'sdlc-lib' / library_id
        library.mkdir(parents=True)
        builder.write_library(files, manifest, library)
        return library, manifest

    def test_workflows_share_one_runtime_implementation(self):
        for module in (planning, delivery, source_review):
            self.assertIs(module._run_json_contract_step, runtime._run_json_contract_step)
        self.assertIs(planning._write_json, delivery._write_json)
        self.assertIs(delivery._write_json, source_review._write_json)
        self.assertIs(planning.WorkflowContractError, source_review.WorkflowContractError)

    def test_the_library_is_the_package_byte_for_byte_and_its_id_is_stable(self):
        files, manifest, library_id = builder.build_library()
        self.assertEqual((files, manifest, library_id), builder.build_library())
        self.assertRegex(library_id, '^[0-9a-f]{16}$')
        modules = {p.relative_to(builder.PACKAGE).as_posix() for p in builder.PACKAGE.glob('*.py')}
        self.assertLessEqual(modules, set(files))
        for name, content in files.items():
            self.assertEqual(content, (builder.PACKAGE / name).read_bytes())
            self.assertEqual(manifest[name], hashlib.sha256(content).hexdigest())

    def test_a_changed_module_gives_a_new_library_and_leaves_the_old_one_alone(self):
        package = self.root / 'package'
        shutil.copytree(builder.PACKAGE, package, ignore=shutil.ignore_patterns('__pycache__'))
        old, old_manifest = self.installed_library(package)
        path = package / 'runtime.py'
        path.write_text(path.read_text().replace('COMPLETION_STABLE_POLLS = 2', 'COMPLETION_STABLE_POLLS = 9'))
        _, new_manifest, new_id = builder.build_library(package)
        self.assertNotEqual(new_id, old.name)
        builder.verify_library(old, old_manifest)  # untouched
        with self.assertRaises(SystemExit):
            builder.verify_library(old, new_manifest)

    def test_the_library_holds_only_modules_and_prompt_files(self):
        package = self.root / 'package'
        package.mkdir()
        (package / '__init__.py').write_text('"""Docs."""\n')
        (package / 'notes.txt').write_text('x')
        with self.assertRaisesRegex(builder.BuildError, 'notes.txt'):
            builder.build_library(package)
        (package / 'notes.txt').unlink()
        (package / 'prompts/delivery').mkdir(parents=True)
        (package / 'prompts/delivery/pr-reviewer.md').write_text('Review ${diff}.')
        self.assertIn('prompts/delivery/pr-reviewer.md', builder.build_library(package)[0])
        (package / '__init__.py').unlink()
        with self.assertRaisesRegex(builder.BuildError, '__init__'):
            builder.build_library(package)

    def test_entries_carry_literal_inputs_and_stay_small(self):
        # CAO's index skips a workflow whose INPUTS is not a literal, while `cao workflow validate` still passes.
        _, manifest, library_id = builder.build_library()
        library = Path('/opt/cao/sdlc-lib') / library_id
        for name, module in builder.WORKFLOWS.items():
            with self.subTest(workflow=name):
                source = builder.build_entry(name, library, manifest)
                self.assertEqual(source, builder.build_entry(name, library, manifest))
                self.assertLess(len(source), ENTRY_MAX_CHARS)
                self.assertNotIn(str(ROOT), source)
                tree = ast.parse(source)
                node = next(n for n in tree.body if isinstance(n, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == 'INPUTS' for t in n.targets))
                self.assertEqual(ast.literal_eval(node.value), importlib.import_module('sdlc_workflows.' + module).INPUTS)
                self.assertFalse(any(isinstance(n, ast.ImportFrom) and n.level for n in ast.walk(tree)))
        self.assertEqual(delivery.INPUTS['hybrid_max_parallel']['default'], delivery.MAX_PARALLEL_WORKERS)
        self.assertEqual(delivery.INPUTS['review_max_parallel']['default'], delivery.MAX_PARALLEL_REVIEWS)
        self.assertEqual(source_review.INPUTS['review_max_parallel']['default'], source_review.MAX_PARALLEL_REVIEWS)

    def test_every_step_call_declares_recovery_and_every_literal_step_id_is_valid(self):
        # CAO lints only the installed entry, which no longer contains the step() calls.
        literal_ids = steps = 0
        for path in sorted(builder.PACKAGE.glob('*.py')):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', None)
                if name in ('step', 'run_step'):
                    steps += 1
                    self.assertIn('recovery', {k.arg for k in node.keywords}, f'{path.name}:{node.lineno}')
                for keyword in node.keywords:
                    if keyword.arg == 'step_id' and isinstance(keyword.value, ast.Constant):
                        literal_ids += 1
                        self.assertRegex(keyword.value.value, STEP_ID, f'{path.name}:{node.lineno}')
        self.assertGreaterEqual(steps, 1)
        self.assertGreater(literal_ids, 5)

    def test_a_relocated_entry_runs_the_workflow_from_the_library_only(self):
        # Simulates CAO running the frozen entry from an engine-owned folder, in an isolated interpreter
        # with no repository on the path, and performs a two-attempt JSON repair through the library's runtime.
        library, manifest = self.installed_library()
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
runtime = sys.modules['sdlc_workflows.runtime']
value = runtime._run_json_contract_step(agent='test', prompt='test', label='test',
          step_id='repair', repo=root, evidence_dir=root)
assert value == {'ok': True}
print(json.dumps({'calls': calls, 'module': runtime.__file__, 'manifest': runtime.installed_library_manifest()}))
'''
        for name in builder.WORKFLOWS:
            with self.subTest(workflow=name), tempfile.TemporaryDirectory() as temp:
                work = Path(temp)
                snapshot = work / 'snapshot.py'
                snapshot.write_text(builder.build_entry(name, library, manifest))
                result = subprocess.run([sys.executable, '-I', '-c', program, str(snapshot)],
                                        cwd=work, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                answer = json.loads(result.stdout)
                self.assertEqual(answer['calls'], ['repair', 'repair-repair-1'])
                self.assertTrue(answer['module'].startswith(str(library)))
                self.assertEqual(answer['manifest'], manifest)
                self.assertFalse(list(library.rglob('__pycache__')))

    def test_an_entry_refuses_a_changed_missing_or_extra_library_file_before_importing(self):
        program = r'''
import runpy, sys, types
shim = types.ModuleType('cao_workflow')
shim.step = shim.get_inputs = shim.emit_output = lambda *a, **k: None
sys.modules['cao_workflow'] = shim
try:
    runpy.run_path(sys.argv[1], run_name='frozen_workflow')
except SystemExit as exc:
    assert not any(k.startswith('sdlc_workflows') for k in sys.modules)
    print(exc)
    raise SystemExit(3)
'''
        cases = {
            'runtime.py changed': lambda pkg: (pkg / 'runtime.py').write_text('# tampered\n', encoding='utf-8'),
            "extra: ['evil.py']": lambda pkg: (pkg / 'evil.py').write_text('X = 1\n'),
            "missing: ['errors.py']": lambda pkg: (pkg / 'errors.py').unlink(),
            'manifest.json differs': lambda pkg: (pkg.parent / 'manifest.json').write_text('{}'),
            'it is missing': lambda pkg: shutil.rmtree(pkg),
        }
        for expected, damage in cases.items():
            with self.subTest(case=expected), tempfile.TemporaryDirectory() as temp:
                files, manifest, library_id = builder.build_library()
                library = Path(temp) / library_id
                library.mkdir()
                builder.write_library(files, manifest, library)
                make_writable(library)
                damage(library / 'sdlc_workflows')
                snapshot = Path(temp) / 'snapshot.py'
                snapshot.write_text(builder.build_entry('dev_plan', library, manifest))
                result = subprocess.run([sys.executable, '-I', '-c', program, str(snapshot)],
                                        cwd=temp, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertIn('cannot resume', result.stdout)

    def test_running_from_the_repository_has_no_library_manifest(self):
        self.assertIsNone(runtime.installed_library_manifest())

    def test_the_final_validator_replaces_the_validator_on_the_last_attempt_only(self):
        seen = []
        def strict(value):
            seen.append('strict')
            raise runtime.WorkflowContractError('missing verdict')
        def lenient(value):
            seen.append('lenient')
            return {**value, 'filled': True}
        with tempfile.TemporaryDirectory() as temp, patch.object(runtime, '_run_delivered_step', return_value='{"ok":true}'):
            value = runtime._run_json_contract_step(agent='test', prompt='p', label='test', step_id='s', repo=Path(temp),
                                                    evidence_dir=Path(temp), validator=strict, final_validator=lenient)
        self.assertEqual((value, seen), ({'ok': True, 'filled': True}, ['strict', 'lenient']))

    def test_repair_policy_preserves_delivery_and_planning_instructions(self):
        for preserve in (True, False):
            calls = []
            def execute(**kwargs):
                calls.append(kwargs)
                return '{bad' if len(calls) == 1 else '{"ok":true}'
            with tempfile.TemporaryDirectory() as temp, patch.object(runtime, '_run_delivered_step', side_effect=execute):
                runtime._run_json_contract_step(agent='test', prompt='original', label='test',
                    step_id='s', repo=Path(temp), evidence_dir=Path(temp), preserve_source=preserve)
            self.assertEqual('Preserve the meaning and provenance' in calls[1]['prompt'], preserve)
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


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(lambda: make_writable(Path(self.temp.name)))
        self.destination = Path(self.temp.name) / 'workflows'
        self.destination.mkdir()
        self.libraries = Path(self.temp.name) / 'sdlc-lib'
        self.calls = []
        _, self.manifest, self.library_id = builder.build_library()
        self.library = self.libraries / self.library_id

    def install(self, name, **kwargs):
        return installer.install(name, ROOT, self.destination, library_root=self.libraries, **kwargs)

    def fake_cao(self, *args, check=True):
        self.calls.append(args)
        # Profiles installed earlier in the same test count as present (source-standards.md -> sdlc_source_standards).
        installed = {'sdlc_' + Path(c[1]).stem.replace('-', '_') for c in self.calls if c[0] == 'install'}
        present = {'sdlc_remediator', 'sdlc_source_fix_reviewer'} | installed
        code = 1 if args[:2] == ('profile', 'show') and args[2] not in present else 0
        return subprocess.CompletedProcess(args, code, 'ok', '')

    def test_all_installers_keep_names_and_install_one_shared_read_only_library(self):
        with patch.object(installer, 'cao', side_effect=self.fake_cao):
            for name, registered in installer.REGISTERED_NAMES.items():
                target = self.install(name)
                self.assertEqual(target.name, registered + '.py')
                self.assertEqual(target.read_text(), builder.build_entry(name, self.library, self.manifest))
        self.assertEqual([p.name for p in self.libraries.iterdir()], [self.library_id])
        builder.verify_library(self.library, self.manifest)
        self.assertFalse(os.access(self.library / 'sdlc_workflows/runtime.py', os.W_OK))
        self.assertFalse(list(self.destination.glob('*candidate*')))
        self.assertFalse(list(self.destination.glob('.*.lock')))
        # Source review's 6 profiles and the follow-up's 2.
        self.assertEqual(len([c for c in self.calls if c[0] == 'install']), 8)

    def test_an_existing_library_that_differs_is_never_replaced(self):
        self.library.mkdir(parents=True)
        (self.library / 'manifest.json').write_text('{}')
        with patch.object(installer, 'cao', side_effect=self.fake_cao), self.assertRaisesRegex(RuntimeError, 'does not match'):
            self.install('dev_plan')
        self.assertEqual((self.library / 'manifest.json').read_text(), '{}')
        self.assertFalse((self.destination / 'sdlc_dev_plan.py').exists())

    def test_followup_requires_the_standards_profile_and_installs_its_own(self):
        with patch.object(installer, 'cao', side_effect=self.fake_cao), self.assertRaisesRegex(FileNotFoundError, 'source_review first'):
            self.install('source_followup')
        self.assertFalse((self.destination / 'source_followup.py').exists())
        self.calls.append(('install', 'profiles/source-standards.md'))
        with patch.object(installer, 'cao', side_effect=self.fake_cao):
            self.install('source_followup')
        self.assertEqual([Path(c[1]).name for c in self.calls if c[0] == 'install'][1:],
                         ['source-followup-judge.md', 'source-followup-changes.md'])

    def test_validation_failure_preserves_installed_bytes_cleans_candidate_and_writes_no_library(self):
        target = self.destination / 'sdlc_dev_plan.py'
        target.write_text('previous')
        def fail(*args, **kwargs):
            if args[:2] == ('workflow', 'validate'):
                raise RuntimeError('validation failed')
            return self.fake_cao(*args, **kwargs)
        with patch.object(installer, 'cao', side_effect=fail), self.assertRaisesRegex(RuntimeError, 'validation failed'):
            self.install('dev_plan')
        self.assertEqual(target.read_text(), 'previous')
        self.assertEqual([p.name for p in self.destination.iterdir()], ['sdlc_dev_plan.py'])
        self.assertFalse(self.libraries.exists())

    def test_remediation_requires_profiles_without_overwriting_shared_profile(self):
        def missing(*args, **kwargs):
            self.calls.append(args)
            return subprocess.CompletedProcess(args, 1 if args[:2] == ('profile', 'show') else 0, '', '')
        with patch.object(installer, 'cao', side_effect=missing), self.assertRaises(FileNotFoundError):
            self.install('source_remediate')
        self.assertFalse(any(c[0] == 'install' for c in self.calls))
        self.assertFalse((self.destination / 'source_remediate.py').exists())

    def test_planning_delivery_upgrades_keep_backup(self):
        for name in ('dev_plan', 'deliver'):
            target = self.destination / (installer.REGISTERED_NAMES[name] + '.py')
            target.write_text('previous')
            with patch.object(installer, 'cao', side_effect=self.fake_cao):
                self.install(name)
            self.assertEqual(Path(str(target) + '.bak').read_text(), 'previous')

    def test_source_install_refuses_existing_workflow_and_profiles(self):
        target = self.destination / 'source_review.py'
        target.write_text('previous')
        with patch.object(installer, 'cao', side_effect=self.fake_cao), self.assertRaises(FileExistsError):
            self.install('source_review')
        self.assertEqual(target.read_text(), 'previous')
        target.unlink()
        with patch.object(installer, 'cao', return_value=subprocess.CompletedProcess([], 0)), self.assertRaisesRegex(FileExistsError, 'profile'):
            self.install('source_review')
        self.assertFalse(target.exists())

    def test_validation_only_never_installs_libraries_workflows_or_profiles(self):
        for name in installer.REGISTERED_NAMES:
            target = self.destination / (installer.REGISTERED_NAMES[name] + '.py')
            target.write_text('previous')
            with patch.object(installer, 'cao', side_effect=self.fake_cao):
                self.install(name, validate_only=True)
            self.assertEqual(target.read_text(), 'previous')
        self.assertTrue(all(c[0] == 'workflow' for c in self.calls))
        self.assertFalse(self.libraries.exists())

    def test_collisions_and_concurrent_installer_fail_closed(self):
        for suffix in ('.yaml', '.yml', '.install.lock'):
            filename = ('sdlc_dev_plan' if suffix != '.install.lock' else '.sdlc_dev_plan') + suffix
            path = self.destination / filename
            path.write_text('owned by another process')
            with patch.object(installer, 'cao', side_effect=self.fake_cao), self.assertRaises(FileExistsError):
                self.install('dev_plan')
            self.assertEqual(path.read_text(), 'owned by another process')
            path.unlink()


if __name__ == '__main__':
    unittest.main()
