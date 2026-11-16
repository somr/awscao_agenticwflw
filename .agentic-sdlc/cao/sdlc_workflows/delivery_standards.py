"""Delivery's standards review: the project's rules checked against each review round's diff.

The rules come from review_knowledge.py. Each round selects the rules whose applicable files the
diff adds lines to; from the second round on, only rules with a covered file that changed since the
previous round are reviewed again and the others keep their verdict and findings (D15). The
reviewer's impact stands (D9). NICE-TO-HAVE findings (unless rated HIGH) and findings outside the
lines the diff adds become suggestions, which are never remediated and never block (D11, D14); rule
findings in a file the PR reviewer flags with a protected category go to the developer (see
route_sensitive_files). Batches hold at most batch_max_files files, and the reviewer gives every
rule a verdict per file it covers, so no file is skipped silently.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import WorkflowContractError
from .review_knowledge import (
    RULE_VERDICT_VALUES,
    batch_files,
    build_batches,
    check_file_verdicts,
    diff_for_files,
    rule_status,
    select_rules,
    stage_rules,
)

STANDARDS_REVIEWER = "sdlc_standards_reviewer"
RULE_ID = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+")
STANDARDS_FINDING_EXAMPLE = {
    "id": "N1", "standard_ref": "RULE-ID", "file": "<source-root>/path/to/changed_file",
    "line_start": 1, "line_end": 1, "location": "short description of where in the file",
    "category": "STYLE | DOCUMENTATION | OTHER", "impact": "LOW | MEDIUM", "confidence": 0.0,
    "failure_scenario": "the rule the added code violates and how",
    "consequence": "why the rule matters here", "related_acceptance_criterion": None,
    "remediation_direction": "what should change", "localized_and_bounded": True,
    "deterministically_verifiable": True, "automation_eligibility": "AUTO_FIX | DEVELOPER_REQUIRED",
    "reason": "why you classified automation eligibility this way",
}


def plan_standards_round(knowledge: dict[str, Any], added: dict[str, set[int]], changed_since: set[str] | None,
                         previous: dict[str, Any] | None) -> dict[str, Any]:
    """Which applicable rules this round reviews, and which keep the previous round's outcome.

    added: head lines the round's diff adds, per file. changed_since: files changed since the previous
    round's reviewed commit (None on the first round of a run).
    """
    selection = select_rules(knowledge, sorted(added))
    reviewed, carried = [], []
    earlier = (previous or {}).get("rules", {})
    for rule in selection["applicable"]:
        unchanged = changed_since is not None and rule["id"] in earlier and not set(rule["covers"]) & changed_since
        (carried if unchanged else reviewed).append(rule)
    return {"selection": selection, "reviewed": reviewed, "carried": carried,
            "batches": build_batches(reviewed, knowledge["settings"])}


def build_standards_reviewer_prompt(*, repo: Path, number: int, diff_text: str, pr_head_sha: str,
                                    knowledge_dir: Path, index: str, batch: list[dict[str, Any]]) -> str:
    """The prompt for one batch; the diff is narrowed to the batch's files.

    Shared instructions first, then the diff, and the batch index last, so calls for the same files
    share the longest common start.
    """
    diff_text = diff_for_files(diff_text, batch_files(batch))
    return f"""Check the candidate PR diff below for repository {repo} against one batch of the project's own rules. You did not write this diff.

Check only the rules in the index at the end, only the changed files the index lists for each rule, and only lines the diff adds. A violation of a listed rule is a finding even without a failure scenario: describe the violated rule and the offending code in failure_scenario. Code the diff does not add is out of scope.
The full text of each rule, with its exceptions and examples, is in {knowledge_dir}/rules/<RULE-ID>.md; read it whenever the title is not enough.
Go through every listed file for every rule, and report every occurrence: one finding per rule and location. Do not stop at the first occurrence in a file, and do not let one finding stand for others.
For every rule in the index, give exactly one verdict per file the index lists for it: VIOLATED (finding_ids lists every finding citing that rule in that file), NOT_VIOLATED, or NOT_ASSESSED (only if you could not check it; say why in coverage_gaps). The workflow checks that every rule and file has a verdict.
Every finding sets standard_ref to its rule id and line_start/line_end to lines of the file at the PR HEAD, one finding per rule and location. Use STYLE or DOCUMENTATION unless another category fits better. Impact is normally LOW or MEDIUM for MANDATORY rules and LOW for NICE-TO-HAVE rules; rate it higher only when the violation also causes a concrete problem (a failure, a security exposure), and say so in consequence. Mark a finding AUTO_FIX only when an edit to the file can resolve it.
PR HEAD SHA under review: {pr_head_sha}

Return {{"rules":[{{"id":"RULE-ID", "files":[{{"path":"<changed file listed for the rule>", "status":"VIOLATED|NOT_VIOLATED|NOT_ASSESSED", "finding_ids":[]}}]}}], "findings":[{json.dumps(STANDARDS_FINDING_EXAMPLE)}], "coverage_gaps":[]}}.
The example is a schema, not a finding.

Candidate diff of this batch's files (already computed by the workflow; do not run git yourself):
```diff
{diff_text}
```

