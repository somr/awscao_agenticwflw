"""Follow-up on review comments: reading threads, scope (F3, F4, F8, F11) and joining findings."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
stub = types.ModuleType('cao_workflow')
stub.emit_output = lambda value: None
stub.get_inputs = lambda: {}
stub.step = lambda *args, **kwargs: None
sys.modules.setdefault('cao_workflow', stub)
sys.path.insert(0, str(ROOT / '.agentic-sdlc' / 'cao'))
from sdlc_workflows import review_threads as rt
from sdlc_workflows.errors import WorkflowContractError
from sdlc_workflows.review_comments import finding_comment_marker

REPO = 'owner/repo'
HEAD = 'b' * 40


def comment(cid, author, body, commit=HEAD):
    return {'databaseId': cid, 'author': {'login': author}, 'body': body, 'createdAt': '2026-11-18T10:00:00Z',
            'url': f'https://github.com/{REPO}/pull/1#discussion_r{cid}', 'commit': {'oid': commit},
            'originalCommit': {'oid': commit}, 'replyTo': None, 'pullRequestReview': {'databaseId': 9}}


def thread(tid, comments, resolved=False, path='code.py', line=2, start=None, side='RIGHT'):
    return {'id': tid, 'isResolved': resolved, 'isOutdated': False, 'path': path, 'line': line, 'startLine': start,
            'originalLine': line, 'originalStartLine': start, 'diffSide': side, 'startDiffSide': None,
            'subjectType': 'LINE', 'comments': {'totalCount': len(comments), 'nodes': comments}}


def report(run_id, findings):
    return {'run_id': run_id, 'status': 'REVIEWED', 'findings': findings,
            'snapshot': {'repository': REPO, 'pr_number': '1', 'pr_url': f'https://github.com/{REPO}/pull/1',
                         'base_sha': 'a' * 40, 'head_sha': HEAD, 'merge_base_sha': 'a' * 40}}


def finding(fid, file='code.py', line=2):
    return {'stable_id': fid, 'file': file, 'side': 'head', 'line_start': line, 'line_end': line,
            'title': f'Defect {fid}', 'reviewed_head_sha': HEAD, 'standard_ref': None}


class ThreadsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)

    def record(self, run_id, findings, receipt, layout='records'):
        folder = (self.repo / rt.REVIEW_RECORDS / 'repo-pr1' / run_id if layout == 'records'
                  else self.repo / rt.REVIEW_RUNTIME / run_id)
        folder.mkdir(parents=True)
        value = report(run_id, findings)
        (folder / 'code-review.json').write_text(json.dumps(value))
        (folder / 'publication.json').write_text(json.dumps(receipt))
        return value

    def test_allow_list_is_pinned_and_refuses_other_calls(self):
        self.assertEqual(rt.ALLOWED_READS, ('user', 'pull', 'reviews', 'issue_comments', 'threads', 'files'))
        with self.assertRaises(WorkflowContractError):
            rt.followup_github('replies', REPO, '1')
        with self.assertRaises(WorkflowContractError):
            rt.followup_github('pull', 'owner/repo;rm', '1')
        with self.assertRaises(WorkflowContractError):
            rt.followup_github('pull', REPO, '0')

    def test_threads_query_is_read_only(self):
        self.assertTrue(rt.THREADS_QUERY.lstrip().startswith('query('))
        self.assertNotIn('mutation', rt.THREADS_QUERY)

    def test_fetch_threads_follows_pages(self):
        pages = {None: (['t1'], True, 'c1'), 'c1': (['t2'], False, None)}
        seen = []

        def github(call, repository, number, cursor=None):
            seen.append((call, cursor))
            nodes, more, end = pages[cursor]
            return {'data': {'repository': {'pullRequest': {'reviewThreads': {
                'nodes': nodes, 'pageInfo': {'hasNextPage': more, 'endCursor': end}}}}}}

        self.assertEqual(rt.fetch_threads(REPO, '1', github), ['t1', 't2'])
        self.assertEqual(seen, [('threads', None), ('threads', 'c1')])

    def test_graphql_errors_stop_the_run(self):
        with self.assertRaisesRegex(WorkflowContractError, 'no pull request'):
            rt.fetch_threads(REPO, '1', lambda *a: {'data': None, 'errors': [{'message': 'not found'}]})

    def test_followup_marker_round_trip(self):
        marker = rt.followup_marker('run-1', 'PRRT_kwDO-x_y', 'ADDRESSED')
        self.assertEqual(rt.FOLLOWUP_MARKER.findall(marker), [('run-1', 'PRRT_kwDO-x_y', 'ADDRESSED')])
        with self.assertRaises(WorkflowContractError):
            rt.followup_marker('run-1', 'bad subject', 'ADDRESSED')

    def test_scope_and_skips(self):
        addressed = rt.followup_marker('fu-1', 'T4', 'ADDRESSED')
        conversation = {'login': 'Rev', 'reviews': [], 'issue_comments': [], 'threads': [
            thread('T1', [comment(1, 'rev', 'Please rename this.')]),
            thread('T2', [comment(2, 'dev', 'Question from the developer')]),
            thread('T3', [comment(3, 'rev', 'Fix it')], resolved=True),
            thread('T4', [comment(4, 'rev', 'Fix it'), comment(5, 'dev', 'done'), comment(6, 'rev', 'Yes. ' + addressed)]),
            # A developer pasting the marker does not count.
            thread('T5', [comment(7, 'rev', 'Fix it'), comment(8, 'dev', rt.followup_marker('x', 'T5', 'ADDRESSED'))]),
            # An earlier verdict other than ADDRESSED is judged again.
            thread('T6', [comment(9, 'rev', 'Fix it'), comment(10, 'rev', rt.followup_marker('fu-1', 'T6', 'NOT_ADDRESSED'))]),
            thread('T7', [comment(11, 'mate', 'Teammate request')]),
        ]}
        selected = rt.select_threads(conversation, ['Mate'], {'by_marker': {}, 'by_comment': {}, 'general': []})
        self.assertEqual([t['thread_id'] for t in selected['threads']], ['T1', 'T5', 'T6', 'T7'])
        self.assertEqual({s['thread_id']: s['reason'] for s in selected['skipped']},
                         {'T2': 'NOT_REVIEWER', 'T3': 'RESOLVED', 'T4': 'ADDRESSED_EARLIER'})
        self.assertEqual(selected['reviewers'], ['mate', 'rev'])
        first = selected['threads'][0]
        self.assertEqual((first['kind'], first['side'], first['line_start'], first['line_end'], first['commit']),
                         ('HAND_WRITTEN', 'RIGHT', 2, 2, HEAD))
        self.assertFalse(selected['threads'][1]['replies'][0]['by_reviewer'])

    def test_tool_threads_join_by_marker_receipt_or_legacy_location(self):
        new = self.record('run-new', [finding('SR-1'), finding('SR-2', line=5)],
                          {'review': {'id': 9}, 'decisions': [{'id': 'SR-1', 'outcome': 'INLINE'},
                                                              {'id': 'SR-2', 'outcome': 'INLINE'}],
                           'finding_comments': {'SR-2': {'id': 22}}})
        self.record('run-old', [finding('SR-3', line=7)],
                    {'review': {'id': 8}, 'decisions': [{'id': 'SR-3', 'outcome': 'INLINE', 'path': 'code.py',
                                                         'side': 'RIGHT', 'line_end': 7}],
                     'comments': [{'id': 33, 'path': 'code.py', 'side': 'RIGHT', 'line': 7}]}, layout='runtime')
        published = rt.published_findings(self.repo, REPO, '1')
        conversation = {'login': 'rev', 'reviews': [], 'issue_comments': [], 'threads': [
            thread('A', [comment(11, 'rev', 'Text\n\n' + finding_comment_marker(new, 'SR-1'))]),
            thread('B', [comment(22, 'rev', 'Edited text without marker')]),
            thread('C', [comment(33, 'rev', 'Legacy text')]),
            # A marker for a finding of another run or PR is not joined.
            thread('D', [comment(44, 'rev', 'x\n<!-- cao-source-finding:' + 'f' * 64 + ' -->')]),
        ]}
        selected = rt.select_threads(conversation, [], published)
        joined = {t['thread_id']: (t['kind'], t.get('finding', {}).get('stable_id'), t.get('review_run_id'))
                  for t in selected['threads']}
        self.assertEqual(joined, {'A': ('TOOL', 'SR-1', 'run-new'), 'B': ('TOOL', 'SR-2', 'run-new'),
                                  'C': ('TOOL', 'SR-3', 'run-old'), 'D': ('HAND_WRITTEN', None, None)})

    def test_unpublished_and_other_pr_runs_are_ignored(self):
        folder = self.repo / rt.REVIEW_RECORDS / 'repo-pr1' / 'unpublished'
        folder.mkdir(parents=True)
        (folder / 'code-review.json').write_text(json.dumps(report('unpublished', [finding('SR-9')])))
        other = report('other-pr', [finding('SR-8')])
        other['snapshot']['pr_number'] = '2'
        legacy = self.repo / rt.REVIEW_RUNTIME / 'other-pr'
        legacy.mkdir(parents=True)
        (legacy / 'code-review.json').write_text(json.dumps(other))
        (legacy / 'publication.json').write_text(json.dumps({'review': {'id': 1}, 'decisions': []}))
        self.assertEqual(rt.published_findings(self.repo, REPO, '1'), {'by_marker': {}, 'by_comment': {}, 'general': []})

    def test_general_findings_are_judged_until_answered_addressed(self):
        self.record('run-g', [finding('SR-G1'), finding('SR-G2'), finding('SR-G3')],
                    {'review': {'id': 9}, 'decisions': [{'id': f'SR-G{n}', 'outcome': 'GENERAL'} for n in (1, 2, 3)]})
        published = rt.published_findings(self.repo, REPO, '1')
        conversation = {'login': 'rev', 'threads': [],
                        'reviews': [{'id': 9, 'user': {'login': 'rev'}, 'html_url': 'https://review'}],
                        'issue_comments': [
                            {'user': {'login': 'rev'}, 'body': rt.followup_marker('fu-1', 'SR-G1', 'ADDRESSED')},
                            {'user': {'login': 'dev'}, 'body': rt.followup_marker('fu-1', 'SR-G2', 'ADDRESSED')},
                            {'user': {'login': 'rev'}, 'body': rt.followup_marker('fu-1', 'SR-G3', 'PARTIALLY_ADDRESSED')}]}
        selected = rt.select_threads(conversation, [], published)
        self.assertEqual([g['finding_id'] for g in selected['general']], ['SR-G2', 'SR-G3'])
        self.assertEqual(selected['skipped'], [{'finding_id': 'SR-G1', 'reason': 'ADDRESSED_EARLIER', 'run_id': 'fu-1'}])
        # The review is gone from GitHub (or someone else posted it): nothing to follow up.
        conversation['reviews'] = [{'id': 9, 'user': {'login': 'someone'}}]
        self.assertEqual(rt.select_threads(conversation, [], published)['general'], [])

    def test_truncated_threads_are_flagged(self):
        node = thread('T', [comment(1, 'rev', 'x')])
        node['comments']['totalCount'] = 150
        selected = rt.select_threads({'login': 'rev', 'threads': [node], 'reviews': [], 'issue_comments': []}, [],
                                     {'by_marker': {}, 'by_comment': {}, 'general': []})
        self.assertTrue(selected['threads'][0]['comments_truncated'])

    def test_fixture_needs_login_and_threads(self):
        path = self.repo / 'threads.json'
        path.write_text(json.dumps({'threads': []}))
        with self.assertRaises(WorkflowContractError):
            rt.load_fixture(path)
        path.write_text(json.dumps({'login': 'rev', 'threads': []}))
        self.assertEqual(rt.load_fixture(path), {'login': 'rev', 'threads': [], 'reviews': [], 'issue_comments': []})


import subprocess

FIXTURE = ROOT / 'agentic-sdlc-local-inputs' / 'source-followup' / 'create_fixture.py'
from sdlc_workflows import source_followup as fu
from sdlc_workflows.review_knowledge import load_review_knowledge


class FixtureItemsTests(unittest.TestCase):
    """build_items and the judge contract on the planted fixture (no agents)."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = Path(cls.temp.name)
        cls.inputs = json.loads(subprocess.run([sys.executable, str(FIXTURE), str(base / 'fixture')], check=True,
                                               capture_output=True, text=True).stdout)
        root = base / 'run'
        root.mkdir()
        cls.snapshot, conversation, remote = fu.pin_pull_request(cls.inputs, root)
        repo = Path(cls.inputs['repository_root'])
        selection = rt.select_threads(conversation, [], rt.published_findings(repo, cls.snapshot['repository'],
                                                                              cls.snapshot['pr_number']))
        cls.work = root / 'workspace'
        fu.export_tree(root / 'objects.git', cls.snapshot['head_sha'], cls.work / 'source' / 'head')
        fu.configure_workspace(cls.work)
        cls.objects = root / 'objects.git'
        cls.repo = repo
        cls.items, cls.changes, cls.gaps = fu.build_items(selection, cls.snapshot, cls.objects, remote,
                                                          cls.work, load_review_knowledge(repo))
        cls.c1 = selection['threads'][0]['commit']
        cls.by_key = {item['key']: item for item in cls.items}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def verdict(self, key, verdict, evidence=(), **extra):
        return {'item': key, 'verdict': verdict, 'reason': 'because', 'evidence': list(evidence),
                'reply': 'Reply text', 'rule_verdict': None, **extra}

    def check(self, *entries, keys=None):
        batch = [self.by_key[k] for k in (keys or [e['item'] for e in entries])]
        return fu.judge_contract({'verdicts': list(entries), 'coverage_gaps': []}, batch, self.changes, self.work)

    def test_items_locations_and_rule_staging(self):
        summary = {k: (i['kind'], i['location'], i.get('head_lines')) for k, i in self.by_key.items()}
        self.assertEqual(summary, {'T1': ('TOOL', 'CHANGED', None), 'T2': ('HAND_WRITTEN', 'CURRENT', [26, 28]),
                                   'T3': ('HAND_WRITTEN', 'CHANGED', None), 'T4': ('HAND_WRITTEN', 'CHANGED', None),
                                   'T5': ('HAND_WRITTEN', 'CURRENT', [5, 8]), 'G6': ('GENERAL', 'CHANGED', None)})
        self.assertEqual(self.by_key['T1']['rule'], {'id': 'NAMING-1', 'file': 'knowledge/rules/NAMING-1.md'})
        self.assertTrue((self.work / 'knowledge' / 'rules' / 'NAMING-1.md').is_file())
        self.assertTrue((self.work / self.by_key['T3']['changes_file']).is_file())
        self.assertIn('16>     return int(text)', self.by_key['T3']['commented_code'])
        self.assertEqual(self.gaps, [])

    def test_addressed_must_cite_changed_lines(self):
        # cart.py line 4 (TAX_RATE = 0.20) was added; line 7 (def checkout) is unchanged context.
        self.check(self.verdict('T2', 'ADDRESSED', [{'file': 'app/billing/cart.py', 'side': 'head', 'line_start': 4, 'line_end': 4}]))
        with self.assertRaisesRegex(WorkflowContractError, 'a reply alone never addresses'):
            self.check(self.verdict('T2', 'ADDRESSED', [{'file': 'app/billing/cart.py', 'side': 'head', 'line_start': 7, 'line_end': 7}]))
        with self.assertRaisesRegex(WorkflowContractError, 'a reply alone never addresses'):
            self.check(self.verdict('T3', 'PARTIALLY_ADDRESSED'))
        # A removed line counts on the before side.
        self.check(self.verdict('T2', 'ADDRESSED', [{'file': 'app/billing/cart.py', 'side': 'before', 'line_start': 4, 'line_end': 4}]))
        self.check(self.verdict('T3', 'NOT_ADDRESSED'))

    def test_evidence_must_exist_in_the_head(self):
        with self.assertRaisesRegex(WorkflowContractError, 'not in the current head'):
            self.check(self.verdict('T3', 'NOT_ADDRESSED', [{'file': 'app/billing/invoice.py', 'side': 'head', 'line_start': 90, 'line_end': 91}]))
        with self.assertRaises(WorkflowContractError):
            self.check(self.verdict('T3', 'NOT_ADDRESSED', [{'file': '../x.py', 'side': 'head', 'line_start': 1, 'line_end': 1}]))

    def test_rule_threads_need_a_rule_verdict(self):
        cited = [{'file': 'app/billing/invoice.py', 'side': 'head', 'line_start': 22, 'line_end': 23}]
        with self.assertRaisesRegex(WorkflowContractError, 'rule_verdict must be one of'):
            self.check(self.verdict('T1', 'ADDRESSED', cited))
        with self.assertRaisesRegex(WorkflowContractError, 'no longer violates NAMING-1'):
            self.check(self.verdict('T1', 'ADDRESSED', cited, rule_verdict='VIOLATED'))
        self.check(self.verdict('T1', 'ADDRESSED', cited, rule_verdict='NOT_VIOLATED'))
        with self.assertRaisesRegex(WorkflowContractError, 'only for project-rule threads'):
            self.check(self.verdict('T3', 'NOT_ADDRESSED', rule_verdict='VIOLATED'))

    def test_every_item_once_and_replies_per_verdict(self):
        with self.assertRaisesRegex(WorkflowContractError, 'missing'):
            self.check(self.verdict('T3', 'NOT_ADDRESSED'), keys=['T3', 'T5'])
        with self.assertRaisesRegex(WorkflowContractError, 'Unknown or repeated'):
            self.check(self.verdict('T3', 'NOT_ADDRESSED'), self.verdict('T3', 'NOT_ADDRESSED'), keys=['T3'])
        with self.assertRaisesRegex(WorkflowContractError, 'reply'):
            self.check({**self.verdict('T3', 'NOT_ADDRESSED'), 'reply': ''})
        # F10: the person writes the reply to a disputed or unclear thread.
        result = self.check(self.verdict('T5', 'DISPUTED'))
        self.assertEqual(result['verdicts'][0]['reply'], '')

    def test_unknown_commit_allows_no_change_claim(self):
        item = {**self.by_key['T3'], 'location': 'COMMIT_UNKNOWN', 'changes_file': None}
        with self.assertRaisesRegex(WorkflowContractError, 'use UNCLEAR'):
            fu.judge_contract({'verdicts': [self.verdict('T3', 'ADDRESSED', [{'file': 'app/billing/invoice.py', 'side': 'head',
                               'line_start': 12, 'line_end': 12}])]}, [item], self.changes, self.work)
        fu.judge_contract({'verdicts': [self.verdict('T3', 'UNCLEAR')]}, [item], self.changes, self.work)


