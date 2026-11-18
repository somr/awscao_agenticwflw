"""Publication identity and placement of review comments beside the code (Source review and the follow-up)."""
import hashlib
import json
import re


def finding_comment_marker(report: dict, finding_id: str) -> str:
    snapshot = report['snapshot']
    identity = [report['run_id'], snapshot['repository'], snapshot['pr_number'],
                snapshot['base_sha'], snapshot['head_sha'], finding_id]
    token = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return f'<!-- cao-source-finding:{token} -->'


# --- Placement beside the code ------------------------------------------------------
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def hunk_ranges(patch: str) -> dict[str, list[tuple[int, int]]]:
    """Line ranges per side that GitHub accepts for review comments in one file's hunks.

    RIGHT covers added and context lines of the head; LEFT covers removed and context
    lines of the base. Keep in sync with publish_source_review.hunk_ranges.
    """
    ranges: dict[str, list[tuple[int, int]]] = {"LEFT": [], "RIGHT": []}
    for line in patch.splitlines():
        match = HUNK_HEADER.match(line)
        if match:
            for side, start, count in (("LEFT", match[1], match[2]), ("RIGHT", match[3], match[4])):
                size = 1 if count is None else int(count)
                if size:
                    ranges[side].append((int(start), int(start) + size - 1))
    return ranges


def diff_hunks(diff: str) -> dict[str, dict[str, list[tuple[int, int]]]]:
    """Split a git diff into per-file hunk ranges; quoted (unusual) paths are skipped."""
    files: dict[str, dict[str, list[tuple[int, int]]]] = {}
    path, old, body = None, None, []

    def flush():
        if path is not None and not path.startswith('"'):
            files[path] = hunk_ranges("\n".join(body))

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            path, old, body = None, None, []
        elif line.startswith("--- ") and path is None and not body:
            old = line[4:].removeprefix("a/")
        elif line.startswith("+++ ") and path is None and not body:
            new = line[4:]
            path = old if new == "/dev/null" else new.removeprefix("b/")
        elif path is not None:
            body.append(line)
    flush()
    return files


def place_finding(finding: dict, hunks: dict) -> dict:
    """Deterministic anchor: inline only when the whole range sits in one hunk on its side."""
    side = "RIGHT" if finding["side"] == "head" else "LEFT"
    start, end = finding["line_start"], finding["line_end"]
    if finding["file"] not in hunks:
        return {"mode": "GENERAL", "reason": "File has no changed lines in the diff"}
    if not any(low <= start and end <= high for low, high in hunks[finding["file"]][side]):
        return {"mode": "GENERAL", "reason": f"Lines {start}-{end} are not within one changed section of the diff"}
    return {"mode": "INLINE", "path": finding["file"], "side": side, "line_start": start, "line_end": end}
