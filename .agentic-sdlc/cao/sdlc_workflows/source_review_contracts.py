"""Source-review contracts and routing shared with source remediation."""
from __future__ import annotations
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Any
from .errors import WorkflowContractError

POLICY_VERSION = "source-review-v1"

CONFIDENCE_THRESHOLD = 0.90

PROTECTED = {
    "ARCHITECTURE", "PUBLIC_API", "DB_SCHEMA", "AUTHN_AUTHZ",
    "CRYPTO_SECRETS", "DATA_LOSS", "CONCURRENCY", "BUSINESS_RULES",
    "INFRA_TOPOLOGY", "DEPENDENCY",
}

CATEGORIES = PROTECTED | {"CORRECTNESS", "TESTING", "RESOURCE_HANDLING", "SECURITY", "OTHER"}

ELIGIBILITY = (
    "evidence_sufficient", "intended_behavior_clear", "localized_and_bounded",
    "deterministically_verifiable",
)

def require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkflowContractError(message)

def string(value: Any, label: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"{label}: nonempty string required")
    return value

def strings(value: Any, label: str) -> list[str]:
    require(isinstance(value, list), f"{label}: array required")
    for item in value:
        string(item, label)
    return value

def sha(value: Any) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None,
            "An exact 40-character commit SHA is required")
    return value

def safe_path(value: Any) -> str:
    string(value, "file")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(p not in {".", "..", ".git"} for p in path.parts)
            and str(path) == value and "\\" not in value and not any(ord(c) < 32 for c in value),
            f"Unsafe source path: {value!r}")
    return value

def mapping_contract(value: Any, snapshot: dict) -> dict:
    require(isinstance(value, dict), "Mapping must be an object")
    string(value.get("summary"), "summary")
    require(isinstance(value.get("files"), list), "files array required")
    names = []
    for item in value["files"]:
        require(isinstance(item, dict), "file mapping must be an object")
        names.append(safe_path(item.get("file")))
        require(item.get("scope") in {"SOURCE", "TEST", "OUT_OF_SCOPE"}, "Invalid file scope")
        string(item.get("reason"), "scope reason")
        strings(item.get("related_files"), "related_files")
        strings(item.get("sensitive_boundaries"), "sensitive_boundaries")
        require(set(item["sensitive_boundaries"]) <= PROTECTED, "Unknown sensitive boundary")
    require(len(names) == len(set(names)) and set(names) == set(snapshot["changed_files"]),
            "Mapping must account for every changed file exactly once")
    strings(value.get("coverage_gaps"), "coverage_gaps")
    return value

def validate_finding(finding: Any, work: Path, snapshot: dict, mapping: dict) -> dict:
    require(isinstance(finding, dict), "Finding must be an object")
    for key in ("candidate_id", "title", "symbol", "failure_scenario", "source_evidence",
                "consequence", "remediation_direction", "verification_method"):
        string(finding.get(key), key)
    name = safe_path(finding.get("file"))
    scopes = {i["file"]: i["scope"] for i in mapping["files"]}
    require(scopes.get(name) in {"SOURCE", "TEST"}, "Findings must anchor to changed source/test files")
    require(finding.get("side") in {"base", "head"}, "side must be base or head")
    path = work / "source" / finding["side"] / name
    require(path.is_file(), "Finding source is unavailable")
    start, end = finding.get("line_start"), finding.get("line_end")
    require(type(start) is int and type(end) is int and 1 <= start <= end <= len(path.read_text().splitlines()),
            "Finding line range is outside source")
    require(finding.get("category") in CATEGORIES, "Unknown category")
    require(finding.get("severity") in {"LOW", "MEDIUM", "HIGH"}, "Invalid severity")
    confidence = finding.get("confidence")
    require(type(confidence) in {int, float} and math.isfinite(confidence) and 0 <= confidence <= 1,
            "confidence must be a finite number in [0,1]")
    for flag in (*ELIGIBILITY, "protected_boundary", "introduced_by_pr"):
        require(type(finding.get(flag)) is bool, f"{flag} must be a boolean")
    require(finding["introduced_by_pr"], "Pre-existing issues are out of scope")
    return finding