class ChangesTests(FixtureItemsTests):
    """F9 start commit and the F7 check of the changes, on the same fixture."""

    def test_recorded_head_is_the_start(self):
        recorded = rt.recorded_heads(self.repo, 'fixture/followup', '1')
        self.assertEqual(recorded, [self.c1])
        start = fu.change_start(self.objects, None, self.snapshot['head_sha'], recorded, [])
        self.assertEqual(start, {'commit': self.c1, 'source': 'RECORDED_HEAD', 'commits_since': 1})

    def test_nearest_ancestor_wins_and_threads_are_the_fallback(self):
        base, head = self.snapshot['base_sha'], self.snapshot['head_sha']
        self.assertEqual(fu.change_start(self.objects, None, head, [base, self.c1], [])['commit'], self.c1)
        self.assertEqual(fu.change_start(self.objects, None, head, [], [base])['source'], 'THREAD_COMMIT')

    def test_dropped_commits_stop_the_run(self):
        with self.assertRaisesRegex(fu.BaseUnknown, 'BASE_UNKNOWN: none of the commits'):
            fu.change_start(self.objects, None, self.snapshot['head_sha'], ['e' * 40], [])
        with self.assertRaisesRegex(fu.BaseUnknown, 'nothing to compare'):
            fu.change_start(self.objects, None, self.snapshot['head_sha'], [], [])
        # A later commit is not an ancestor of an earlier head.
        with self.assertRaisesRegex(fu.BaseUnknown, 'BASE_UNKNOWN'):
            fu.change_start(self.objects, None, self.c1, [self.snapshot['head_sha']], [])

    def test_prepare_changes_stages_rules_and_lines(self):
        work = Path(self.temp.name) / 'changes-work'
        fu.export_tree(self.objects, self.snapshot['head_sha'], work / 'source' / 'head')
        changes = fu.prepare_changes(self.objects, self.snapshot, self.c1, work, load_review_knowledge(self.repo))
        self.assertEqual(changes['changed_files'], ['app/billing/cart.py', 'app/billing/invoice.py'])
        self.assertIn(21, changes['added']['app/billing/invoice.py'])  # the history comment
        self.assertIn(19, changes['removed']['app/billing/invoice.py'])  # def fmt(amt)
        self.assertEqual([r['id'] for r in changes['standards']['selection']['applicable']], ['COMMENTS-1', 'NAMING-1'])
        self.assertTrue((work / 'knowledge' / 'diff-b1.patch').is_file())
        self.assertTrue((work / 'source' / 'base' / 'app/billing/invoice.py').is_file())
        on = {'file': 'app/billing/invoice.py', 'side': 'head', 'line_start': 21, 'line_end': 21}
        self.assertTrue(fu.on_changed_lines(on, changes))
        self.assertFalse(fu.on_changed_lines({**on, 'line_start': 5, 'line_end': 8}, changes))
        self.assertTrue(fu.on_changed_lines({**on, 'side': 'base', 'line_start': 19, 'line_end': 19}, changes))


