"""Project rules for the reviewers: settings, rule-file format, selection by changed files, batching and staging."""
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
from sdlc_workflows import review_knowledge as rk
from sdlc_workflows.errors import WorkflowContractError

KB = 'agentic-sdlc-pr-review-kb'


def rule(rule_id, *, level='MANDATORY', domain='Readability', title='A rule', globs=('app/**/*.py',),
         sections=rk.RULE_SECTIONS, body='Text.'):
    lines = [f'## {rule_id} - [{level}] {domain}. {title}', '']
    for section in sections:
        lines += [f'### {section}', '']
        if section == 'Applicable files':
            lines += [f'- `{glob}`' for glob in globs]
        else:
            lines.append(body)
        lines.append('')
    return '\n'.join(lines)


def rule_file(*rules):
    return '# Rules\n\nIntro text.\n\n' + '\n'.join(rules)


class RepoCase(unittest.TestCase):
    def make_repo(self, files, knowledge=None, project_extra=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        (repo / '.agentic-sdlc/cao').mkdir(parents=True)
        (repo / KB).mkdir()
        for name, text in files.items():
            (repo / KB / name).write_text(text, encoding='utf-8')
        project = {'version': 1, **(project_extra or {})}
        if knowledge is not None:
            project['review_knowledge'] = knowledge
        (repo / 'agentic-sdlc-project.json').write_text(json.dumps(project))
        return repo


class SettingsTest(unittest.TestCase):
    def test_defaults_apply_to_every_optional_limit(self):
        settings = rk.validate_knowledge_settings({'files': [f'{KB}/a.md']})
        self.assertEqual(settings, {'files': [f'{KB}/a.md'], 'batch_max_rules': 15, 'batch_max_kb': 32,
                                    'batch_max_files': 5, 'max_files': 32, 'max_rules': 200, 'max_rule_kb': 8})

    def test_every_limit_is_configurable_within_its_bounds(self):
        raw = {'files': [f'{KB}/a.md'], 'batch_max_rules': 50, 'batch_max_kb': 128, 'batch_max_files': 50, 'max_files': 128,
               'max_rules': 1000, 'max_rule_kb': 32}
        self.assertEqual(rk.validate_knowledge_settings(raw), raw)

    def test_invalid_settings_are_rejected(self):
        cases = {
            'not an object': [f'{KB}/a.md'],
            'true is not accepted': True,
            'files missing': {},
            'files empty': {'files': []},
            'unknown key': {'files': [f'{KB}/a.md'], 'batch_size': 3},
            'outside the folder': {'files': ['docs/a.md']},
            'folder itself': {'files': [KB]},
            'not markdown': {'files': [f'{KB}/a.txt']},
            'traversal': {'files': [f'{KB}/../app/a.md']},
            'not normalized': {'files': [f'{KB}//a.md']},
            'glob in path': {'files': [f'{KB}/*.md']},
            'duplicate': {'files': [f'{KB}/a.md', f'{KB}/a.md']},
            'too many files': {'files': [f'{KB}/a.md', f'{KB}/b.md'], 'max_files': 1},
            'limit below range': {'files': [f'{KB}/a.md'], 'batch_max_rules': 0},
            'files per batch above range': {'files': [f'{KB}/a.md'], 'batch_max_files': 51},
            'limit above range': {'files': [f'{KB}/a.md'], 'max_rules': 1001},
            'limit is a boolean': {'files': [f'{KB}/a.md'], 'batch_max_kb': True},
            'limit is a string': {'files': [f'{KB}/a.md'], 'batch_max_kb': '32'},
            'rule larger than a batch': {'files': [f'{KB}/a.md'], 'batch_max_kb': 4, 'max_rule_kb': 8},
        }
        for name, raw in cases.items():
            with self.subTest(case=name), self.assertRaises(WorkflowContractError):
                rk.validate_knowledge_settings(raw)


class ParseTest(unittest.TestCase):
    def parse(self, text, max_bytes=8192):
        return rk.parse_rules(text, f'{KB}/a.md', max_bytes)

    def test_the_sample_rule_file_parses(self):
        path = ROOT / KB / 'code-readability.md'
        rules = rk.parse_rules(path.read_text(encoding='utf-8'), f'{KB}/code-readability.md', 8192)
        self.assertEqual([(r['id'], r['level'], r['domain']) for r in rules],
                         [('NAMING-1', 'MANDATORY', 'Readability'), ('COMMENTS-1', 'MANDATORY', 'Maintainability')])
        self.assertEqual(rules[0]['title'], 'Identifiers are full, self-explanatory names')
        self.assertEqual(rules[0]['globs'], ['app/**/*.py', 'app/**/*.js', 'app/**/*.scala'])
        self.assertTrue(rules[0]['text'].startswith('## NAMING-1 - [MANDATORY]'))
        self.assertIn('class PmtMgr', rules[0]['text'])
        self.assertNotIn('COMMENTS-1', rules[0]['text'])

    def test_levels_titles_and_a_trailing_period(self):
        rules = self.parse(rule_file(rule('A-1', level='NICE-TO-HAVE', domain='Error handling', title='Fail fast.')))
        self.assertEqual((rules[0]['level'], rules[0]['domain'], rules[0]['title']),
                         ('NICE-TO-HAVE', 'Error handling', 'Fail fast'))

    def test_headings_inside_code_examples_are_ignored(self):
        body = '```markdown\n## not a rule\n### Not a section\n```'
        rules = self.parse(rule_file(rule('A-1', body=body), rule('A-2')))
        self.assertEqual([r['id'] for r in rules], ['A-1', 'A-2'])

    def test_format_deviations_are_rejected(self):
        cases = {
            'no rules': '# Rules\n\nNothing here.\n',
            'bad heading': rule_file(rule('A-1')).replace('## A-1 - [MANDATORY]', '## A-1 [MANDATORY]'),
            'unknown level': rule_file(rule('A-1', level='OPTIONAL')),
            'lowercase id': rule_file(rule('A-1')).replace('A-1', 'a-1'),
            'id without number': rule_file(rule('A-1')).replace('A-1', 'NAMING'),
            'no domain': rule_file(rule('A-1')).replace('Readability. ', ''),
            'missing section': rule_file(rule('A-1', sections=rk.RULE_SECTIONS[:-1])),
            'unknown section': rule_file(rule('A-1', sections=rk.RULE_SECTIONS + ('Notes',))),
            'repeated section': rule_file(rule('A-1', sections=rk.RULE_SECTIONS + ('Examples: bad',))),
            'section outside a rule': '# Rules\n\n### Rule description\n\n' + rule('A-1'),
            'no applicable files': rule_file(rule('A-1', globs=())),
            'applicable file without backticks': rule_file(rule('A-1')).replace('- `app/**/*.py`', '- app/**/*.py'),
            'absolute glob': rule_file(rule('A-1', globs=('/app/*.py',))),
            'glob leaves the repo': rule_file(rule('A-1', globs=('../x/*.py',))),
            'unclosed code block': rule_file(rule('A-1', body='```python\nx = 1')),
        }
        for name, text in cases.items():
            with self.subTest(case=name), self.assertRaises(WorkflowContractError):
                self.parse(text)

    def test_a_rule_larger_than_max_rule_kb_is_rejected(self):
        with self.assertRaisesRegex(WorkflowContractError, 'larger than max_rule_kb'):
            self.parse(rule_file(rule('A-1', body='x' * 2000)), max_bytes=1024)


class LoadTest(RepoCase):
    def test_without_review_knowledge_nothing_is_loaded(self):
        self.assertIsNone(rk.load_review_knowledge(self.make_repo({})))

    def test_rules_from_several_files_are_loaded_sorted_with_digests(self):
        repo = self.make_repo({'b.md': rule_file(rule('B-1')), 'a.md': rule_file(rule('A-2'), rule('A-1'))},
                              {'files': [f'{KB}/b.md', f'{KB}/a.md']})
        knowledge = rk.load_review_knowledge(repo)
        self.assertEqual([r['id'] for r in knowledge['rules']], ['A-1', 'A-2', 'B-1'])
        self.assertEqual([f['path'] for f in knowledge['files']], [f'{KB}/b.md', f'{KB}/a.md'])
        self.assertEqual(len(knowledge['files'][0]['sha256']), 64)

    def test_the_registry_is_used_without_a_project_file(self):
        repo = self.make_repo({'a.md': rule_file(rule('A-1'))})
        os.remove(repo / 'agentic-sdlc-project.json')
        (repo / '.agentic-sdlc/cao/specialists.json').write_text(
            json.dumps({'version': 1, 'review_knowledge': {'files': [f'{KB}/a.md']}}))
        self.assertEqual([r['id'] for r in rk.load_review_knowledge(repo)['rules']], ['A-1'])

    def test_load_errors(self):
        cases = {
            'missing file': ({}, {'files': [f'{KB}/a.md']}),
            'duplicate id across files': ({'a.md': rule_file(rule('A-1')), 'b.md': rule_file(rule('A-1'))},
                                          {'files': [f'{KB}/a.md', f'{KB}/b.md']}),
            'too many rules': ({'a.md': rule_file(rule('A-1'), rule('A-2'))}, {'files': [f'{KB}/a.md'], 'max_rules': 1}),
            'not utf-8': ({'a.md': None}, {'files': [f'{KB}/a.md']}),
        }
        for name, (files, knowledge) in cases.items():
            with self.subTest(case=name):
                repo = self.make_repo({k: v for k, v in files.items() if v is not None}, knowledge)
                if None in files.values():
                    (repo / KB / 'a.md').write_bytes(b'\xff\xfe')
                with self.assertRaises(WorkflowContractError):
                    rk.load_review_knowledge(repo)

    def test_a_symlink_out_of_the_rules_folder_is_rejected(self):
        repo = self.make_repo({}, {'files': [f'{KB}/link.md']})
        (repo / 'outside.md').write_text(rule_file(rule('A-1')))
        os.symlink(repo / 'outside.md', repo / KB / 'link.md')
        with self.assertRaisesRegex(WorkflowContractError, 'resolves outside'):
            rk.load_review_knowledge(repo)

    def test_review_knowledge_in_both_files_is_refused(self):
        repo = self.make_repo({'a.md': rule_file(rule('A-1'))}, {'files': [f'{KB}/a.md']})
        (repo / '.agentic-sdlc/cao/specialists.json').write_text(
            json.dumps({'version': 1, 'review_knowledge': {'files': [f'{KB}/a.md']}}))
        with self.assertRaisesRegex(WorkflowContractError, 'set in both'):
            rk.load_review_knowledge(repo)

    def test_the_real_project_configuration_loads(self):
        knowledge = rk.load_review_knowledge(ROOT)
        self.assertEqual([r['id'] for r in knowledge['rules']], ['COMMENTS-1', 'NAMING-1'])


class SelectTest(unittest.TestCase):
    def knowledge(self):
        return {'rules': [
            {'id': 'PY-1', 'globs': ['app/**/*.py'], 'text': ''},
            {'id': 'UI-1', 'globs': ['app/ui/**/*.js', 'app/ui/**/*.html'], 'text': ''},
            {'id': 'TOP-1', 'globs': ['app/*.py'], 'text': ''},
        ]}

    def test_rules_match_by_glob_and_list_the_files_they_cover(self):
        selection = rk.select_rules(self.knowledge(), ['app/main.py', 'app/pay/service.py', 'docs/x.md', 'app/main.py'])
        self.assertEqual([(r['id'], r['covers']) for r in selection['applicable']],
                         [('PY-1', ['app/main.py', 'app/pay/service.py']), ('TOP-1', ['app/main.py'])])
        self.assertEqual(selection['skipped'], ['UI-1'])

    def test_no_changed_file_means_every_rule_is_skipped(self):
        selection = rk.select_rules(self.knowledge(), [])
        self.assertEqual((selection['applicable'], selection['skipped']), ([], ['PY-1', 'UI-1', 'TOP-1']))


def synthetic(count, *, domains=('Alpha',), size=100):
    return [{'id': f'R-{index}', 'domain': domains[index % len(domains)], 'text': 'x' * size, 'covers': ['app/a.py']}
            for index in range(1, count + 1)]


class BatchTest(unittest.TestCase):
    SETTINGS = {'batch_max_rules': 15, 'batch_max_kb': 32, 'batch_max_files': 5}

    def ids(self, batches):
        return [[r['id'] for r in batch] for batch in batches]

    def test_no_rules_no_batches(self):
        self.assertEqual(rk.build_batches([], self.SETTINGS), [])

    def test_fifty_rules_give_four_balanced_batches(self):
        batches = rk.build_batches(synthetic(50), self.SETTINGS)
        self.assertEqual([len(b) for b in batches], [13, 13, 13, 11])

    def test_size_budget_adds_batches(self):
        batches = rk.build_batches(synthetic(10, size=6 * 1024), self.SETTINGS)
        self.assertTrue(all(sum(len(r['text']) for r in b) <= 32 * 1024 for b in batches))
        self.assertEqual([len(b) for b in batches], [5, 5])

    def test_every_rule_appears_once_and_domains_stay_together(self):
        rules = synthetic(40, domains=('Naming', 'Comments', 'Errors'))
        batches = rk.build_batches(rules, {'batch_max_rules': 10, 'batch_max_kb': 32, 'batch_max_files': 5})
        flat = [r['id'] for b in batches for r in b]
        self.assertEqual(sorted(flat), sorted(r['id'] for r in rules))
        domains = [r['domain'] for b in batches for r in b]
        self.assertEqual(domains, sorted(domains, key=str.lower))
        self.assertTrue(all(len(b) <= 10 for b in batches))

    def test_batches_are_deterministic_regardless_of_input_order(self):
        rules = synthetic(33, domains=('B', 'A'))
        self.assertEqual(self.ids(rk.build_batches(rules, self.SETTINGS)),
                         self.ids(rk.build_batches(list(reversed(rules)), self.SETTINGS)))

    def test_files_are_split_into_even_chunks_and_every_rule_file_pair_is_reviewed_once(self):
        files = [f'app/m{n}.py' for n in range(1, 8)]
        rules = [{'id': 'A-1', 'domain': 'D', 'text': 'x', 'covers': files},
                 {'id': 'A-2', 'domain': 'D', 'text': 'x', 'covers': ['app/m1.py', 'app/m7.py']},
                 {'id': 'A-3', 'domain': 'D', 'text': 'x', 'covers': ['app/m2.py']}]
        batches = rk.build_batches(rules, self.SETTINGS)
        self.assertEqual(rk.batch_summary(batches), [
            {'rules': ['A-1', 'A-2', 'A-3'], 'files': files[:4]},
            {'rules': ['A-1', 'A-2'], 'files': files[4:]},
        ])
        self.assertEqual([r['covers'] for r in batches[0]], [files[:4], ['app/m1.py'], ['app/m2.py']])
        pairs = [(r['id'], f) for b in batches for r in b for f in r['covers']]
        self.assertEqual(sorted(pairs), sorted((r['id'], f) for r in rules for f in r['covers']))
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_a_cap_of_one_file_gives_one_batch_per_file(self):
        rules = [{'id': 'A-1', 'domain': 'D', 'text': 'x', 'covers': ['b.py', 'a.py', 'c.py']}]
        batches = rk.build_batches(rules, {**self.SETTINGS, 'batch_max_files': 1})
        self.assertEqual([rk.batch_files(b) for b in batches], [['a.py'], ['b.py'], ['c.py']])

    def test_configured_batch_size_is_respected(self):
        self.assertEqual([len(b) for b in rk.build_batches(synthetic(7), {'batch_max_rules': 1, 'batch_max_kb': 4, 'batch_max_files': 5})],
                         [1] * 7)


class StageTest(RepoCase):
    def test_staging_writes_rule_files_batch_indexes_and_the_audit_index(self):
        repo = self.make_repo({'a.md': rule_file(rule('A-1', title='First'), rule('A-2', title='Second'))},
                              {'files': [f'{KB}/a.md'], 'batch_max_rules': 1})
        knowledge = rk.load_review_knowledge(repo)
        selection = rk.select_rules(knowledge, ['app/x.py'])
        batches = rk.build_batches(selection['applicable'], knowledge['settings'])
        destination = repo / 'staged'
        indexes = rk.stage_rules(batches, selection, knowledge, destination)
        self.assertEqual([p.name for p in indexes], ['index-b1.md', 'index-b2.md'])
        self.assertIn('- A-1 [MANDATORY] Readability. First (rules/A-1.md); changed files: app/x.py',
                      indexes[0].read_text())
        self.assertIn('Changed files in this batch: app/x.py', indexes[0].read_text())
        self.assertNotIn('A-2', indexes[0].read_text())
        self.assertEqual((destination / 'rules/A-2.md').read_text(), selection['applicable'][1]['text'])
        audit = json.loads((destination / 'index.json').read_text())
        self.assertEqual(audit['batches'], [{'rules': ['A-1'], 'files': ['app/x.py']},
                                            {'rules': ['A-2'], 'files': ['app/x.py']}])
        self.assertEqual(audit['files'], knowledge['files'])
        self.assertNotIn('files', audit['settings'])



class FileVerdictTest(unittest.TestCase):
    BATCH = [{'id': 'A-1', 'covers': ['a.py', 'b.py']}, {'id': 'A-2', 'covers': ['a.py']}]

    def entry(self, rule_id, **files):
        return {'id': rule_id, 'files': [{'path': path.replace('_', '.'), 'status': status, 'finding_ids': ids}
                                         for path, (status, ids) in files.items()]}

    def test_a_complete_answer_gives_verdicts_per_rule_and_file(self):
        raw = [self.entry('A-1', a_py=('VIOLATED', ['F1']), b_py=('NOT_VIOLATED', [])),
               self.entry('A-2', a_py=('NOT_ASSESSED', []))]
        verdicts, gaps = rk.check_file_verdicts(raw, self.BATCH, {('A-1', 'a.py'): ['F1']})
        self.assertEqual(verdicts, {'A-1': {'a.py': 'VIOLATED', 'b.py': 'NOT_VIOLATED'}, 'A-2': {'a.py': 'NOT_ASSESSED'}})
        self.assertEqual(gaps, [])
        self.assertEqual(rk.rule_status(verdicts['A-1']), 'VIOLATED')
        self.assertEqual(rk.rule_status({'a.py': 'NOT_VIOLATED', 'b.py': 'NOT_ASSESSED'}), 'NOT_ASSESSED')
        self.assertEqual(rk.not_assessed_pairs({'A-1': {'a.py': 'VIOLATED', 'b.py': 'NOT_ASSESSED'}}), ['A-1 in b.py'])

    def test_contract_errors(self):
        citing = {('A-1', 'a.py'): ['F1']}
        cases = {
            'a covered file is missing': [self.entry('A-1', a_py=('VIOLATED', ['F1'])), self.entry('A-2', a_py=('NOT_VIOLATED', []))],
            'a rule is missing': [self.entry('A-1', a_py=('VIOLATED', ['F1']), b_py=('NOT_VIOLATED', []))],
            'a file the rule does not cover here': [self.entry('A-1', a_py=('VIOLATED', ['F1']), b_py=('NOT_VIOLATED', [])),
                                                    self.entry('A-2', a_py=('NOT_VIOLATED', []), b_py=('NOT_VIOLATED', []))],
            'findings in a file not VIOLATED': [self.entry('A-1', a_py=('NOT_VIOLATED', []), b_py=('NOT_VIOLATED', [])),
                                                self.entry('A-2', a_py=('NOT_VIOLATED', []))],
            'VIOLATED without its findings': [self.entry('A-1', a_py=('VIOLATED', ['F1']), b_py=('VIOLATED', ['F1'])),
                                              self.entry('A-2', a_py=('NOT_VIOLATED', []))],
            'old per-rule shape': [{'id': 'A-1', 'status': 'VIOLATED', 'finding_ids': ['F1']},
                                   {'id': 'A-2', 'status': 'NOT_VIOLATED', 'finding_ids': []}],
            'not an array': {'A-1': 'VIOLATED'},
        }
        for name, raw in cases.items():
            with self.subTest(name), self.assertRaises(WorkflowContractError):
                rk.check_file_verdicts(raw, self.BATCH, citing)

    def test_the_last_attempt_turns_missing_verdicts_into_gaps(self):
        raw = [self.entry('A-1', b_py=('NOT_VIOLATED', []))]
        verdicts, gaps = rk.check_file_verdicts(raw, self.BATCH, {('A-1', 'a.py'): ['F1']}, fill_missing=True)
        self.assertEqual(verdicts, {'A-1': {'a.py': 'VIOLATED', 'b.py': 'NOT_VIOLATED'}, 'A-2': {'a.py': 'NOT_ASSESSED'}})
        self.assertEqual(gaps, ['The reviewer gave rule A-1 no verdict for a.py',
                                'The reviewer gave rule A-2 no verdict for a.py: not assessed'])
        with self.assertRaises(WorkflowContractError):  # what is given must still be right
            rk.check_file_verdicts([self.entry('A-1', b_py=('VIOLATED', []))], self.BATCH, {}, fill_missing=True)


class DiffForFilesTest(unittest.TestCase):
    def test_keeps_only_the_named_files(self):
        diff = ('diff --git a/app/a.py b/app/a.py\n--- a/app/a.py\n+++ b/app/a.py\n@@ -1 +1 @@\n-x\n+y\n'
                'diff --git a/app/b.py b/app/b.py\nnew file mode 100644\n--- /dev/null\n+++ b/app/b.py\n@@ -0,0 +1 @@\n+z\n'
                'diff --git a/old.py b/app/c.py\nsimilarity index 90%\nrename from old.py\nrename to app/c.py\n')
        self.assertEqual(rk.diff_for_files(diff, ['app/b.py', 'app/c.py']),
                         'diff --git a/app/b.py b/app/b.py\nnew file mode 100644\n--- /dev/null\n+++ b/app/b.py\n@@ -0,0 +1 @@\n+z\n'
                         'diff --git a/old.py b/app/c.py\nsimilarity index 90%\nrename from old.py\nrename to app/c.py\n')
        self.assertEqual(rk.diff_for_files(diff, []), '')


class AuthorRulesTest(unittest.TestCase):
    RULES = [
        {'id': 'PY-1', 'level': 'MANDATORY', 'domain': 'Docs', 'title': 'Docstrings', 'globs': ['app/**/*.py'], 'text': '## PY-1\n'},
        {'id': 'UI-1', 'level': 'NICE-TO-HAVE', 'domain': 'UI', 'title': 'Components', 'globs': ['app/ui/**/*.js'], 'text': '## UI-1\n'},
        {'id': 'ANY-1', 'level': 'MANDATORY', 'domain': 'All', 'title': 'Everywhere', 'globs': ['**/*.py'], 'text': '## ANY-1\n'},
        {'id': 'ONE-1', 'level': 'MANDATORY', 'domain': 'One', 'title': 'One file', 'globs': ['app/pay/service.py'], 'text': '## ONE-1\n'},
    ]

    def ids(self, paths):
        return [r['id'] for r in self.RULES if rk.rule_may_apply(r, paths)]

    def test_rules_that_may_apply_under_files_and_folders(self):
        self.assertEqual(self.ids(['app']), ['PY-1', 'UI-1', 'ANY-1', 'ONE-1'])
        self.assertEqual(self.ids(['app/pay']), ['PY-1', 'ANY-1', 'ONE-1'])
        self.assertEqual(self.ids(['app/pay/service.py']), ['PY-1', 'ANY-1', 'ONE-1'])
        self.assertEqual(self.ids(['app/pay/other.py']), ['PY-1', 'ANY-1'])
        self.assertEqual(self.ids(['app/ui/button.js']), ['UI-1'])
        self.assertEqual(self.ids(['docs']), ['ANY-1'])
        self.assertEqual(self.ids([]), [])

    def test_guidance_lists_the_rules_that_may_apply_and_every_cited_rule(self):
        knowledge = {'rules': self.RULES}
        text = rk.author_rules_guidance(knowledge, ['app/ui/button.js'], Path('/staged/rules'), ['ONE-1'])
        self.assertIn('/staged/rules/<RULE-ID>.md', text)
        self.assertIn('- UI-1 [NICE-TO-HAVE] UI. Components (applies to app/ui/**/*.js)', text)
        self.assertIn('- ONE-1 [MANDATORY]', text)
        self.assertNotIn('PY-1', text)
        self.assertEqual(rk.author_rules_guidance(None, ['app'], Path('/x')), '')
        self.assertEqual(rk.author_rules_guidance({'rules': [self.RULES[3]]}, ['app/ui'], Path('/x')), '')

    def test_every_rule_is_staged_for_the_authors(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = rk.stage_author_rules({'rules': self.RULES}, Path(temp))
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ['ANY-1.md', 'ONE-1.md', 'PY-1.md', 'UI-1.md'])


if __name__ == '__main__':
    unittest.main()
