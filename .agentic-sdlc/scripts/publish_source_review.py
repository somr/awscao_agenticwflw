#!/usr/bin/env python3
"""Publish a reviewed, human-edited source-review draft as one COMMENT review.

Without --publish nothing is written to GitHub: the draft is parsed, every comment
location is checked against the pull request's current diff, and the exact request
is printed. With --publish, one review is created atomically with event COMMENT:
the general comment plus one comment beside the code per placed finding. The tool
never approves, requests changes, dismisses, edits, deletes or resolves anything.

If the PR moved since the review, each finding is checked against the new head in
the run's private object store: only findings whose commented lines are unchanged
are placed beside the code; the rest are listed in the general comment. This
proves the code is textually unchanged, not that the finding is still relevant.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

MAX_BODY = 65536
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DRAFT_HEADER = re.compile(r'^<!-- cao-source-review-draft base="([0-9a-f]{40})" head="([0-9a-f]{40})" -->$')
FINDING_MARKER = re.compile(r"^<!-- finding (.*) -->$")
ATTRIBUTE = re.compile(r'(\w+)="([^"]*)"')
ANCHOR = re.compile(r"^(.+):(RIGHT|LEFT):([1-9][0-9]*)-([1-9][0-9]*)$")
# The complete set of GitHub calls this tool may make; tests pin it.
ALLOWED_CALLS = {("GET", "pulls"), ("GET", "files"), ("GET", "reviews"), ("GET", "comments"), ("POST", "reviews")}


class DraftError(ValueError):
    pass


def gh(endpoint: str, *, payload: dict | None = None, paginate: bool = False):
    method = "POST" if payload is not None else "GET"
    kind = endpoint.rsplit("/", 1)[-1].split("?")[0]
    kind = "pulls" if kind.isdigit() else kind
    if (method, kind) not in ALLOWED_CALLS:
        raise ValueError(f"Refusing GitHub call outside the allow-list: {method} {endpoint}")
    if payload is not None and payload.get("event") != "COMMENT":
        raise ValueError("Only COMMENT reviews may be created")
    args = ["gh", "api", "--hostname", "github.com", endpoint]
    if paginate:
        args += ["--paginate", "--slurp"]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(f"GitHub request failed: {result.stderr[:1000]}")
    return json.loads(result.stdout)


def git(objects: Path, *args: str) -> str:
    # Never run repository hooks, filters or external diff drivers.
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                             "-C", str(objects), *args], capture_output=True, timeout=600)
    if result.returncode:
        raise ValueError(f"git {' '.join(args[:2])} failed: {result.stderr.decode(errors='replace')[:500]}")
    return result.stdout.decode("utf-8", errors="replace")


def hunk_ranges(patch: str) -> dict[str, list[tuple[int, int]]]:
    """Keep in sync with sdlc_workflows.source_review.hunk_ranges (parity-tested)."""
    ranges: dict[str, list[tuple[int, int]]] = {"LEFT": [], "RIGHT": []}
    for line in patch.splitlines():
        match = HUNK_HEADER.match(line)
        if match:
            for side, start, count in (("LEFT", match[1], match[2]), ("RIGHT", match[3], match[4])):
                size = 1 if count is None else int(count)
                if size:
                    ranges[side].append((int(start), int(start) + size - 1))
    return ranges


# --- Draft ---------------------------------------------------------------------
def parse_draft(text: str, report: dict) -> dict:
    """Strictly parse review-draft.md; only marked blocks are meaningful."""
    lines = text.splitlines()
    snapshot = report["snapshot"]
    header = DRAFT_HEADER.match(lines[0]) if lines else None
    if not header or (header[1], header[2]) != (snapshot["base_sha"], snapshot["head_sha"]):
        raise DraftError("line 1: draft header is missing or belongs to another review")
    known = {f["stable_id"] for f in report["findings"]}
    general, findings, block, body = None, [], None, []
    for number, line in enumerate(lines[1:], start=2):
        stripped = line.strip()
        if block is None:
            if stripped == "<!-- general -->":
                if general is not None:
                    raise DraftError(f"line {number}: second general block")
                block, body = {"kind": "general", "line": number}, []
            elif (match := FINDING_MARKER.match(stripped)):
                attributes = dict(ATTRIBUTE.findall(match[1]))
                if ATTRIBUTE.sub("", match[1]).strip() or set(attributes) != {"id", "publish", "anchor", "reason"}:
                    raise DraftError(f'line {number}: finding marker needs exactly id, publish, anchor and reason as key="value"')
                if attributes["id"] not in known:
                    raise DraftError(f"line {number}: unknown finding {attributes['id']}; adding findings is not supported")
                if any(f["id"] == attributes["id"] for f in findings):
                    raise DraftError(f"line {number}: finding {attributes['id']} appears twice")
                if attributes["publish"] not in {"yes", "no"}:
                    raise DraftError(f'line {number}: publish must be "yes" or "no"')
                anchor = None
                if attributes["anchor"] != "general":
                    parsed = ANCHOR.match(attributes["anchor"])
                    if not parsed or int(parsed[3]) > int(parsed[4]):
                        raise DraftError(f'line {number}: anchor must be "general" or path:RIGHT|LEFT:start-end')
                    anchor = {"path": parsed[1], "side": parsed[2], "line_start": int(parsed[3]),
                              "line_end": int(parsed[4])}
                block, body = {"kind": "finding", "line": number, "id": attributes["id"],
                               "publish": attributes["publish"] == "yes", "anchor": anchor,
                               "reason": attributes["reason"]}, []
            elif stripped.startswith("<!-- end") or stripped.startswith("<!-- finding"):
                raise DraftError(f"line {number}: marker outside a block or malformed")
        elif stripped in {"<!-- end general -->", "<!-- end finding -->"}:
            if stripped != f"<!-- end {block['kind']} -->":
                raise DraftError(f"line {number}: {stripped} closes a {block['kind']} block")
            content = "\n".join(body).strip()
            if block["kind"] == "general":
                general = content
            else:
                if block["publish"] and not content:
                    raise DraftError(f"line {block['line']}: finding {block['id']} is published but empty")
                findings.append({**block, "body": content})
            block = None
        elif stripped.startswith("<!-- general") or FINDING_MARKER.match(stripped) or stripped.startswith("<!-- end"):
            raise DraftError(f"line {number}: marker inside an open {block['kind']} block")
        else:
            body.append(line)
    if block is not None:
        raise DraftError(f"line {block['line']}: {block['kind']} block is not closed")
    if general is None:
        raise DraftError("the draft has no general block")
    missing = known - {f["id"] for f in findings}
    if missing:
        raise DraftError(f"finding blocks removed: {sorted(missing)}; set publish=\"no\" instead")
    return {"general": general, "findings": findings}


# --- Currency of findings after the PR moved -------------------------------------
def map_range(objects: Path, old: str, new: str, path: str, start: int, end: int) -> tuple[str, tuple[int, int] | None]:
    """Map an unchanged line range from commit old to commit new, or report why not."""
    if old == new:
        return "CURRENT", (start, end)
    try:
        git(objects, "cat-file", "-e", f"{new}:{path}")
    except ValueError:
        return "GONE", None
    diff = git(objects, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "-U0", old, new, "--", path)
    offset = 0
    for line in diff.splitlines():
        match = HUNK_HEADER.match(line)
        if not match:
            continue
        a, b = int(match[1]), 1 if match[2] is None else int(match[2])
        c, d = int(match[3]), 1 if match[4] is None else int(match[4])
        if b == 0:  # pure insertion after old line a
            if a < start:
                offset += d
            elif a < end:
                return "CHANGED", None
        elif a + b - 1 < start:
            offset += d - b
        elif a <= end:
            return "CHANGED", None
    mapped = (start + offset, end + offset)
    old_lines = git(objects, "show", f"{old}:{path}").splitlines()[start - 1:end]
    new_lines = git(objects, "show", f"{new}:{path}").splitlines()[mapped[0] - 1:mapped[1]]
    return ("CURRENT", mapped) if old_lines == new_lines else ("CHANGED", None)


def fetch_current(root: Path, snapshot: dict, head: str, base: str) -> str:
    """Fetch the PR's current tips into the run's own object store; return the merge base."""
    objects = root / "objects.git"
    remote = f"https://github.com/{snapshot['repository']}.git"
    git(objects, "fetch", "--no-tags", remote, base)
    git(objects, "fetch", "--no-tags", remote, f"refs/pull/{snapshot['pr_number']}/head")
    if git(objects, "rev-parse", "FETCH_HEAD").strip() != head:
        raise ValueError("PR head moved during retrieval; try again")
    return git(objects, "merge-base", base, head).strip()


# --- Plan the review ---------------------------------------------------------------
def load(root: Path) -> tuple[dict, str]:
    report = json.loads((root / "code-review.json").read_text())
    if report.get("status") != "REVIEWED":
        raise ValueError("Only REVIEWED artifacts may be published; rerun stale/failed reviews")
    snapshot = report["snapshot"]
    repository, number = snapshot["repository"], snapshot["pr_number"]
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Publication requires a GitHub PR artifact")
    if not isinstance(number, str) or not re.fullmatch(r"[1-9][0-9]*", number):
        raise ValueError("Invalid PR number")
    for key in ("base_sha", "head_sha", "merge_base_sha"):
        if not re.fullmatch(r"[0-9a-f]{40}", snapshot[key]):
            raise ValueError(f"Invalid {key}")
    comments = (root / "comments.md").read_text()
    if hashlib.sha256(comments.encode()).hexdigest() != report["comments_sha256"]:
        raise ValueError("comments.md differs from the reviewed artifact")
    draft_path = root / "review-draft.md"
    if not draft_path.is_file() or "draft_sha256" not in report:
        raise ValueError("No review-draft.md: rerun the review with the current workflow")
    return report, draft_path.read_text()


def marker(snapshot: dict) -> str:
    return f"<!-- cao-source-review:{snapshot['base_sha']}:{snapshot['head_sha']} -->"


def link(repository: str, commit: str, path: str, start: int, end: int) -> str:
    from urllib.parse import quote
    return (f"[{path}:{start}-{end}](https://github.com/{repository}/blob/{commit}/"
            f"{quote(path, safe='/')}#L{start}-L{end})")


def plan_review(root: Path, report: dict, draft_text: str, metadata: dict, files: list[dict],
                *, include_context_changed: bool = False) -> dict:
    """Decide, per finding, where it goes; build the single COMMENT review request."""
    snapshot = report["snapshot"]
    draft = parse_draft(draft_text, report)
    if metadata["state"] != "open":
        raise ValueError("PR is closed; nothing can be published")
    head, base = metadata["head"]["sha"], metadata["base"]["sha"]
    moved = (head, base) != (snapshot["head_sha"], snapshot["base_sha"])
    merge_base = fetch_current(root, snapshot, head, base) if moved else snapshot["merge_base_sha"]
    patches = {f["filename"]: f for f in files}
    mapping_file = root / "workspace" / "mapping.json"
    related = {item["file"]: item.get("related_files", [])
               for item in json.loads(mapping_file.read_text())["files"]} if mapping_file.is_file() else {}
    by_id = {f["stable_id"]: f for f in report["findings"]}
    comments, general_items, held, omitted, decisions = [], [], [], [], []
    for item in draft["findings"]:
        finding = by_id[item["id"]]
        if not item["publish"]:
            omitted.append({"id": item["id"], "reason": item["reason"] or "not given"})
            decisions.append({"id": item["id"], "outcome": "OMITTED", "reason": item["reason"]})
            continue
        anchor = item["anchor"]
        if anchor is None:
            general_items.append((item, finding, "Placed in the general comment by the reviewer"))
            decisions.append({"id": item["id"], "outcome": "GENERAL", "reason": "reviewer choice"})
            continue
        note = ""
        if moved:
            old_commit, new_commit = ((snapshot["head_sha"], head) if anchor["side"] == "RIGHT"
                                      else (snapshot["merge_base_sha"], merge_base))
            status, mapped = map_range(root / "objects.git", old_commit, new_commit, anchor["path"],
                                       anchor["line_start"], anchor["line_end"])
            if status == "CURRENT" and related.get(finding["file"]):
                changed = git(root / "objects.git", "diff", "--name-only", "--no-renames",
                              snapshot["head_sha"], head, "--", *related[finding["file"]]).split()
                if changed and not include_context_changed:
                    status = "CONTEXT_CHANGED"
            if status != "CURRENT":
                held.append({"id": item["id"], "title": finding["title"], "status": status})
                decisions.append({"id": item["id"], "outcome": "HELD", "status": status})
                continue
            anchor = {**anchor, "line_start": mapped[0], "line_end": mapped[1]}
            note = (f"_Reviewed at `{snapshot['head_sha'][:12]}`; these lines are unchanged at "
                    f"`{head[:12]}`._\n\n")
        entry = patches.get(anchor["path"])
        reason = None
        if entry is None:
            reason = "file is not in the pull request's current diff"
        elif "patch" not in entry:
            reason = "GitHub does not show this file's diff (too large or binary)"
        elif not any(low <= anchor["line_start"] and anchor["line_end"] <= high
                     for low, high in hunk_ranges(entry["patch"])[anchor["side"]]):
            reason = f"lines {anchor['line_start']}-{anchor['line_end']} are not within one changed section of the current diff"
        if reason:
            general_items.append((item, finding, reason))
            decisions.append({"id": item["id"], "outcome": "GENERAL", "reason": reason})
            continue
        comment = {"path": anchor["path"], "body": note + item["body"], "side": anchor["side"],
                   "line": anchor["line_end"]}
        if anchor["line_start"] != anchor["line_end"]:
            comment.update(start_line=anchor["line_start"], start_side=anchor["side"])
        if len(comment["body"]) > MAX_BODY:
            raise ValueError(f"Comment for {item['id']} exceeds {MAX_BODY} characters")
        comments.append(comment)
        decisions.append({"id": item["id"], "outcome": "INLINE", "path": anchor["path"], "side": anchor["side"],
                          "line_start": anchor["line_start"], "line_end": anchor["line_end"]})
    parts = [draft["general"]]
    if moved:
        parts.append(f"_This review was produced at `{snapshot['head_sha'][:12]}` and published at "
                     f"`{head[:12]}` after checking each finding's lines._")
    if general_items:
        parts.append("## Findings not placed beside the code")
        for item, finding, why in general_items:
            commit = snapshot["head_sha"] if finding["side"] == "head" else snapshot["merge_base_sha"]
            parts.append(f"### {link(snapshot['repository'], commit, finding['file'], finding['line_start'], finding['line_end'])}\n\n"
                         f"_{why}._\n\n{item['body']}")
    if held:
        parts.append("## Not published because the code changed after the review\n\n"
                     "These need a new review run:\n\n" +
                     "\n".join(f"- `{h['id']}` ({h['status']}): {h['title']}" for h in held))
    if report["coverage_gaps"]:
        parts.append("## Coverage gaps\n\n" + "\n".join(f"- {gap}" for gap in report["coverage_gaps"]))
    body = "\n\n".join(parts) + "\n\n" + marker(snapshot)
    if len(body) > MAX_BODY:
        raise ValueError(f"General comment exceeds {MAX_BODY} characters; move findings beside the code or shorten it")
    request = {"commit_id": head, "event": "COMMENT", "body": body, "comments": comments}
    return {"request": request, "decisions": decisions, "omitted": omitted, "held": held, "moved": moved,
            "draft_sha256": hashlib.sha256(draft_text.encode()).hexdigest(),
            "draft_edited": hashlib.sha256(draft_text.encode()).hexdigest() != report["draft_sha256"]}


def prepare(root: Path, *, include_context_changed: bool = False) -> tuple[dict, dict, str]:
    report, draft_text = load(root)
    snapshot = report["snapshot"]
    endpoint = f"repos/{snapshot['repository']}/pulls/{snapshot['pr_number']}"
    metadata = gh(endpoint)
    files = [f for page in gh(endpoint + "/files?per_page=100", paginate=True) for f in page]
    plan = plan_review(root, report, draft_text, metadata, files, include_context_changed=include_context_changed)
    return report, plan, endpoint


def publish(root: Path, *, include_context_changed: bool = False) -> dict:
    report, plan, endpoint = prepare(root, include_context_changed=include_context_changed)
    # Exclusive local lock avoids double publication from concurrent invocations.
    # A crash leaves it behind deliberately: inspect GitHub before removing it.
    lock = root / "publication.lock"
    with lock.open("x"):
        pass
    try:
        pages = gh(endpoint + "/reviews?per_page=100", paginate=True)
        existing = [review for page in pages for review in page if marker(report["snapshot"]) in (review.get("body") or "")]
        receipt_path = root / "publication.json"
        previous = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
        if not existing:
            # One atomic request: the general comment and every placed comment, or nothing.
            review = gh(endpoint + "/reviews", payload=plan["request"])
            receipt = {"review": review, "reused_existing": False,
                       "posted_at_commit": plan["request"]["commit_id"], "request": plan["request"],
                       "draft_sha256": plan["draft_sha256"], "draft_edited": plan["draft_edited"],
                       "decisions": plan["decisions"], "omitted": plan["omitted"], "held": plan["held"]}
        elif previous.get("review", {}).get("id") == existing[0]["id"]:
            # Keep the record of what was sent the first time; today's plan was not posted.
            review = existing[0]
            receipt = {**previous, "review": review, "reused_existing": True}
            receipt.setdefault("request", None)
        else:
            review = existing[0]
            receipt = {"review": review, "reused_existing": True, "request": None,
                       "note": "Posted by an earlier publication without a receipt in this run directory; "
                               "the comments below are read back from GitHub."}
        # GitHub's create-review response omits the comments; read back what it stored. The PR-level
        # endpoint (unlike the per-review one) reports line and side.
        receipt["comments"] = [
            {key: comment.get(key) for key in ("id", "path", "side", "start_line", "line", "commit_id", "html_url", "body")}
            for page in gh(f"{endpoint}/comments?per_page=100", paginate=True)
            for comment in page if comment.get("pull_request_review_id") == review["id"]]
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        return receipt
    finally:
        lock.unlink()


def summary(plan: dict) -> str:
    counts = {}
    for decision in plan["decisions"]:
        counts[decision["outcome"]] = counts.get(decision["outcome"], 0) + 1
    lines = [f"Beside the code: {counts.get('INLINE', 0)}; general comment: {counts.get('GENERAL', 0)}; "
             f"held: {counts.get('HELD', 0)}; omitted: {counts.get('OMITTED', 0)}; "
             f"draft edited: {'yes' if plan['draft_edited'] else 'no'}; PR moved since review: {'yes' if plan['moved'] else 'no'}"]
    lines += [f"  {d['id']}: {d['outcome']} " + json.dumps({k: v for k, v in d.items() if k not in {'id', 'outcome'}})
              for d in plan["decisions"]]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--publish", action="store_true", help="Create the COMMENT review on GitHub")
    parser.add_argument("--include-context-changed", action="store_true",
                        help="Also place findings whose related files changed after the review")
    args = parser.parse_args()
    root = args.run_directory.resolve()
    try:
        if args.publish:
            print(json.dumps(publish(root, include_context_changed=args.include_context_changed), indent=2))
        else:
            _, plan, _ = prepare(root, include_context_changed=args.include_context_changed)
            print(summary(plan))
            print(json.dumps(plan["request"], indent=2))
    except DraftError as exc:
        raise SystemExit(f"review-draft.md: {exc}")


if __name__ == "__main__":
    main()