import importlib.util
from sdlc_workflows import followup_draft as fd

_spec = importlib.util.spec_from_file_location('publish_source_followup', ROOT / '.agentic-sdlc/scripts/publish_source_followup.py')
publisher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publisher)


def followup_report(run_id='fu-1', head=HEAD):
    new = {'stable_id': 'SR-new1', 'title': 'History comment', 'severity': 'LOW', 'category': 'MAINTAINABILITY',
           'confidence': 0.9, 'failure_scenario': 'COMMENTS-1: change-history comment', 'consequence': 'Stale comments',
           'remediation_direction': 'Remove it', 'source_evidence': 'line 21', 'verification_method': 'Read it',
           'standard_ref': 'COMMENTS-1', 'rule_level': 'MANDATORY', 'file': 'code.py', 'side': 'head',
           'line_start': 3, 'line_end': 3, 'placement': {'mode': 'INLINE', 'path': 'code.py', 'side': 'RIGHT',
                                                          'line_start': 3, 'line_end': 3}}
    item = lambda key, verdict, **extra: {'key': key, 'verdict': verdict, 'reason': 'r', 'reply': f'Reply {key}',
                                         'request': 'Please fix', 'url': 'u', 'path': 'code.py', 'line_start': 2,
                                         'line_end': 2, **extra}
    report = {'run_id': run_id, 'status': 'JUDGED',
              'snapshot': {'repository': REPO, 'pr_number': '1', 'pr_url': f'https://github.com/{REPO}/pull/1',
                           'base_sha': 'a' * 40, 'head_sha': head, 'merge_base_sha': 'a' * 40},
              'items': [item('T1', 'ADDRESSED', thread_id='TH1', finding_id=None),
                        item('T2', 'DISPUTED', thread_id='TH2', finding_id=None, reply=''),
                        item('T3', 'NOT_ADDRESSED', thread_id='TH3', finding_id=None),
                        item('G4', 'ADDRESSED', thread_id=None, finding_id='SR-g4')],
              'skipped': [], 'findings': [new], 'coverage_gaps': [],
              'changes': {'commit': 'c' * 40, 'source': 'RECORDED_HEAD', 'commits_since': 1, 'changed_files': ['code.py']}}
    report['counts'] = {v: sum(i['verdict'] == v for i in report['items']) for v in rt.VERDICTS}
    report['draft_sha256'] = 'x'
    return report


