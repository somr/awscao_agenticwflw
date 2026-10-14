"""Project rules for the reviewers: load the rule files named in review_knowledge, select the rules
that apply to a change, split them into batches and stage them for an agent.

Rule files live in agentic-sdlc-pr-review-kb/, which no agent may write (FORBIDDEN_ROOTS and the
write-scope hook). Each rule is a "## RULEID - [MANDATORY|NICE-TO-HAVE] Domain. Title" heading
followed by the RULE_SECTIONS as "###" headings; see agentic-sdlc-docs/plans/reviewer-knowledge.md.
Everything here is deterministic: the same files and changed paths give the same batches.
"""
from __future__ import annotations

import math
import posixpath
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import WorkflowContractError
from .artifacts import _sha256_bytes, _write_json, _write_text
from .source_config import REGISTRY_RELATIVE_PATH, _read_optional, load_project_file, select_source_settings

KNOWLEDGE_DIR = "agentic-sdlc-pr-review-kb"
# Optional settings of review_knowledge: default and the fixed bounds a project may choose within.
KNOWLEDGE_LIMITS = {
    "batch_max_rules": (15, 1, 50),
    "batch_max_kb": (32, 4, 128),
    "max_files": (32, 1, 128),
    "max_rules": (200, 1, 1000),
    "max_rule_kb": (8, 1, 32),
}
RULE_LEVELS = ("MANDATORY", "NICE-TO-HAVE")
RULE_SECTIONS = ("Applicable files", "Rule description", "Why we have the rule", "Examples: bad", "Examples: good")
RULE_HEADING = re.compile(
    r"^## (?P<id>[A-Z][A-Z0-9]*-[0-9]+) - \[(?P<level>[A-Z-]+)\] (?P<domain>[^.]+?)\. (?P<title>\S.*?)\.?$"
)
APPLICABLE_FILE = re.compile(r"^- `(?P<glob>[^`]+)`$")
DIFF_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def validate_knowledge_settings(raw: Any) -> dict[str, Any]:
    """Check the review_knowledge object and return it with the defaults applied."""
    if not isinstance(raw, dict):
        raise WorkflowContractError("review_knowledge must be an object with a files list")
    unknown = sorted(set(raw) - {"files", *KNOWLEDGE_LIMITS})
    if unknown:
        raise WorkflowContractError(f"review_knowledge has unknown keys: {', '.join(unknown)}")
    files = raw.get("files")
    if not isinstance(files, list) or not files:
        raise WorkflowContractError("review_knowledge.files must be a non-empty list of rule files")
    settings: dict[str, Any] = {}
    for key, (default, low, high) in KNOWLEDGE_LIMITS.items():
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise WorkflowContractError(f"review_knowledge.{key} must be an integer from {low} to {high}")
        settings[key] = value
    if len(files) > settings["max_files"]:
        raise WorkflowContractError(f"review_knowledge lists {len(files)} files; max_files is {settings['max_files']}")
    if settings["max_rule_kb"] > settings["batch_max_kb"]:
        raise WorkflowContractError("review_knowledge.max_rule_kb may not exceed batch_max_kb, or a rule could fit no batch")
    paths: list[str] = []
    for index, value in enumerate(files):
        label = f"review_knowledge.files[{index}]"
        if not isinstance(value, str) or not value or any(ord(char) < 32 or char in "\\*?[]:" for char in value):
            raise WorkflowContractError(f"{label} must be a plain relative path")
        if value != posixpath.normpath(value) or not value.startswith(KNOWLEDGE_DIR + "/") or not value.endswith(".md"):
            raise WorkflowContractError(f"{label} must be a .md file inside {KNOWLEDGE_DIR}/: {value!r}")
        if value in paths:
            raise WorkflowContractError(f"{label} lists {value!r} more than once")
        paths.append(value)
    return {"files": paths, **settings}


def _check_glob(value: str, label: str) -> str:
    parts = value.split("/")
    if value.startswith("/") or "\\" in value or ".." in parts or "" in parts or "." in parts:
        raise WorkflowContractError(f"{label} must be a relative glob such as app/**/*.py: {value!r}")
    return value


