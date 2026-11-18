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
        self.assertEqual(rt.ALLOWED_READS, ('user', 'pull', 'reviews', 'issue_comments', 'threads'))
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


if __name__ == '__main__':
    unittest.main()
