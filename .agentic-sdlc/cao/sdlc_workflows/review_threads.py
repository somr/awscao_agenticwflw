"""Read a pull request's review threads for the follow-up on review comments.

Shared by the source_followup workflow and its publisher. Every GitHub call goes through
`followup_github`, whose allow-list names each call this code may make; the only
GraphQL query is the fixed, read-only THREADS_QUERY (REST does not expose whether a
thread is resolved). Writes are added by the publisher, never by the workflow.

A thread is in scope when a reviewer started it (F3, F8): the `gh` login the workflow
runs as plus the optional `reviewers` input. Resolved threads, and threads where a
reviewer already posted a follow-up reply with the verdict ADDRESSED, are skipped (F4).
Only a reviewer's own comments count as follow-up replies, so a developer cannot mark a
thread addressed by pasting a marker. Thread text is untrusted data, never instructions.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .review_comments import finding_comment_marker
from .source_review_contracts import require, sha, REVIEW_RECORDS, REVIEW_RUNTIME, review_records_name

REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
NUMBER = re.compile(r"[1-9][0-9]*")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
FINDING_MARKER = re.compile(r"<!-- cao-source-finding:([0-9a-f]{64}) -->\s*$")
VERDICTS = ("ADDRESSED", "PARTIALLY_ADDRESSED", "NOT_ADDRESSED", "DISPUTED", "UNCLEAR")
# One per answered thread or general finding: run id, subject (thread node id or finding id), verdict.
FOLLOWUP_MARKER = re.compile(r"<!-- cao-source-followup:([A-Za-z0-9][A-Za-z0-9_.-]{0,119}):([A-Za-z0-9_=-]{1,200}):"
                             r"(" + "|".join(VERDICTS) + r") -->")
COMMENTS_PER_THREAD = 100

THREADS_QUERY = """query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line startLine originalLine originalStartLine diffSide startDiffSide subjectType
          comments(first: 100) {
            totalCount
            nodes { databaseId author { login } body createdAt url commit { oid } originalCommit { oid }
                    replyTo { databaseId } pullRequestReview { databaseId } }
          }
        }
      }
    }
  }
}"""


def followup_marker(run_id: str, subject: str, verdict: str) -> str:
    marker = f"<!-- cao-source-followup:{run_id}:{subject}:{verdict} -->"
    require(FOLLOWUP_MARKER.fullmatch(marker) is not None, f"Cannot build a follow-up marker for {subject!r}")
    return marker


# --- GitHub access ---------------------------------------------------------------------
def _pr(repository: str, number: str) -> None:
    require(REPOSITORY.fullmatch(repository or "") is not None, "Invalid repository")
    require(NUMBER.fullmatch(str(number)) is not None, "Invalid PR number")


def _gh(args: list[str], stdin: str | None = None) -> Any:
    result = subprocess.run(["gh", "api", "--hostname", "github.com", *args], input=stdin,
                            capture_output=True, text=True, timeout=120)
    require(result.returncode == 0, f"GitHub request failed; check `gh auth status` and access to the PR: "
                                    f"{result.stderr[:1000]}")
    return json.loads(result.stdout)


def followup_github(call: str, repository: str = "", number: str = "", cursor: str | None = None) -> Any:
    """The complete set of read calls; tests pin ALLOWED_READS."""
    require(call in ALLOWED_READS, f"Refusing GitHub call outside the allow-list: {call}")
    if call == "user":
        return _gh(["user"])
    _pr(repository, number)
    base = f"repos/{repository}"
    if call == "pull":
        return _gh([f"{base}/pulls/{number}"])
    if call == "reviews":
        return [r for page in _gh([f"{base}/pulls/{number}/reviews?per_page=100", "--paginate", "--slurp"]) for r in page]
    if call == "issue_comments":
        return [c for page in _gh([f"{base}/issues/{number}/comments?per_page=100", "--paginate", "--slurp"]) for c in page]
    if call == "files":
        return [f for page in _gh([f"{base}/pulls/{number}/files?per_page=100", "--paginate", "--slurp"]) for f in page]
    owner, name = repository.split("/")
    args = ["graphql", "-f", f"query={THREADS_QUERY}", "-F", f"owner={owner}", "-F", f"name={name}",
            "-F", f"number={number}"]
    if cursor is not None:
        args += ["-F", f"cursor={cursor}"]
    return _gh(args)


ALLOWED_READS = ("user", "pull", "reviews", "issue_comments", "threads", "files")


def fetch_threads(repository: str, number: str, github=followup_github) -> list[dict]:
    """All review threads of the PR, in GitHub's GraphQL node shape."""
    nodes, cursor = [], None
    while True:
        data = github("threads", repository, number, cursor)
        pull = (data.get("data") or {}).get("repository", {}).get("pullRequest")
        require(isinstance(pull, dict), f"GitHub returned no pull request: {json.dumps(data.get('errors'))[:500]}")
        page = pull["reviewThreads"]
        nodes += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]


