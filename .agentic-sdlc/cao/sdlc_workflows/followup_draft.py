"""The editable draft of a follow-up on review comments: rendered by the workflow, parsed by the publisher.

Only the marked blocks are meaningful. A reply block answers one thread (T keys) or one finding of a
review's general comment (G keys, posted together as one PR conversation comment). A new-finding block is a
comment beside the code in one COMMENT review, whose body is the general block. A person may edit any text,
set publish="no", change a reply's verdict, or move a new finding's anchor; adding blocks is not supported.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from .review_threads import VERDICTS

DRAFT_HEADER = re.compile(r'^<!-- cao-source-followup-draft run="([A-Za-z0-9][A-Za-z0-9_.-]{0,119})" head="([0-9a-f]{40})" -->$')
BLOCK = re.compile(r"^<!-- (reply|new-finding|general) ?(.*?) -->$")
ATTRIBUTE = re.compile(r'(\w+)="([^"]*)"')
ANCHOR = re.compile(r"^(.+):(RIGHT|LEFT):([1-9][0-9]*)-([1-9][0-9]*)$")
NEEDS_PERSON = ("DISPUTED", "UNCLEAR")


class FollowupDraftError(ValueError):
    pass


def _location(item: dict) -> str:
    if item.get("path") and item.get("line_start"):
        return f"{item['path']}:{item['line_start']}-{item['line_end']}"
    return item.get("path") or "whole PR"


def _quote(text: str, limit: int = 600) -> list[str]:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + " ..."
    return ["> " + line if line else ">" for line in text.splitlines()] or [">"]


def new_finding_comment(finding: dict) -> str:
    lines = [f"**{finding['title']}** ({finding['severity']})", "", finding["failure_scenario"], "",
             f"**Why it matters:** {finding['consequence']}", "",
             f"**Fix direction:** {finding['remediation_direction']}"]
    if finding.get("standard_ref"):
        lines += ["", f"Project rule: {finding['standard_ref']} ({finding.get('rule_level')})"]
    lines += ["", "<details><summary>Review details</summary>", "",
              f"- Finding: `{finding['stable_id']}` ({finding['category']}, confidence {finding['confidence']:.2f})",
              f"- Evidence: {finding['source_evidence']}",
              f"- Suggested verification: {finding['verification_method']}", "", "</details>"]
    return "\n".join(lines)


def anchor_text(placement: dict) -> str:
    if placement["mode"] == "GENERAL":
        return "general"
    return f"{placement['path']}:{placement['side']}:{placement['line_start']}-{placement['line_end']}"


def render_draft(report: dict) -> str:
    snapshot, changes = report["snapshot"], report["changes"]
    pr = (f"{snapshot['repository']}#{snapshot['pr_number']}" if snapshot.get("repository")
          else "local fixture")
    head = snapshot["head_sha"]
    counts = ", ".join(f"{verdict} {n}" for verdict, n in report["counts"].items() if n) or "none"
    person = [item for item in report["items"] if item["verdict"] in NEEDS_PERSON]
    lines = [f'<!-- cao-source-followup-draft run="{report["run_id"]}" head="{head}" -->',
             f"# Follow-up draft: {pr} at `{head[:12]}`", "",
             "Edit the text inside the marked blocks; text outside them is ignored. Per block you may set",
             '`publish="no"`, change a reply\'s `verdict` (the reply records it, and a thread answered ADDRESSED is',
             "skipped by later follow-ups), or move a new finding's `anchor` (`path:RIGHT|LEFT:start-end` inside",
             'the diff, or "general"). DISPUTED and UNCLEAR replies are empty: write one and set `publish="yes"` to',
             "answer. Check with `python3 .agentic-sdlc/scripts/publish_source_followup.py <this folder>` before",
             "`--publish`. Nothing is resolved, approved or merged.", "",
             "## Summary", "",
             f"- Comments judged: {len(report['items'])} ({counts}); skipped: {len(report['skipped'])}.",
             f"- Changes since `{changes['commit'][:12]}` ({changes['source'].replace('_', ' ').lower()}, "
             f"{changes['commits_since']} commit(s), {len(changes['changed_files'])} file(s)) checked: "
             f"{len(report['findings'])} new finding(s).",
             f"- Coverage gaps: {len(report['coverage_gaps'])}."]
    waiting = ", ".join(f"{item['key']} ({item['verdict']})" for item in person)
    lines += [f"- Needs your reply: {waiting}."] if person else []
    for gap in report["coverage_gaps"]:
        lines.append(f"  - Gap: {gap}")
    lines += ["", "## Replies"]
    for item in report["items"]:
        where = "the PR conversation (general comment)" if item["key"].startswith("G") else "the thread"
        lines += ["", f"### {item['key']}: {item['verdict']}: {_location(item)}", "",
                  f"Comment ({item.get('url') or 'no link'}):", *_quote(item["request"]), "",
                  f"Judge's reason: {item['reason']}", f"Posted in {where}.", "",
                  f'<!-- reply item="{item["key"]}" verdict="{item["verdict"]}" '
                  f'publish="{"no" if item["verdict"] in NEEDS_PERSON else "yes"}" -->',
                  item["reply"], "<!-- end reply -->"]
    if report["findings"]:
        lines += ["", "## New findings in the changes", "",
                  "Posted as one review with event COMMENT: this general text, plus one comment per finding.", "",
                  "<!-- general -->",
                  f"Follow-up review of the changes since `{changes['commit'][:12]}`: "
                  f"{len(report['findings'])} new finding(s) in lines those changes add or remove.",
                  "<!-- end general -->"]
        for finding in report["findings"]:
            lines += ["", f"### {finding['stable_id']}: {finding['severity']}: {finding['file']}:{finding['line_start']}", "",
                      f'<!-- new-finding id="{finding["stable_id"]}" publish="yes" anchor="{anchor_text(finding["placement"])}" -->',
                      new_finding_comment(finding), "<!-- end new-finding -->"]
    return "\n".join(lines) + "\n"


def parse_draft(text: str, report: dict) -> dict:
    """Strictly parse follow-up-draft.md against its report; returns {"replies", "general", "findings"}."""
    lines = text.splitlines()
    header = DRAFT_HEADER.match(lines[0]) if lines else None
    if not header or (header[1], header[2]) != (report["run_id"], report["snapshot"]["head_sha"]):
        raise FollowupDraftError("line 1: draft header is missing or belongs to another follow-up")
    keys = {item["key"] for item in report["items"]}
    ids = {finding["stable_id"] for finding in report["findings"]}
    replies, findings, general, block, body = {}, {}, None, None, []
    for number, line in enumerate(lines[1:], start=2):
        stripped = line.strip()
        match = BLOCK.match(stripped)
        if block is None:
            if match:
                kind, attributes = match[1], dict(ATTRIBUTE.findall(match[2]))
                if ATTRIBUTE.sub("", match[2]).strip():
                    raise FollowupDraftError(f'line {number}: attributes must be key="value"')
                if kind == "reply":
                    if set(attributes) != {"item", "verdict", "publish"}:
                        raise FollowupDraftError(f"line {number}: a reply needs exactly item, verdict and publish")
                    if attributes["item"] not in keys or attributes["item"] in replies:
                        raise FollowupDraftError(f"line {number}: unknown or repeated item {attributes['item']}")
                    if attributes["verdict"] not in VERDICTS:
                        raise FollowupDraftError(f"line {number}: verdict must be one of {', '.join(VERDICTS)}")
                elif kind == "new-finding":
                    if set(attributes) != {"id", "publish", "anchor"}:
                        raise FollowupDraftError(f"line {number}: a new finding needs exactly id, publish and anchor")
                    if attributes["id"] not in ids or attributes["id"] in findings:
                        raise FollowupDraftError(f"line {number}: unknown or repeated finding {attributes['id']}; "
                                                 "adding findings is not supported")
                    anchor = attributes["anchor"]
                    if anchor != "general":
                        parsed = ANCHOR.match(anchor)
                        if not parsed or int(parsed[3]) > int(parsed[4]):
                            raise FollowupDraftError(f'line {number}: anchor must be "general" or path:RIGHT|LEFT:start-end')
                        attributes["anchor"] = {"path": parsed[1], "side": parsed[2], "line_start": int(parsed[3]),
                                                "line_end": int(parsed[4])}
                    else:
                        attributes["anchor"] = None
                elif attributes or general is not None:
                    raise FollowupDraftError(f"line {number}: second or malformed general block")
                if kind != "general" and attributes["publish"] not in {"yes", "no"}:
                    raise FollowupDraftError(f'line {number}: publish must be "yes" or "no"')
                block, body = {"kind": kind, "line": number, **attributes}, []
            elif stripped.startswith("<!-- end") or stripped.startswith("<!-- reply") or stripped.startswith("<!-- new-finding"):
                raise FollowupDraftError(f"line {number}: marker outside a block or malformed")
        elif stripped.startswith("<!-- end"):
            if stripped != f"<!-- end {block['kind']} -->":
                raise FollowupDraftError(f"line {number}: {stripped} closes a {block['kind']} block")
            content = "\n".join(body).strip()
            if block["kind"] == "general":
                general = content
            else:
                publish = block["publish"] == "yes"
                if publish and not content:
                    raise FollowupDraftError(f"line {block['line']}: a published block is empty")
                entry = {**block, "publish": publish, "body": content}
                if block["kind"] == "reply":
                    replies[block["item"]] = entry
                else:
                    findings[block["id"]] = entry
            block = None
        elif match:
            raise FollowupDraftError(f"line {number}: marker inside an open {block['kind']} block")
        else:
            body.append(line)
    if block is not None:
        raise FollowupDraftError(f"line {block['line']}: {block['kind']} block is not closed")
    if set(replies) != keys:
        raise FollowupDraftError(f"reply blocks removed: {sorted(keys - set(replies))}; set publish=\"no\" instead")
    if set(findings) != ids:
        raise FollowupDraftError(f"new-finding blocks removed: {sorted(ids - set(findings))}; set publish=\"no\" instead")
    if ids and general is None:
        raise FollowupDraftError("the draft has new findings but no general block")
    return {"replies": replies, "findings": findings, "general": general or ""}


def blob_link(repository: str, commit: str, path: str, start: int, end: int) -> str:
    return (f"[{path}:{start}-{end}](https://github.com/{repository}/blob/{commit}/"
            f"{quote(path, safe='/')}#L{start}-L{end})")