def review_contract(value: Any, work: Path, snapshot: dict, mapping: dict) -> dict:
    require(isinstance(value, dict), "Review must be an object")
    require(isinstance(value.get("findings"), list), "findings array required")
    ids = []
    for finding in value["findings"]:
        validate_finding(finding, work, snapshot, mapping)
        ids.append(finding["candidate_id"])
    require(len(ids) == len(set(ids)), "Duplicate candidate IDs")
    strings(value.get("coverage_gaps"), "coverage_gaps")
    return value

def adjudication_contract(value: Any, candidates: list[dict], work: Path, snapshot: dict, mapping: dict,
                          reported_gaps: list[dict] = ()) -> dict:
    require(isinstance(value, dict) and isinstance(value.get("decisions"), list), "decisions array required")
    available = {f["candidate_id"] for f in candidates}
    seen = set()
    accepted = set()
    duplicates = []
    for decision in value["decisions"]:
        require(isinstance(decision, dict), "Decision must be an object")
        cid = decision.get("candidate_id")
        require(cid in available and cid not in seen, "Unknown or repeated candidate decision")
        seen.add(cid)
        string(decision.get("reason"), "decision reason")
        outcome = decision.get("disposition")
        require(outcome in {"ACCEPT", "REJECT", "DUPLICATE"}, "Invalid disposition")
        if outcome == "ACCEPT":
            finding = validate_finding(decision.get("finding"), work, snapshot, mapping)
            require(finding["candidate_id"] == cid, "Decision/finding ID mismatch")
            require(finding["evidence_sufficient"], "Unsubstantiated findings cannot be accepted")
            accepted.add(cid)
        elif outcome == "DUPLICATE":
            duplicates.append(decision.get("duplicate_of"))
    require(seen == available, "Validator must account for every candidate")
    require(all(cid in accepted for cid in duplicates), "Duplicates must reference accepted candidates")
    # Merging reworded gaps is an agent decision; Python only guarantees none is lost.
    require(isinstance(value.get("coverage_gaps"), list), "coverage_gaps: array required")
    accounted = []
    for entry in value["coverage_gaps"]:
        require(isinstance(entry, dict), "Coverage gap must be an object")
        string(entry.get("gap"), "coverage gap")
        accounted += strings(entry.get("covers"), "covers")
    restated = strings(value.get("snapshot_restatements", []), "snapshot_restatements")
    require(not restated or bool(snapshot["coverage_gaps"]), "No snapshot coverage gap to restate")
    accounted += restated
    require(len(accounted) == len(set(accounted)) and set(accounted) == {g["id"] for g in reported_gaps},
            "Validator must account for every reported coverage gap exactly once")
    return value

def route_finding(finding: dict, snapshot: dict, mapping: dict) -> dict:
    reasons = []
    if finding["severity"] == "HIGH":
        reasons.append("High impact")
    boundaries = next(item["sensitive_boundaries"] for item in mapping["files"] if item["file"] == finding["file"])
    if finding["protected_boundary"] or finding["category"] in PROTECTED or boundaries:
        reasons.append("Protected boundary: " + ", ".join(boundaries or [finding["category"]]))
    if finding["confidence"] < CONFIDENCE_THRESHOLD:
        reasons.append(f"Confidence below {CONFIDENCE_THRESHOLD}")
    for flag in ELIGIBILITY:
        if not finding[flag]:
            reasons.append(f"Eligibility condition not met: {flag}")
    # Semantic identity deliberately excludes line numbers and HEAD so normal
    # line movement does not change IDs. Wording/symbol changes may still do so.
    identity = [snapshot["repository"], snapshot["pr_number"], finding["file"],
                finding["symbol"], finding["category"], finding["failure_scenario"]]
    fid = "SR-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    return {**finding, "stable_id": fid, "reviewed_head_sha": snapshot["head_sha"],
            "route": "HUMAN_REQUIRED" if reasons else "AUTO_FIX",
            "routing_reasons": reasons or ["All automatic-fix eligibility conditions satisfied"],
            "policy_version": POLICY_VERSION}