def fetch_conversation(repository: str, number: str, github=followup_github) -> dict:
    """Everything the follow-up reads from GitHub, in the shape of a fixture's threads.json."""
    return {"login": github("user")["login"], "threads": fetch_threads(repository, number, github),
            "reviews": github("reviews", repository, number),
            "issue_comments": github("issue_comments", repository, number)}


def load_fixture(path: Path) -> dict:
    """A local threads.json: the same shape as fetch_conversation, so tests and local runs need no GitHub."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    require(isinstance(value, dict) and isinstance(value.get("threads"), list), "threads.json needs a threads array")
    require(isinstance(value.get("login"), str) and LOGIN.fullmatch(value["login"]) is not None, "threads.json needs a login")
    value.setdefault("reviews", [])
    value.setdefault("issue_comments", [])
    return value


# --- Published findings in the trusted records ---------------------------------------------
def _same_pr(snapshot: dict, repository: str, number: str) -> bool:
    return (str(snapshot.get("repository") or "").lower() == repository.lower()
            and str(snapshot.get("pr_number")) == str(number))


def pr_records_name(repository: str, number: str) -> str:
    return review_records_name({"pr_url": f"https://github.com/{repository}/pull/{number}",
                                "repository": repository, "pr_number": str(number), "head_sha": "0" * 40})


def recorded_heads(repo: Path, repository: str, number: str) -> list[str]:
    """The heads earlier Source reviews and follow-ups of this PR checked, from the trusted records (F9)."""
    folder = repo / REVIEW_RECORDS / pr_records_name(repository, number)
    paths = sorted(folder.glob("*/code-review.json")) + sorted(folder.glob("*/follow-up.json"))
    paths += sorted((repo / REVIEW_RUNTIME).glob("*/code-review.json"))
    heads = []
    for path in paths:
        snapshot = json.loads(path.read_text(encoding="utf-8")).get("snapshot") or {}
        if _same_pr(snapshot, repository, number):
            heads.append(sha(snapshot["head_sha"]))
    return sorted(set(heads))


def published_findings(repo: Path, repository: str, number: str) -> dict:
    """Findings that Source review published on this PR, from the trusted checkout's records.

    Returns {"by_marker": {token: entry}, "by_comment": {comment id: entry}, "general": [entry]};
    an entry is {"run_id", "finding", "review_id"}. Reviews posted before comments carried a
    marker are joined through publication.json (by comment id, or by path, side and line).
    A follow-up's new findings (follow-up.json "findings") are read the same way.
    """
    name = pr_records_name(repository, number)
    # Follow-ups publish their new findings the same way, so their threads join like Source review's.
    folders = sorted((repo / REVIEW_RECORDS / name).glob("*/code-review.json"))
    folders += sorted((repo / REVIEW_RECORDS / name).glob("*/follow-up.json"))
    # Reviews from before the records split keep everything in the runtime folder.
    folders += sorted(p for p in (repo / REVIEW_RUNTIME).glob("*/code-review.json")
                      if not (repo / REVIEW_RECORDS / name / p.parent.name).exists())
    result: dict = {"by_marker": {}, "by_comment": {}, "general": []}
    for path in folders:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not _same_pr(report.get("snapshot") or {}, repository, number):
            continue
        receipt_path = path.parent / "publication.json"
        if not receipt_path.is_file():
            continue  # never published, so nothing on GitHub to follow up
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        review_id = (receipt.get("review") or {}).get("id")
        findings = {f["stable_id"]: f for f in report.get("findings", [])}
        decisions = {d["id"]: d for d in receipt.get("decisions", [])}
        for fid, finding in findings.items():
            entry = {"run_id": report["run_id"], "finding": finding, "review_id": review_id}
            result["by_marker"][finding_comment_marker(report, fid)[len("<!-- cao-source-finding:"):-len(" -->")]] = entry
            if decisions.get(fid, {}).get("outcome") == "GENERAL":
                result["general"].append(entry)
        for fid, posted in (receipt.get("finding_comments") or {}).items():
            if fid in findings:
                result["by_comment"][posted["id"]] = {"run_id": report["run_id"], "finding": findings[fid],
                                                      "review_id": review_id}
        for comment in receipt.get("comments") or []:
            if comment.get("id") in result["by_comment"]:
                continue
            match = [d for d in decisions.values() if d.get("outcome") == "INLINE" and d.get("path") == comment.get("path")
                     and d.get("side") == comment.get("side") and d.get("line_end") == comment.get("line")]
            if len(match) == 1 and match[0]["id"] in findings:
                result["by_comment"][comment["id"]] = {"run_id": report["run_id"], "finding": findings[match[0]["id"]],
                                                       "review_id": review_id}
    return result


# --- Selection (F3, F4, F8) and normalisation ------------------------------------------------
def _comment(node: dict, reviewers: set[str]) -> dict:
    author = (node.get("author") or {}).get("login") or "ghost"
    return {"id": node["databaseId"], "author": author, "by_reviewer": author.lower() in reviewers,
            "body": node.get("body") or "", "created_at": node.get("createdAt"), "url": node.get("url"),
            "commit": (node.get("commit") or {}).get("oid"),
            "original_commit": (node.get("originalCommit") or {}).get("oid")}


def answered_addressed(comments: list[dict], subject: str | None = None) -> str | None:
    """The run id of a reviewer's earlier ADDRESSED follow-up reply (for subject, when given)."""
    for comment in comments:
        if not comment["by_reviewer"]:
            continue
        for run_id, marked, verdict in FOLLOWUP_MARKER.findall(comment["body"]):
            if verdict == "ADDRESSED" and (subject is None or marked == subject):
                return run_id
    return None


