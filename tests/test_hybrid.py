"""Registry, task graph and dispatch checks in source and deployed bundles."""
import copy
import importlib
import json
import shutil
import subprocess
import sys
import threading
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
    value = dict(id=name, worker='developer', instructions='Implement assigned feature',
                 plan_reference='T1', depends_on=[], skills=[], owns=[f'app/{name.lower()}.py'])
    value.update(changes)
    return value


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def make_repo(base: Path) -> Path:
    # A temporary repository with this project's registry, skills and write boundary, so run_hybrid's Git work
    # never touches the real checkout.
    repo = base / 'repo'
    shutil.copytree(ROOT / '.agentic-sdlc/cao', repo / '.agentic-sdlc/cao',
                    ignore=shutil.ignore_patterns('__pycache__', 'profiles', 'workflows', 'sdlc_workflows'))
    shutil.copytree(ROOT / '.claude/hooks', repo / '.claude/hooks', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(ROOT / '.claude/settings.json', repo / '.claude/settings.json')
    (repo / 'app').mkdir()
    (repo / 'app/shared.py').write_text('a = 1\nb = 2\nc = 3\n')
    (repo / '.gitignore').write_text('.agentic-sdlc/runtime/\n')
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.email', 'test@example.com')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'initial')
    return repo


def run_hybrid(mod, repo, max_parallel=4):
    return mod.run_hybrid(repo=repo, prompt='Approved scope', evidence_dir=repo / '.agentic-sdlc/runtime/T-1/run/implementation/agent-output',
                          completion_validator=lambda value: value, ticket_id='T-1', run_id='run-1', max_parallel=max_parallel)


def summary(step_id, **extra):
    return dict({'tasks_completed': [step_id], 'files_changed': [], 'assumptions': [], 'deviations': []}, **extra)


def plan_graph():
    # PAY-DEMO-001's approved task graph, as the supervisor returned it in run deliver-profiles-1.
    edges = {'T1': [], 'T2': ['T1'], 'T3': [], 'T6': ['T1'], 'T4': ['T2', 'T3'], 'T5': ['T2', 'T3'],
             'T7': ['T4', 'T5', 'T6']}
    return [task(name, depends_on=deps) for name, deps in edges.items()]


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
                               ('depends_on', ['T1']), ('id', '../escape'), ('plan_reference', ''),
                               ('owns', []), ('owns', None), ('owns', ['app/x.py'] * 65), ('owns', ['README.md']),
                               ('owns', ['app/../README.md']), ('owns', ['/abs/app/x.py']), ('owns', ['app/*.py']),
                               ('owns', ['.agentic-sdlc/cao/x']), ('owns', ['app/']), ('owns', [3])]:
                entry = task()
                entry[key] = value
                bad_values.append({'tasks': [entry]})
            for value in bad_values:
                with self.subTest(value=value), self.assertRaises(mod.WorkflowContractError):
                    mod.validate_dispatch(value, registry)

    def test_sequential_waves_commit_per_task_in_the_main_checkout(self):
        for mod in modules():
            with tempfile.TemporaryDirectory() as temp:
                repo = make_repo(Path(temp))
                registry = mod.load_specialists(repo)
                first = task(skills=['sdlc-angularjs-ui'])
                second = task('T2', depends_on=['T1'], skills=['sdlc-spark-workflows'])
                dispatch = {'tasks': [first, second]}
                calls = []
                def run(**kwargs):
                    calls.append(kwargs)
                    if kwargs['agent'] == mod.SUPERVISOR:
                        return kwargs['validator'](dispatch)
                    if kwargs['step_id'].startswith('worker-'):
                        name = kwargs['step_id'].split('-')[1].lower()
                        (kwargs['repo'] / f'app/{name}.py').write_text('x = 1\n')
                    return summary(kwargs['step_id'], deviations=['disclosed limitation'])
                with patch.object(mod, '_run_json_contract_step', side_effect=run):
                    result, commands, context, commits = run_hybrid(mod, repo)
                self.assertEqual([c['step_id'] for c in calls], ['dispatch-v1', 'worker-T1', 'worker-T2', 'integrate-v1'])
                self.assertTrue(all(c['repo'] == repo for c in calls))
                self.assertIn('Required skill sdlc-angularjs-ui', calls[1]['prompt'])
                self.assertIn('Required skill sdlc-spark-workflows', calls[2]['prompt'])
                self.assertIn('worker-T1', calls[2]['prompt'])  # T2 sees the result of the task it depends on
                self.assertNotIn('isolated working copy', calls[1]['prompt'])
                self.assertEqual(len(commands), 4)
                self.assertEqual(result['deviations'], ['disclosed limitation'])
                evidence = repo / '.agentic-sdlc/runtime/T-1/run/implementation/agent-output'
                self.assertEqual(json.loads((evidence / 'dispatch.json').read_text()), dispatch)
                self.assertEqual(json.loads((evidence / 'schedule.json').read_text())['waves'], [['T1'], ['T2']])
                self.assertIn('sdlc-angularjs-ui', context)
                self.assertEqual([c['task'] for c in commits], ['T1', 'T2'])
                self.assertEqual(git(repo, 'log', '--format=%s', '-2').splitlines(), ['[T-1] T2: T1', '[T-1] T1: T1'])
                self.assertEqual(registry['workers'].keys(), mod.load_specialists(repo)['workers'].keys())

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

    def test_a_worker_whose_profile_may_not_write_is_a_registry_error(self):
        for mod in modules():
            original = mod.load_specialists(ROOT)
            # Default write profiles cover the implementer, so this is accepted.
            self.assertEqual(original['workers']['developer']['profile'], 'sdlc_implementer')
            registry = copy.deepcopy(original)
            registry['workers']['java'] = dict(registry['workers']['developer'], profile='sdlc_java_persistence')
            with patch.object(mod, '_read_json', return_value=registry), self.assertRaisesRegex(
                    mod.WorkflowContractError, 'not listed in write_profiles'):
                mod.load_specialists(ROOT)
            # Listing the profile (limited to part of a source root) makes the same registry valid.
            registry['write_profiles'] = {'sdlc_implementer': None, 'sdlc_java_persistence': ['app/payment_service']}
            with patch.object(mod, '_read_json', return_value=registry):
                self.assertIn('java', mod.load_specialists(ROOT)['workers'])

    def test_invalid_source_root_config_stops_the_registry_from_loading(self):
        for mod in modules():
            for extra in ({'source_roots': ['.git']}, {'write_profiles': {'sdlc_code_supervisor': None}},
                          {'source_roots': ['app'], 'write_profiles': {'sdlc_implementer': ['elsewhere']}}):
                registry = dict(copy.deepcopy(mod.load_specialists(ROOT)), **extra)
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
                    run_hybrid(mod, make_repo(Path(temp)))
            self.assertEqual(calls, ['dispatch-v1', 'worker-T1'])


class ParallelWaveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.temp.name))
        self.evidence = self.repo / '.agentic-sdlc/runtime/T-1/run/implementation/agent-output'

    def tearDown(self):
        self.temp.cleanup()

    def run_wave(self, mod, tasks, edits, fail=(), barrier_parties=0):
        barrier = threading.Barrier(barrier_parties) if barrier_parties else None
        calls = []
        def run(**kwargs):
            calls.append((kwargs['step_id'], kwargs['repo']))
            if kwargs['agent'] == mod.SUPERVISOR:
                return kwargs['validator']({'tasks': tasks})
            step = kwargs['step_id']
            if barrier and kwargs['repo'] != self.repo:
                barrier.wait(timeout=10)  # both wave members must be running at the same time
            if step in fail:
                raise mod.WorkflowContractError(f'{step} failed')
            answers = kwargs['evidence_dir']
            answers.mkdir(parents=True, exist_ok=True)
            (answers / f'{step}.answer.json').write_text('{}')
            for path, text in edits.get(step, {}).items():
                (kwargs['repo'] / path).parent.mkdir(parents=True, exist_ok=True)
                (kwargs['repo'] / path).write_text(text)
            return summary(step)
        with patch.object(mod, '_run_json_contract_step', side_effect=run):
            return calls, run_hybrid(mod, self.repo)

    def assert_clean_worktrees(self):
        self.assertEqual(len(git(self.repo, 'worktree', 'list').splitlines()), 1)
        self.assertEqual(git(self.repo, 'branch', '--list', 'sdlc-work/*'), '')
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_independent_tasks_run_concurrently_in_worktrees_and_merge_in_task_order(self):
        for mod in modules():
            self.setUp()
            tasks = [task('T1', owns=['app/one.py']), task('T2', owns=['app/two.py']),
                     task('T3', depends_on=['T1', 'T2'], owns=['app/three.py'])]
            edits = {'worker-T1': {'app/one.py': '1\n'}, 'worker-T2': {'app/two.py': '2\n', 'app/extra.py': 'e\n'},
                     'worker-T3': {'app/three.py': '3\n'}}
            calls, (result, _, _, commits) = self.run_wave(mod, tasks, edits, barrier_parties=2)
            repos = dict(calls)
            self.assertNotEqual(repos['worker-T1'], self.repo)
            self.assertNotEqual(repos['worker-T1'], repos['worker-T2'])
            self.assertEqual(repos['worker-T3'], self.repo)
            self.assertEqual([c['task'] for c in commits], ['T1', 'T2', 'T3'])
            self.assertEqual(git(self.repo, 'log', '--format=%s', '-3').splitlines(), ['[T-1] T3: T1', '[T-1] T2: T1', '[T-1] T1: T1'])
            for name in ('one', 'two', 'three', 'extra'):
                self.assertTrue((self.repo / f'app/{name}.py').is_file())
            self.assertIn('Task T2 changed paths outside owns: app/extra.py', result['deviations'])
            results = json.loads((self.evidence / 'worker-results.json').read_text())
            self.assertEqual([(r['task'], r['wave'], r['workspace'], r['status']) for r in results],
                             [('T1', 1, 'worktree', 'merged'), ('T2', 1, 'worktree', 'merged'), ('T3', 2, 'main', 'committed')])
            self.assertTrue((self.evidence / 'worker-T1.answer.json').is_file())  # copied out of the worktree
            self.assertIn('app/one.py', (self.evidence / 'T1.patch').read_text())
            self.assert_clean_worktrees()
            self.tearDown()

    def test_a_conflicting_task_is_rerun_alone_in_the_main_checkout(self):
        for mod in modules():
            self.setUp()
            tasks = [task('T1', owns=['app/a.py']), task('T2', owns=['app/b.py'])]  # owns understates the real edits
            edits = {'worker-T1': {'app/shared.py': 'a = 1\nb = 20\nc = 3\n'},
                     'worker-T2': {'app/shared.py': 'a = 1\nb = 200\nc = 3\n'},
                     'worker-T2-rerun': {'app/shared.py': 'a = 1\nb = 20\nc = 300\n'}}
            calls, (_, _, _, commits) = self.run_wave(mod, tasks, edits)
            self.assertEqual(dict(calls)['worker-T2-rerun'], self.repo)
            self.assertEqual([(c['task'], c['status']) for c in commits], [('T1', 'merged'), ('T2', 'rerun')])
            self.assertEqual((self.repo / 'app/shared.py').read_text(), 'a = 1\nb = 20\nc = 300\n')
            statuses = [(r['task'], r['status']) for r in json.loads((self.evidence / 'worker-results.json').read_text())]
            self.assertEqual(statuses, [('T1', 'merged'), ('T2', 'conflict'), ('T2', 'rerun')])
            self.assert_clean_worktrees()
            self.tearDown()

    def test_a_failed_worker_lets_its_wave_finish_and_merges_nothing(self):
        for mod in modules():
            self.setUp()
            head = git(self.repo, 'rev-parse', 'HEAD')
            tasks = [task('T1'), task('T2'), task('T3', depends_on=['T1'])]
            edits = {'worker-T1': {'app/t1.py': '1\n'}}
            with self.assertRaisesRegex(mod.WorkflowContractError, 'Worker T2 failed'):
                self.run_wave(mod, tasks, edits, fail=('worker-T2',))
            self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), head)
            self.assertFalse((self.repo / 'app/t1.py').exists())
            self.assertTrue((self.evidence / 'wave-1-failures.json').is_file())
            self.assert_clean_worktrees()
            self.tearDown()

    def test_a_changed_write_boundary_stops_the_wave_before_any_worker(self):
        for mod in modules():
            self.setUp()
            (self.repo / '.claude/settings.json').write_text('{"hooks": {}}\n')  # uncommitted local edit
            with self.assertRaisesRegex(mod.WorkflowContractError, 'settings.json'):
                calls, _ = self.run_wave(mod, [task('T1'), task('T2')], {})
            self.assertEqual(len(git(self.repo, 'worktree', 'list').splitlines()), 1)
            self.tearDown()