Batch {number}:
{index}"""


def check_standards_answer(value: Any, batch: list[dict[str, Any]], repo: Path, prefix: str,
                           fill_missing: bool = False) -> dict[str, Any]:
    """Validate one batch's answer; return its findings (ids prefixed, rule level added), verdicts and gaps.

    verdicts are per rule and file (see review_knowledge.check_file_verdicts); fill_missing is for the
    last contract attempt. Routing (automation eligibility) is left to the PR review classifier; see
    split_suggestions.
    """
    if not isinstance(value, dict) or not isinstance(value.get("findings"), list):
        raise WorkflowContractError("Standards review must be an object with rules and findings arrays")
    rules = {rule["id"]: rule for rule in batch}
    citing: dict[tuple[str, str], list[str]] = {}
    findings: list[dict[str, Any]] = []
    for index, raw in enumerate(value["findings"]):
        label = f"findings[{index}]"
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"].strip():
            raise WorkflowContractError(f"{label} needs an id")
        ref = raw.get("standard_ref")
        if ref not in rules:
            raise WorkflowContractError(f"{label}.standard_ref must name a rule of this batch: {ref!r}")
        if raw.get("file") not in rules[ref]["covers"]:
            raise WorkflowContractError(f"{label}: rule {ref} does not apply to {raw.get('file')!r} in this batch")
        path = repo / raw["file"]
        start, end = raw.get("line_start"), raw.get("line_end")
        if not (type(start) is int and type(end) is int and path.is_file()
                and 1 <= start <= end <= len(path.read_text(encoding="utf-8", errors="replace").splitlines())):
            raise WorkflowContractError(f"{label} line range is outside {raw['file']}")
        citing.setdefault((ref, raw["file"]), []).append(raw["id"])
        findings.append({**raw, "id": f"{prefix}:{raw['id']}", "rule_level": rules[ref]["level"]})
    if len({f["id"] for f in findings}) != len(findings):
        raise WorkflowContractError("Duplicate finding ids")
    file_verdicts, filled = check_file_verdicts(value.get("rules"), batch, citing, fill_missing)
    gaps = value.get("coverage_gaps", [])
    if not isinstance(gaps, list) or not all(isinstance(gap, str) and gap.strip() for gap in gaps):
        raise WorkflowContractError("coverage_gaps must be an array of strings")
    return {"findings": findings, "file_verdicts": file_verdicts, "coverage_gaps": gaps + filled}


def merge_file_verdicts(answers: list[dict[str, dict[str, str]]]) -> dict[str, dict[str, str]]:
    """Per-file verdicts of several batches (a rule's files can be split over batches) in one map."""
    merged: dict[str, dict[str, str]] = {}
    for answer in answers:
        for rule_id, per_file in answer.items():
            merged.setdefault(rule_id, {}).update(per_file)
    return {rule_id: dict(sorted(per_file.items())) for rule_id, per_file in sorted(merged.items())}


def rule_verdicts(file_verdicts: dict[str, dict[str, str]]) -> dict[str, str]:
    """Each rule's overall verdict: VIOLATED in any file, else NOT_ASSESSED in any file, else NOT_VIOLATED."""
    return {rule_id: rule_status(per_file) for rule_id, per_file in file_verdicts.items()}


def split_suggestions(findings: list[dict[str, Any]], added: dict[str, set[int]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classified rule findings -> (findings that are routed normally, suggestions)."""
    routed, suggestions = [], []
    for finding in findings:
        lines = added.get(finding["file"], set())
        if not any(line in lines for line in range(finding["line_start"], finding["line_end"] + 1)):
            reason = "Outside the lines this diff adds: suggestion only"
        elif finding["rule_level"] == "NICE-TO-HAVE" and finding["impact"] != "HIGH":
            reason = f"NICE-TO-HAVE rule {finding['standard_ref']}: suggestion only"
        else:
            routed.append(finding)
            continue
        suggestions.append({**finding, "automation_eligibility": "SUGGESTION", "suggestion_reason": reason})
    return routed, suggestions


def route_sensitive_files(findings: list[dict[str, Any]], pr_findings: list[dict[str, Any]],
                          protected: set[str]) -> list[dict[str, Any]]:
    """Rule findings in a file the PR reviewer flags with a protected category go to the developer.

    Delivery has no file-level sensitivity map like Source review's mapper; the PR reviewer's protected
    findings in the same review are the signal that the file is business-sensitive.
    """
    flagged: dict[str, list[str]] = {}
    for finding in pr_findings:
        if finding["category"] in protected:
            flagged.setdefault(finding["file"], []).append(f"{finding['id']} {finding['category']}")
    routed = []
    for finding in findings:
        if finding["file"] in flagged and finding["automation_eligibility"] == "AUTO_FIX":
            finding = {**finding, "automation_eligibility": "DEVELOPER_REQUIRED", "policy_overrode_agent_classification": True,
                       "reason": f"{finding['reason']} Business-sensitive file (PR review: {', '.join(flagged[finding['file']])}): a human fixes it."}
        routed.append(finding)
    return routed


def carry_forward(previous: dict[str, Any], carried: list[dict[str, Any]], round_no: int) -> dict[str, Any]:
    """The previous round's verdicts, findings and suggestions for rules this round does not review again."""
    ids = {rule["id"] for rule in carried}
    def keep(items):
        return [{**item, "carried_from_round": item.get("carried_from_round", round_no - 1)}
                for item in items if item.get("standard_ref") in ids]
    return {"file_verdicts": {rule_id: previous["rules"][rule_id] for rule_id in sorted(ids)},
            "findings": keep(previous["findings"]), "suggestions": keep(previous["suggestions"])}


def stage_round(plan: dict[str, Any], knowledge: dict[str, Any], destination: Path) -> list[str]:
    """Stage this round's batches; return each batch's index text."""
    indexes = stage_rules(plan["batches"], plan["selection"], knowledge, destination)
    return [path.read_text(encoding="utf-8") for path in indexes]