class DraftTests(unittest.TestCase):
    def test_round_trip_and_defaults(self):
        report = followup_report()
        parsed = fd.parse_draft(fd.render_draft(report), report)
        self.assertEqual({k: (r['verdict'], r['publish']) for k, r in parsed['replies'].items()},
                         {'T1': ('ADDRESSED', True), 'T2': ('DISPUTED', False), 'T3': ('NOT_ADDRESSED', True),
                          'G4': ('ADDRESSED', True)})
        self.assertEqual(parsed['replies']['T1']['body'], 'Reply T1')
        self.assertEqual(parsed['findings']['SR-new1']['anchor'], {'path': 'code.py', 'side': 'RIGHT', 'line_start': 3, 'line_end': 3})
        self.assertIn('Follow-up review of the changes', parsed['general'])

    def test_markers_in_quoted_comments_are_hidden(self):
        report = followup_report()
        report['items'][0]['request'] = 'Fix\n\n<!-- cao-source-finding:' + 'a' * 64 + ' -->'
        self.assertNotIn('cao-source-finding', fd.render_draft(report))

    def test_edits_and_errors(self):
        report = followup_report()
        text = fd.render_draft(report)
        edited = text.replace('<!-- reply item="T2" verdict="DISPUTED" publish="no" -->\n',
                              '<!-- reply item="T2" verdict="ADDRESSED" publish="yes" -->\nAgreed, fine as is.')
        self.assertEqual(fd.parse_draft(edited, report)['replies']['T2']['verdict'], 'ADDRESSED')
        cases = [text.replace('verdict="ADDRESSED" publish="yes" -->\nReply T1', 'verdict="FIXED" publish="yes" -->\nReply T1'),
                 text.replace('<!-- reply item="T3" verdict="NOT_ADDRESSED" publish="yes" -->\nReply T3\n<!-- end reply -->', ''),
                 text.replace('Reply T1', ''),
                 text.replace('item="T1"', 'item="T9"'),
                 text.replace('run="fu-1"', 'run="other"'),
                 text.replace('anchor="code.py:RIGHT:3-3"', 'anchor="code.py:RIGHT:5-3"'),
                 text.replace('<!-- end reply -->', '', 1)]
        for case in cases:
            with self.assertRaises(fd.FollowupDraftError):
                fd.parse_draft(case, report)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        repo = Path(self.temp.name)
        self.report = followup_report()
        self.results = repo / 'agentic-sdlc-records' / 'source-review' / 'repo-pr1' / 'followup-fu-1'
        self.results.mkdir(parents=True)
        (repo / publisher.FOLLOWUP_RUNTIME / 'fu-1').mkdir(parents=True)
        (self.results / 'follow-up.json').write_text(json.dumps(self.report))
        (self.results / 'follow-up-draft.md').write_text(fd.render_draft(self.report))
        self.head = HEAD
        self.posts = []
        self.issue_comments = []
        self.reviews = []
        self.threads = {tid: thread(tid, [comment(n, 'rev', 'Please fix')]) for n, tid in ((11, 'TH1'), (12, 'TH2'), (13, 'TH3'))}

    def github(self, call, repository='', number='', cursor=None):
        return {'pull': {'state': 'open', 'head': {'sha': self.head}},
                'user': {'login': 'rev'}, 'issue_comments': self.issue_comments, 'reviews': self.reviews,
                'files': [{'filename': 'code.py', 'patch': '@@ -1,2 +1,4 @@\n a\n+b\n+c\n d'}]}[call]

    def threads_reader(self, repository, number):
        return list(self.threads.values())

    def poster(self, kind, repository, number, payload, parent=None):
        self.posts.append((kind, parent, payload))
        new_id = 100 + len(self.posts)
        if kind == 'reply':
            node = self.threads[{11: 'TH1', 12: 'TH2', 13: 'TH3'}[parent]]
            node['comments']['nodes'].append(comment(new_id, 'rev', payload['body']))
        elif kind == 'conversation':
            self.issue_comments.append({'id': new_id, 'user': {'login': 'rev'}, 'body': payload['body']})
        else:
            self.reviews.append({'id': new_id, 'body': payload['body']})
            for c in payload['comments']:
                node = thread(f'NEW{new_id}', [comment(new_id + 50, 'rev', c['body'])], path=c['path'], line=c['line'])
                node['comments']['nodes'][0]['pullRequestReview'] = {'databaseId': new_id}
                self.threads[node['id']] = node
        return {'id': new_id, 'html_url': f'url{new_id}'}

    def publish(self):
        return publisher.publish(self.results, self.github, self.threads_reader, self.poster)

    def test_writes_are_pinned(self):
        self.assertEqual(publisher.ALLOWED_WRITES, ('reply', 'conversation', 'review'))
        with self.assertRaises(ValueError):
            publisher.post('resolve', REPO, '1', {'body': 'x'})
        with self.assertRaises(ValueError):
            publisher.post('review', REPO, '1', {'commit_id': HEAD, 'event': 'APPROVE', 'body': 'x', 'comments': []})
        with self.assertRaises(ValueError):
            publisher.post('reply', REPO, '1', {'body': 'x', 'event': 'APPROVE'}, 5)

    def test_publish_then_rerun_posts_nothing_new(self):
        receipt = self.publish()
        kinds = [(k, p) for k, p, _ in self.posts]
        self.assertEqual(kinds, [('reply', 11), ('reply', 13), ('conversation', None), ('review', None)])
        reply = self.posts[0][2]['body']
        self.assertTrue(reply.startswith('Reply T1') and reply.endswith(rt.followup_marker('fu-1', 'TH1', 'ADDRESSED')))
        self.assertIn(rt.followup_marker('fu-1', 'SR-g4', 'ADDRESSED'), self.posts[2][2]['body'])
        review = self.posts[3][2]
        self.assertEqual((review['event'], review['comments'][0]['line']), ('COMMENT', 3))
        self.assertEqual(receipt['status'], 'PUBLISHED')
        self.assertEqual(set(receipt['finding_comments']), {'SR-new1'})
        self.assertEqual([s['item'] for s in receipt['skipped']], ['T2'])
        self.posts.clear()
        again = self.publish()
        self.assertEqual(self.posts, [])
        self.assertTrue(all(r['reused'] for r in again['replies'].values()))
        self.assertTrue(again['conversation']['reused'] and again['review']['reused'])
        # The published threads are now ADDRESSED for later follow-ups (F4).
        selection = rt.select_threads({'login': 'rev', 'threads': list(self.threads.values()), 'reviews': [],
                                       'issue_comments': []}, [], {'by_marker': {}, 'by_comment': {}, 'general': []})
        self.assertEqual({s['thread_id'] for s in selection['skipped'] if s['reason'] == 'ADDRESSED_EARLIER'}, {'TH1'})

    def test_moved_pr_notes_replies_and_holds_new_findings(self):
        self.head = 'd' * 40
        receipt = self.publish()
        self.assertTrue(self.posts[0][2]['body'].startswith('_Judged at'))
        self.assertNotIn('review', [k for k, _, _ in self.posts])
        self.assertEqual(receipt['held'][0]['id'], 'SR-new1')

    def test_resolved_or_gone_threads_are_skipped(self):
        self.threads['TH1']['isResolved'] = True
        del self.threads['TH3']
        receipt = self.publish()
        self.assertEqual({s['item']: s['reason'] for s in receipt['skipped']},
                         {'T1': 'the thread was resolved after the run', 'T2': 'publish="no"',
                          'T3': 'the thread is gone from GitHub'})

    def test_anchor_outside_the_diff_goes_to_the_general_body(self):
        text = (self.results / 'follow-up-draft.md').read_text().replace('anchor="code.py:RIGHT:3-3"', 'anchor="code.py:RIGHT:9-9"')
        (self.results / 'follow-up-draft.md').write_text(text)
        self.publish()
        review = self.posts[-1][2]
        self.assertEqual(review['comments'], [])
        self.assertIn('not within one changed section', review['body'])

    def test_fixture_runs_cannot_be_published(self):
        self.report['snapshot']['pr_url'] = None
        (self.results / 'follow-up.json').write_text(json.dumps(self.report))
        with self.assertRaisesRegex(ValueError, 'local fixture'):
            self.publish()


class HelperTests(unittest.TestCase):
    def test_changed_lines_both_sides(self):
        diff = ('diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -2,3 +2,3 @@\n a\n-b\n+B\n c\n'
                'diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n')
        self.assertEqual(fu.changed_lines(diff), {'head': {'x.py': {3}}, 'before': {'x.py': {3}, 'gone.py': {1}}})

    def test_reviewer_logins(self):
        self.assertEqual(fu.reviewer_logins(' alice, bob-2 ,'), ['alice', 'bob-2'])
        self.assertEqual(fu.reviewer_logins(''), [])
        with self.assertRaises(WorkflowContractError):
            fu.reviewer_logins('alice;rm -rf')

    def test_input_defaults_match_constants(self):
        self.assertEqual(fu.INPUTS['threads_per_batch']['default'], fu.THREADS_PER_BATCH)
        self.assertEqual(fu.INPUTS['review_max_parallel']['default'], fu.MAX_PARALLEL_REVIEWS)

    def test_the_library_ships_the_followup_module(self):
        from build_library import build_library
        self.assertIn('source_followup.py', build_library()[1])


if __name__ == '__main__':
    unittest.main()
