"""Per-project provider and model per profile: validation, resolution, step options and loading."""
import json
import re
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
from sdlc_workflows import agent_settings as ags
from sdlc_workflows.errors import WorkflowContractError


class ValidationTest(unittest.TestCase):
    def test_known_profiles_match_the_profile_files(self):
        names = set()
        for path in (ROOT / '.agentic-sdlc/cao/profiles').glob('*.md'):
            match = re.search(r'^name:\s*(\S+)\s*$', path.read_text(encoding='utf-8'), re.MULTILINE)
            self.assertIsNotNone(match, path)
            names.add(match.group(1))
        self.assertEqual(names, set(ags.KNOWN_PROFILES))

    def test_valid_settings_are_normalized(self):
        self.assertEqual(ags.validate_agent_settings({}), {'default': {}, 'profiles': {}})
        raw = {'default': {'provider': 'claude_code', 'model': 'sonnet'},
               'profiles': {'sdlc_pr_reviewer': {'model': 'claude-opus-5-5[1m]'}, 'sdlc_source_mapper': {}}}
        self.assertEqual(ags.validate_agent_settings(raw), {
            'default': {'provider': 'claude_code', 'model': 'sonnet'},
            'profiles': {'sdlc_pr_reviewer': {'model': 'claude-opus-5-5[1m]'}, 'sdlc_source_mapper': {}}})
        for model in ('haiku', 'claude-haiku-5-5', 'gpt-6-luna', 'us.anthropic.claude-x:0', 'org/model-1.5'):
            ags.validate_agent_settings({'default': {'model': model}})

    def test_invalid_settings_are_rejected(self):
        cases = {
            'must be an object': ['x', None, []],
            'unknown keys: effort': [{'effort': 'low'}],
            'unknown keys: workers': [{'workers': {}}],
            'default must be an object': [{'default': 'sonnet'}],
            'default has unknown keys: effort': [{'default': {'effort': 'low'}}],
            'sdlc_implementer has unknown keys: effort':
                [{'profiles': {'sdlc_implementer': {'model': 'opus', 'effort': 'max'}}}],
            'profiles must be an object': [{'profiles': ['sdlc_implementer']}],
            'unknown profile': [{'profiles': {'sdlc_typo': {}}}, {'profiles': {'implementer': {}}}],
            'is not allowed': [{'default': {'provider': 'codex'}},
                               {'profiles': {'sdlc_pr_reviewer': {'provider': 'antigravity_cli'}}},
                               {'default': {'provider': None}}],
            'model must be': [{'default': {'model': ''}}, {'default': {'model': 'two words'}},
                              {'default': {'model': 'x; rm -rf /'}}, {'default': {'model': 5}},
                              {'default': {'model': '-flag'}}, {'default': {'model': 'm' * 101}}],
        }
        for message, values in cases.items():
            for value in values:
                with self.subTest(value=value), self.assertRaisesRegex(WorkflowContractError, message):
                    ags.validate_agent_settings(value)


class ResolutionTest(unittest.TestCase):
    def test_nothing_declared_means_today(self):
        resolved = ags.resolve_agent_settings(None)
        self.assertEqual(set(resolved), set(ags.KNOWN_PROFILES))
        for entry in resolved.values():
            self.assertEqual(entry, {'provider': 'claude_code', 'provider_from': 'unset',
                                     'model': None, 'model_from': 'unset'})
        self.assertEqual(ags.resolve_agent_settings({}), resolved)

    def test_profile_overrides_default_field_by_field(self):
        resolved = ags.resolve_agent_settings({
            'default': {'model': 'sonnet'},
            'profiles': {'sdlc_pr_reviewer': {'model': 'opus'}, 'sdlc_source_mapper': {'provider': 'claude_code'}}})
        self.assertEqual(resolved['sdlc_pr_reviewer'], {'provider': 'claude_code', 'provider_from': 'unset',
                                                        'model': 'opus', 'model_from': 'profile'})
        self.assertEqual(resolved['sdlc_source_mapper'], {'provider': 'claude_code', 'provider_from': 'profile',
                                                          'model': 'sonnet', 'model_from': 'default'})
        self.assertEqual(resolved['sdlc_implementer']['model_from'], 'default')

    def test_step_options(self):
        resolved = ags.resolve_agent_settings({'profiles': {'sdlc_pr_reviewer': {'model': 'opus'}}})
        self.assertEqual(ags.step_options(resolved, 'sdlc_pr_reviewer'), ('claude_code', {'model': 'opus'}))
        self.assertEqual(ags.step_options(resolved, 'sdlc_implementer'), ('claude_code', {}))
        self.assertEqual(ags.step_options(None, 'sdlc_implementer'), ('claude_code', {}))
        self.assertEqual(ags.step_options(resolved, 'not_a_profile'), ('claude_code', {}))


class LoadingTest(unittest.TestCase):
    def repo(self, temp, project=None, registry=None):
        repo = Path(temp)
        (repo / '.agentic-sdlc/cao').mkdir(parents=True)
        if registry is not None:
            (repo / '.agentic-sdlc/cao/specialists.json').write_text(json.dumps(registry))
        if project is not None:
            (repo / 'agentic-sdlc-project.json').write_text(json.dumps(project))
        return repo

    def test_reads_the_project_file(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = self.repo(temp, project={'version': 1, 'agent_settings': {'default': {'model': 'haiku'}}})
            self.assertEqual(ags.load_agent_settings(repo)['sdlc_plan_reviewer']['model'], 'haiku')

    def test_no_files_or_no_key_means_today(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(ags.load_agent_settings(self.repo(temp)), ags.resolve_agent_settings(None))
        with tempfile.TemporaryDirectory() as temp:
            repo = self.repo(temp, project={'version': 1, 'source_roots': ['app']})
            self.assertEqual(ags.load_agent_settings(repo), ags.resolve_agent_settings(None))

    def test_invalid_settings_or_a_key_in_both_files_stop_the_run(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = self.repo(temp, project={'version': 1, 'agent_settings': {'default': {'provider': 'codex'}}})
            with self.assertRaisesRegex(WorkflowContractError, 'not allowed'):
                ags.load_agent_settings(repo)
        with tempfile.TemporaryDirectory() as temp:
            repo = self.repo(temp, project={'version': 1, 'agent_settings': {}},
                             registry={'agent_settings': {}})
            with self.assertRaisesRegex(WorkflowContractError, 'set in both'):
                ags.load_agent_settings(repo)


if __name__ == '__main__':
    unittest.main()