def parse_rules(text: str, source: str, max_rule_bytes: int) -> list[dict[str, Any]]:
    """Split one rule file into rules; any deviation from the agreed format is an error.

    Headings inside fenced code blocks (the examples) are ignored.
    """
    rules: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    section: str | None = None
    fenced = False

    def finish() -> None:
        if current is None:
            return
        label = f"{source}: rule {current['id']}"
        missing = [name for name in RULE_SECTIONS if name not in current["sections"]]
        if missing:
            raise WorkflowContractError(f"{label} has no section {', '.join(missing)}")
        if not current["globs"]:
            raise WorkflowContractError(f"{label} lists no applicable files")
        body = "\n".join(current.pop("lines")).strip() + "\n"
        if len(body.encode("utf-8")) > max_rule_bytes:
            raise WorkflowContractError(f"{label} is larger than max_rule_kb ({max_rule_bytes // 1024} KB)")
        current["text"] = body
        del current["sections"]
        rules.append(current)

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            finish()
            match = RULE_HEADING.match(line.rstrip())
            if not match or match["level"] not in RULE_LEVELS:
                raise WorkflowContractError(
                    f"{source}: {line.strip()!r} is not a rule heading "
                    "'## RULEID - [MANDATORY|NICE-TO-HAVE] Domain. Title'"
                )
            current = {"id": match["id"], "level": match["level"], "domain": match["domain"].strip(),
                       "title": match["title"].strip(), "file": source, "globs": [], "sections": [], "lines": []}
            section = None
        elif not fenced and line.startswith("### "):
            if current is None:
                raise WorkflowContractError(f"{source}: section {line.strip()!r} is outside a rule")
            section = line[4:].strip()
            if section not in RULE_SECTIONS:
                raise WorkflowContractError(f"{source}: rule {current['id']} has an unknown section {section!r}")
            if section in current["sections"]:
                raise WorkflowContractError(f"{source}: rule {current['id']} repeats the section {section!r}")
            current["sections"].append(section)
        elif current is not None and section == "Applicable files" and line.strip():
            match = APPLICABLE_FILE.match(line.strip())
            if not match:
                raise WorkflowContractError(
                    f"{source}: rule {current['id']} applicable files must be '- `glob`' lines, got {line.strip()!r}"
                )
            current["globs"].append(_check_glob(match["glob"], f"{source}: rule {current['id']} applicable file"))
        if current is not None:
            current["lines"].append(line)
    if fenced:
        raise WorkflowContractError(f"{source}: a code block is not closed")
    finish()
    if not rules:
        raise WorkflowContractError(f"{source} contains no rules")
    return rules


def load_review_knowledge(repo: Path) -> dict[str, Any] | None:
    """The project's validated rules, or None when review_knowledge is not configured.

    Read from the same settings object as the source roots (the project file, else the registry).
    """
    settings_source = select_source_settings(_read_optional(repo, REGISTRY_RELATIVE_PATH), load_project_file(repo))
    if not isinstance(settings_source, dict) or "review_knowledge" not in settings_source:
        return None
    settings = validate_knowledge_settings(settings_source["review_knowledge"])
    base = repo.resolve()
    folder = (base / KNOWLEDGE_DIR).resolve()
    files: list[dict[str, str]] = []
    rules: list[dict[str, Any]] = []
    for relative in settings["files"]:
        path = (base / relative).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise WorkflowContractError(f"review_knowledge file {relative!r} is missing or resolves outside {KNOWLEDGE_DIR}/")
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkflowContractError(f"review_knowledge file {relative!r} is not UTF-8") from exc
        files.append({"path": relative, "sha256": _sha256_bytes(data)})
        rules.extend(parse_rules(text, relative, settings["max_rule_kb"] * 1024))
    seen: dict[str, str] = {}
    for rule in rules:
        if rule["id"] in seen:
            raise WorkflowContractError(f"rule {rule['id']} is defined in both {seen[rule['id']]} and {rule['file']}")
        seen[rule["id"]] = rule["file"]
    if len(rules) > settings["max_rules"]:
        raise WorkflowContractError(f"review_knowledge has {len(rules)} rules; max_rules is {settings['max_rules']}")
    return {"settings": settings, "files": files, "rules": sorted(rules, key=lambda rule: rule["id"])}