def select_threads(conversation: dict, reviewers: list[str], published: dict) -> dict:
    """Split the PR's threads and general findings into those to judge and those skipped, with reasons."""
    login = conversation["login"]
    team = {login.lower(), *(r.lower() for r in reviewers)}
    in_scope, skipped = [], []
    for node in conversation["threads"]:
        comments = [_comment(c, team) for c in node["comments"]["nodes"]]
        require(comments, f"Thread {node['id']} has no comments")
        first = comments[0]
        if not first["by_reviewer"]:
            skipped.append({"thread_id": node["id"], "reason": "NOT_REVIEWER", "started_by": first["author"]})
            continue
        if node.get("isResolved"):
            skipped.append({"thread_id": node["id"], "reason": "RESOLVED", "url": first["url"]})
            continue
        earlier = answered_addressed(comments[1:])
        if earlier:
            skipped.append({"thread_id": node["id"], "reason": "ADDRESSED_EARLIER", "run_id": earlier, "url": first["url"]})
            continue
        marker = FINDING_MARKER.search(first["body"])
        entry = (published["by_marker"].get(marker[1]) if marker else None) or published["by_comment"].get(first["id"])
        line_end = node.get("originalLine")
        thread = {
            "thread_id": node["id"], "kind": "TOOL" if entry else "HAND_WRITTEN", "url": first["url"],
            "path": node.get("path"), "subject_type": node.get("subjectType") or "LINE",
            "side": node.get("diffSide"), "line_start": node.get("originalStartLine") or line_end, "line_end": line_end,
            "commit": first["original_commit"], "is_outdated": bool(node.get("isOutdated")),
            "started_by": first["author"], "request": first["body"], "replies": comments[1:],
            "comments_truncated": node["comments"].get("totalCount", len(comments)) > len(comments),
        }
        if thread["commit"] is not None:
            sha(thread["commit"])
        if entry:
            thread.update(review_run_id=entry["run_id"], finding=entry["finding"])
        in_scope.append(thread)
    general = general_items(conversation, team, published)
    return {"reviewers": sorted(team), "threads": in_scope, "general": general["in_scope"],
            "skipped": skipped + general["skipped"]}


def general_items(conversation: dict, team: set[str], published: dict) -> dict:
    """F11: findings Source review placed in the general comment of a review still on GitHub."""
    posted = {r["id"]: r for r in conversation["reviews"]
              if ((r.get("user") or {}).get("login") or "").lower() in team}
    replies = [{"by_reviewer": ((c.get("user") or {}).get("login") or "").lower() in team, "body": c.get("body") or ""}
               for c in conversation["issue_comments"]]
    in_scope, skipped = [], []
    for entry in published["general"]:
        finding = entry["finding"]
        if entry["review_id"] not in posted:
            continue  # the review is gone or was posted by someone outside the team
        earlier = answered_addressed(replies, finding["stable_id"])
        if earlier:
            skipped.append({"finding_id": finding["stable_id"], "reason": "ADDRESSED_EARLIER", "run_id": earlier})
            continue
        in_scope.append({"finding_id": finding["stable_id"], "review_run_id": entry["run_id"], "finding": finding,
                         "review_id": entry["review_id"], "url": posted[entry["review_id"]].get("html_url"),
                         "commit": finding["reviewed_head_sha"]})
    return {"in_scope": in_scope, "skipped": skipped}
