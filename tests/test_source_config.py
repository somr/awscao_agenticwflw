"""Source-root configuration: one validator contract, three implementations that must agree.

The validator lives in the workflow package (modular and deployed-bundle forms) and, as a
standalone copy, in the write-scope hook. Every case below runs through all three, so a rule
added to one and forgotten in another fails here.
"""
import importlib
import importlib.util
import json
import os
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
from build_workflow import build_source

DEFAULTS = {'source_roots': ['app'],
            'write_profiles': {'sdlc_implementer': ['app'], 'sdlc_remediator': ['app']}}


def implementations():
    """(label, module) for the modular package, the deployed bundle and the hook's standalone copy."""
    bundle = types.ModuleType('source_config_bundle')
    exec(compile(build_source('deliver'), '<deliver>', 'exec'), bundle.__dict__)
    spec = importlib.util.spec_from_file_location('restrict_write_scope_hook', ROOT / '.claude/hooks/restrict-write-scope.py')
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return [('modular', importlib.import_module('sdlc_workflows.source_config')), ('bundle', bundle), ('hook', hook)]


def cfg(source_roots=None, write_profiles=None):
    value = {}
    if source_roots is not None:
        value['source_roots'] = source_roots
    if write_profiles is not None:
        value['write_profiles'] = write_profiles
    return value


VALID = {
    'empty registry uses the defaults': ({}, DEFAULTS),
    'explicit defaults': (cfg(['app'], {'sdlc_implementer': None, 'sdlc_remediator': None}), DEFAULTS),
    'multi-module roots': (cfg(['billing/src', 'billing/test', 'shared/lib']),
                           {'source_roots': ['billing/src', 'billing/test', 'shared/lib'],
                            'write_profiles': {'sdlc_implementer': ['billing/src', 'billing/test', 'shared/lib'],
                                               'sdlc_remediator': ['billing/src', 'billing/test', 'shared/lib']}}),
    'a specialist limited to part of a root (equal or inside a root)': (
        cfg(['billing', 'shared'], {'sdlc_implementer': None, 'sdlc_java': ['billing/src', 'shared']}),
        {'source_roots': ['billing', 'shared'],
         'write_profiles': {'sdlc_implementer': ['billing', 'shared'], 'sdlc_java': ['billing/src', 'shared']}}),
    'nested roots are allowed': (cfg(['app', 'app/tests'], {'sdlc_implementer': None}),
                                 {'source_roots': ['app', 'app/tests'], 'write_profiles': {'sdlc_implementer': ['app', 'app/tests']}}),
    'a module that does not exist yet': (cfg(['newmod/src'], {'sdlc_implementer': None}),
                                         {'source_roots': ['newmod/src'], 'write_profiles': {'sdlc_implementer': ['newmod/src']}}),
    'sixteen roots': (cfg([f'm{i}/src' for i in range(16)], {'sdlc_implementer': None}),
                      {'source_roots': [f'm{i}/src' for i in range(16)],
                       'write_profiles': {'sdlc_implementer': [f'm{i}/src' for i in range(16)]}}),
    'unknown extra keys are ignored': ({'version': 1, 'workers': {}, 'source_roots': ['app']}, None),
}

INVALID = {
    'registry is not an object': ['app'],
    'source_roots is a string': {'source_roots': 'app'},
    'source_roots is empty': {'source_roots': []},
    'seventeen roots': {'source_roots': [f'm{i}' for i in range(17)]},
    'root is not a string': {'source_roots': [1]},
    'root is null': {'source_roots': [None]},
    'root is a boolean': {'source_roots': [True]},
    'root is empty': {'source_roots': ['']},
    'absolute root': {'source_roots': ['/etc']},
    'windows drive root': {'source_roots': ['C:/src']},
    'parent segment first': {'source_roots': ['../x']},
    'parent segment inside': {'source_roots': ['a/../b']},
    'bare parent': {'source_roots': ['..']},
    'dot': {'source_roots': ['.']},
    'leading ./': {'source_roots': ['./app']},
    'trailing slash': {'source_roots': ['app/']},
    'double slash': {'source_roots': ['app//src']},
    'inner dot segment': {'source_roots': ['app/./src']},
    'glob star': {'source_roots': ['app/*']},
    'glob question': {'source_roots': ['a?b']},
    'glob bracket': {'source_roots': ['a[1]']},
    'backslash': {'source_roots': ['a\\b']},
    'control character': {'source_roots': ['a\nb']},
    'git pathspec magic': {'source_roots': [':(top)a']},
    'git pathspec exclude': {'source_roots': [':!x']},
    'the git directory': {'source_roots': ['.git']},
    'inside the git directory': {'source_roots': ['.git/hooks']},
    'the claude directory': {'source_roots': ['.claude']},
    'inside the claude directory': {'source_roots': ['.claude/hooks']},
    'the sdlc tooling directory': {'source_roots': ['.agentic-sdlc']},
    'the runtime directory': {'source_roots': ['.agentic-sdlc/runtime']},
    'the records directory': {'source_roots': ['agentic-sdlc-records']},
    'inside the records directory': {'source_roots': ['agentic-sdlc-records/T-1']},
    'the docs directory': {'source_roots': ['agentic-sdlc-docs']},
    'the local-inputs directory': {'source_roots': ['agentic-sdlc-local-inputs/x']},
    'duplicate root': {'source_roots': ['app', 'app']},
    'write_profiles is a list': {'write_profiles': ['sdlc_implementer']},
    'write_profiles is empty': {'write_profiles': {}},
    'empty profile name': {'write_profiles': {'': None}},
    'supervisor may not write': {'write_profiles': {'sdlc_code_supervisor': None}},
    'pr reviewer may not write': {'write_profiles': {'sdlc_pr_reviewer': None}},
    'planning author may not write': {'write_profiles': {'sdlc_plan_author': None}},
    'normalizer may not write': {'write_profiles': {'sdlc_context_normalizer': None}},
    'source-review profiles may not write': {'write_profiles': {'sdlc_source_mapper': None}},
    'profile value is a string': {'write_profiles': {'sdlc_implementer': 'app'}},
    'profile value is a number': {'write_profiles': {'sdlc_implementer': 5}},
    'profile value is an empty list': {'write_profiles': {'sdlc_implementer': []}},
    'profile root outside every source root': {'source_roots': ['app'], 'write_profiles': {'sdlc_implementer': ['other']}},
    'profile root is a sibling with the same prefix': {'source_roots': ['app'], 'write_profiles': {'sdlc_implementer': ['application']}},
    'profile root duplicated': {'source_roots': ['app'], 'write_profiles': {'sdlc_implementer': ['app', 'app']}},
    'profile root escapes': {'source_roots': ['app'], 'write_profiles': {'sdlc_implementer': ['app/../x']}},
}