def select_rules(knowledge: dict[str, Any], changed_files: list[str]) -> dict[str, Any]:
    """The rules whose applicable files match at least one changed file, each with the files it covers."""
    applicable: list[dict[str, Any]] = []
    skipped: list[str] = []
    for rule in knowledge["rules"]:
        covers = sorted(f for f in set(changed_files) if any(PurePosixPath(f).full_match(g) for g in rule["globs"]))
        if covers:
            applicable.append({**rule, "covers": covers})
        else:
            skipped.append(rule["id"])
    return {"applicable": applicable, "skipped": skipped}


def _rule_bytes(rule: dict[str, Any]) -> int:
    return len(rule["text"].encode("utf-8"))


def build_batches(rules: list[dict[str, Any]], settings: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Split rules into the fewest balanced batches within both budgets.

    Rules are ordered by domain then id, so related rules tend to share a batch; each batch takes
    up to an even share of the rules and starts early only when the next rule would exceed the
    size budget.
    """
    if not rules:
        return []
    max_bytes = settings["batch_max_kb"] * 1024
    ordered = sorted(rules, key=lambda rule: (rule["domain"].lower(), rule["id"]))
    count = max(math.ceil(len(ordered) / settings["batch_max_rules"]),
                math.ceil(sum(_rule_bytes(rule) for rule in ordered) / max_bytes))
    while True:
        share = math.ceil(len(ordered) / count)
        batches: list[list[dict[str, Any]]] = [[]]
        used = 0
        for rule in ordered:
            if batches[-1] and (len(batches[-1]) >= share or used + _rule_bytes(rule) > max_bytes):
                batches.append([])
                used = 0
            batches[-1].append(rule)
            used += _rule_bytes(rule)
        if len(batches) <= count or share == 1:
            return batches
        count += 1


def stage_rules(batches: list[list[dict[str, Any]]], selection: dict[str, Any], knowledge: dict[str, Any],
                destination: Path) -> list[Path]:
    """Write one file per rule, a short index per batch and index.json for the audit trail.

    Returns the batch index paths in batch order; an agent gets only its index in the prompt and
    reads rules/<ID>.md when it needs the full text.
    """
    indexes: list[Path] = []
    for number, batch in enumerate(batches, start=1):
        lines = [f"# Project rules, batch {number}", "",
                 "Full text with examples: rules/<RULE-ID>.md next to this index.", ""]
        for rule in batch:
            _write_text(destination / "rules" / f"{rule['id']}.md", rule["text"])
            lines.append(f"- {rule['id']} [{rule['level']}] {rule['domain']}. {rule['title']} "
                         f"(rules/{rule['id']}.md); changed files: {', '.join(rule['covers'])}")
        index = destination / f"index-b{number}.md"
        _write_text(index, "\n".join(lines))
        indexes.append(index)
    _write_json(destination / "index.json", {
        "files": knowledge["files"],
        "settings": {key: value for key, value in knowledge["settings"].items() if key != "files"},
        "applicable": [{"id": rule["id"], "level": rule["level"], "covers": rule["covers"]}
                       for rule in selection["applicable"]],
        "skipped": selection["skipped"],
        "batches": [[rule["id"] for rule in batch] for batch in batches],
    })
    return indexes


def added_lines(diff: str) -> dict[str, set[int]]:
    """Head-side line numbers that a git diff adds, per file; context and removed lines are excluded.

    Quoted (unusual) paths and deleted files are skipped. Used to keep rule findings to the lines a change adds.
    """
    files: dict[str, set[int]] = {}
    path, in_body, line_no = None, False, 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, in_body = None, False
        elif not in_body and line.startswith("+++ "):
            new = line[4:]
            path = None if new == "/dev/null" or new.startswith('"') else new.removeprefix("b/")
        elif match := DIFF_HUNK_HEADER.match(line):
            in_body, line_no = True, int(match[3])
        elif path is not None and in_body:
            if line.startswith("+"):
                files.setdefault(path, set()).add(line_no)
                line_no += 1
            elif line.startswith(" "):
                line_no += 1
    return files
