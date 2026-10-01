"""Worktree, per-task commit and ordered cherry-pick mechanics against real temporary Git repositories."""
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic-sdlc/cao'))
stub = types.ModuleType('cao_workflow')
stub.step = lambda *a, **k: None
stub.emit_output = lambda value: None
stub.get_inputs = lambda: {}
sys.modules.setdefault('cao_workflow', stub)
from sdlc_workflows import worktrees as wt
from sdlc_workflows.errors import WorkflowContractError

ROOTS = ['app']


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def make_repo(base: Path) -> Path:
    repo = base / 'repo'
    (repo / 'app').mkdir(parents=True)
    (repo / '.claude/hooks').mkdir(parents=True)
    (repo / '.agentic-sdlc/cao').mkdir(parents=True)
    (repo / '.gitignore').write_text('.agentic-sdlc/runtime/\n')
    (repo / 'app/shared.py').write_text('a = 1\nb = 2\nc = 3\n')
    (repo / '.claude/settings.json').write_text('{}\n')
    (repo / '.claude/hooks/restrict-write-scope.py').write_text('# hook\n')
    (repo / '.agentic-sdlc/cao/specialists.json').write_text('{}\n')
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.email', 'test@example.com')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'initial')
    return repo


def worktree_for(repo: Path, task_id: str) -> Path:
    return wt.add_worktree(repo, repo / '.agentic-sdlc/runtime/run/worktrees' / task_id, f'sdlc-work/run/{task_id}')


class WorktreeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_worktree_is_invisible_to_the_main_checkout_and_holds_the_trusted_files(self):
        tree = worktree_for(self.repo, 'T1')
        (tree / 'app/new.py').write_text('x = 1\n')
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        wt.check_trusted_files(self.repo, tree)
        self.assertEqual(wt.changed_paths(tree, ROOTS), ['app/new.py'])

    def test_changed_trusted_file_stops_before_workers(self):
        (self.repo / '.claude/hooks/restrict-write-scope.py').write_text('# locally edited hook\n')
        tree = worktree_for(self.repo, 'T1')
        with self.assertRaisesRegex(WorkflowContractError, 'restrict-write-scope.py'):
            wt.check_trusted_files(self.repo, tree)

    def test_changed_paths_covers_edits_deletes_renames_and_ignores_other_folders(self):
        (self.repo / 'app/old.py').write_text('o = 1\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'old')
        tree = worktree_for(self.repo, 'T1')
        (tree / 'app/shared.py').write_text('a = 9\nb = 2\nc = 3\n')
        git(tree, 'mv', 'app/old.py', 'app/renamed.py')
        (tree / 'app/sub').mkdir()
        (tree / 'app/sub/new.py').write_text('n = 1\n')
        (tree / 'README.md').write_text('outside the source roots\n')
        (tree / '.agentic-sdlc/runtime/answer').mkdir(parents=True)
        (tree / '.agentic-sdlc/runtime/answer/w.answer.json').write_text('{}')
        self.assertEqual(wt.changed_paths(tree, ROOTS), ['app/old.py', 'app/renamed.py', 'app/shared.py', 'app/sub/new.py'])
        self.assertEqual(wt.changed_paths(tree, ['missing/root']), [])

    def test_outside_owned(self):
        paths = ['app/a.py', 'app/pkg/b.py', 'app/pkg2/c.py']
        self.assertEqual(wt.outside_owned(paths, ['app/a.py', 'app/pkg']), ['app/pkg2/c.py'])

    def test_commit_per_task_and_ordered_cherry_pick(self):
        base = git(self.repo, 'rev-parse', 'HEAD').strip()
        first, second, idle = worktree_for(self.repo, 'T1'), worktree_for(self.repo, 'T2'), worktree_for(self.repo, 'T3')
        (first / 'app/one.py').write_text('one = 1\n')
        (second / 'app/two.py').write_text('two = 2\n')
        sha1 = wt.commit_task(first, ROOTS, '[X] T1: first')
        sha2 = wt.commit_task(second, ROOTS, '[X] T2: second')
        self.assertIsNone(wt.commit_task(idle, ROOTS, '[X] T3: nothing'))
        self.assertIn('app/one.py', wt.task_patch(first, base))
        self.assertTrue(wt.cherry_pick(self.repo, sha1))
        self.assertTrue(wt.cherry_pick(self.repo, sha2))
        self.assertEqual(git(self.repo, 'log', '--format=%s', f'{base}..HEAD').splitlines(), ['[X] T2: second', '[X] T1: first'])
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_conflict_is_aborted_and_reported_for_a_sequential_rerun(self):
        first, second = worktree_for(self.repo, 'T1'), worktree_for(self.repo, 'T2')
        (first / 'app/shared.py').write_text('a = 1\nb = 20\nc = 3\n')
        (second / 'app/shared.py').write_text('a = 1\nb = 200\nc = 3\n')
        sha1 = wt.commit_task(first, ROOTS, '[X] T1')
        sha2 = wt.commit_task(second, ROOTS, '[X] T2')
        self.assertTrue(wt.cherry_pick(self.repo, sha1))
        head = git(self.repo, 'rev-parse', 'HEAD')
        self.assertFalse(wt.cherry_pick(self.repo, sha2))
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), head)
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        self.assertEqual((self.repo / 'app/shared.py').read_text(), 'a = 1\nb = 20\nc = 3\n')

    def test_cleanup_removes_worktree_and_branch_even_with_uncommitted_changes(self):
        tree = worktree_for(self.repo, 'T1')
        (tree / 'app/left.py').write_text('x\n')
        self.assertEqual(wt.remove_worktree(self.repo, tree, 'sdlc-work/run/T1'), [])
        self.assertFalse(tree.exists())
        self.assertNotIn('sdlc-work/run/T1', git(self.repo, 'branch', '--list'))
        self.assertTrue(wt.remove_worktree(self.repo, tree, 'sdlc-work/run/T1'))  # second call only warns

    def test_prune_forgets_a_worktree_deleted_by_hand(self):
        tree = worktree_for(self.repo, 'T1')
        subprocess.run(['rm', '-rf', str(tree)], check=True)
        wt.prune_worktrees(self.repo)
        self.assertEqual(len(git(self.repo, 'worktree', 'list').splitlines()), 1)
        git(self.repo, 'branch', '-D', 'sdlc-work/run/T1')
        worktree_for(self.repo, 'T1')  # the same path and branch can be reused after pruning


if __name__ == '__main__':
    unittest.main()