class ValidatorParityTest(unittest.TestCase):
    def test_valid_configs_give_the_same_result_everywhere(self):
        for label, module in implementations():
            for name, (raw, expected) in VALID.items():
                with self.subTest(implementation=label, case=name):
                    result = module.validate_source_config(raw)
                    if expected is not None:
                        self.assertEqual(result, expected)
                    self.assertEqual(result, implementations_result(raw))

    def test_invalid_configs_are_rejected_everywhere(self):
        for label, module in implementations():
            for name, raw in INVALID.items():
                with self.subTest(implementation=label, case=name):
                    with self.assertRaises(ValueError):
                        module.validate_source_config(raw)

    def test_none_means_the_defaults(self):
        for label, module in implementations():
            with self.subTest(implementation=label):
                self.assertEqual(module.validate_source_config(None), DEFAULTS)


_REFERENCE = None


def implementations_result(raw):
    """The modular implementation's answer, used as the reference the other two must equal."""
    global _REFERENCE
    if _REFERENCE is None:
        _REFERENCE = importlib.import_module('sdlc_workflows.source_config')
    return _REFERENCE.validate_source_config(raw)


class LoaderAndResolverTest(unittest.TestCase):
    def modules(self):
        return [m for label, m in implementations() if label != 'hook']

    def repo_with_registry(self, temp, text=None):
        repo = Path(temp)
        (repo / '.agentic-sdlc/cao').mkdir(parents=True)
        if text is not None:
            (repo / '.agentic-sdlc/cao/specialists.json').write_text(text)
        return repo

    def test_missing_registry_means_the_defaults(self):
        for module in self.modules():
            with tempfile.TemporaryDirectory() as temp:
                self.assertEqual(module.load_source_config(self.repo_with_registry(temp)), DEFAULTS)

    def test_valid_registry_is_read(self):
        text = json.dumps({'version': 1, 'source_roots': ['billing/src'], 'write_profiles': {'sdlc_implementer': None}})
        for module in self.modules():
            with tempfile.TemporaryDirectory() as temp:
                result = module.load_source_config(self.repo_with_registry(temp, text))
                self.assertEqual(result, {'source_roots': ['billing/src'], 'write_profiles': {'sdlc_implementer': ['billing/src']}})

    def test_present_but_broken_registry_never_falls_back_to_defaults(self):
        for text in ('{not json', '[]', '"app"', json.dumps({'source_roots': ['..']}), '\xff'):
            for module in self.modules():
                with self.subTest(text=text[:20], module=module.__name__):
                    with tempfile.TemporaryDirectory() as temp:
                        repo = self.repo_with_registry(temp)
                        (repo / '.agentic-sdlc/cao/specialists.json').write_bytes(text.encode('latin-1'))
                        with self.assertRaises(module.WorkflowContractError):
                            module.load_source_config(repo)

    def test_resolver_accepts_existing_and_missing_roots(self):
        for module in self.modules():
            with tempfile.TemporaryDirectory() as temp:
                repo = Path(temp).resolve()
                (repo / 'billing/src').mkdir(parents=True)
                resolved = module.resolve_source_roots(repo, ['billing/src', 'newmod/src'])
                self.assertEqual(resolved, [repo / 'billing/src', repo / 'newmod/src'])

    def test_resolver_refuses_symlinks_that_leave_the_repository_or_hit_sdlc_folders(self):
        for module in self.modules():
            with tempfile.TemporaryDirectory() as temp:
                base = Path(temp).resolve()
                repo = base / 'repo'
                (repo / '.git').mkdir(parents=True)
                (repo / 'agentic-sdlc-records').mkdir()
                outside = base / 'outside'
                outside.mkdir()
                os.symlink(outside, repo / 'escape')
                os.symlink(repo / '.git', repo / 'gitlink')
                os.symlink(repo / 'agentic-sdlc-records', repo / 'recordlink')
                os.symlink(repo, repo / 'selflink')
                (repo / 'real').mkdir()
                os.symlink(outside, repo / 'real' / 'inner')
                for root in ('escape', 'gitlink', 'recordlink', 'selflink', 'real/inner', 'escape/sub'):
                    with self.subTest(module=module.__name__, root=root):
                        with self.assertRaises(module.WorkflowContractError):
                            module.resolve_source_roots(repo, [root])


if __name__ == '__main__':
    unittest.main()