class ScheduleTest(unittest.TestCase):
    def test_plan_waves_follow_the_dependency_graph(self):
        for mod in modules():
            schedule = mod.build_schedule(plan_graph(), 4)
            self.assertEqual(schedule['waves'], [['T1', 'T3'], ['T2', 'T6'], ['T4', 'T5'], ['T7']])
            self.assertEqual(schedule['added_dependencies'], [])
            self.assertEqual(schedule['ancestors']['T4'], ['T1', 'T2', 'T3'])

    def test_width_is_capped_and_one_means_sequential(self):
        for mod in modules():
            tasks = [task(f'T{n}') for n in range(1, 7)]
            self.assertEqual(mod.build_schedule(tasks, 4)['waves'], [['T1', 'T2', 'T3', 'T4'], ['T5', 'T6']])
            self.assertEqual(mod.build_schedule(tasks, 1)['waves'], [[f'T{n}'] for n in range(1, 7)])
            for bad in (0, 5):
                with self.assertRaises(mod.WorkflowContractError):
                    mod.build_schedule(tasks, bad)

    def test_overlapping_ownership_serializes_independent_tasks(self):
        for mod in modules():
            tasks = [task('T1', owns=['app/payment_service']), task('T2', owns=['app/payment_service/repo.py']),
                     task('T3', owns=['app/tests/test_a.py']), task('T4', owns=['app/payment_service2.py'])]
            schedule = mod.build_schedule(tasks, 4)
            self.assertEqual(schedule['waves'], [['T1', 'T3', 'T4'], ['T2']])
            self.assertEqual(schedule['added_dependencies'],
                             [{'task': 'T2', 'depends_on': 'T1', 'overlapping_paths': ['app/payment_service', 'app/payment_service/repo.py']}])

    def test_overlap_with_an_existing_dependency_path_adds_nothing(self):
        for mod in modules():
            tasks = [task('T1', owns=['app/a.py']), task('T2', depends_on=['T1'], owns=['app/b.py']),
                     task('T3', depends_on=['T2'], owns=['app/a.py'])]
            schedule = mod.build_schedule(tasks, 4)
            self.assertEqual(schedule['added_dependencies'], [])
            self.assertEqual(schedule['waves'], [['T1'], ['T2'], ['T3']])

    def test_schedule_is_deterministic(self):
        for mod in modules():
            first = mod.build_schedule(plan_graph(), 2)
            self.assertEqual(first, mod.build_schedule(copy.deepcopy(plan_graph()), 2))
            self.assertEqual(first['waves'], [['T1', 'T3'], ['T2', 'T6'], ['T4', 'T5'], ['T7']])


if __name__ == '__main__':
    unittest.main()
