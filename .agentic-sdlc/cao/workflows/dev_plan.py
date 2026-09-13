"""CAO Planning Workflow 1 for an agentic SDLC.

Flow:
  mock/raw source retrieval -> context normalization -> deterministic validation
  -> repository analysis -> plan authoring -> independent plan review
  -> bounded revision / re-normalization loop -> human approval handoff.

The workflow never approves the generated plan and never modifies production
source code. Runtime evidence is written under .agentic-sdlc/runtime/, while the
final reviewed plan and execution manifest are written under
.agentic-sdlc/records/<ticket>/.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from cao_workflow import emit_output, get_inputs, step


INPUTS = {
    "ticket_id": {"type": "string", "required": True},
    "repository_root": {"type": "path", "required": True},
    "source_dir": {"type": "path", "required": True},
    "baseline_sha": {"type": "string", "required": True},
    "base_branch": {"type": "string", "required": False, "default": "main"},
    "max_review_rounds": {"type": "int", "required": False, "default": 3},
}

PROVIDER = "claude_code"
CONTEXT_NORMALIZER = "sdlc_context_normalizer"
PLANNING_ANALYST = "sdlc_planning_analyst"
PLAN_AUTHOR = "sdlc_plan_author"
PLAN_REVIEWER = "sdlc_plan_reviewer"
STEP_TIMEOUT_SECONDS = 1800

REVIEW_STATUSES = {
    "PASS",
    "CHANGES_REQUIRED",
    "CONTEXT_RENORMALIZATION_REQUIRED",
    "HUMAN_DECISION_REQUIRED",
}
DISPOSITIONS = {
    "PLAN_CHANGE_REQUIRED",
    "CONTEXT_RENORMALIZATION_REQUIRED",
    "HUMAN_DECISION_REQUIRED",
    "ADVISORY",
}
IMPACTS = {"LOW", "MEDIUM", "HIGH"}
CATEGORIES = {
    "CONTEXT",
    "REQUIREMENTS",
    "ARCHITECTURE",
    "COMPATIBILITY",
    "SECURITY",
    "DATA",
    "TESTING",
    "OPERATIONS",
    "DEPENDENCY",
    "SCOPE",
    "OTHER",
}


class WorkflowContractError(ValueError):
    """Raised when deterministic validation rejects workflow data."""


def _safe_component(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise WorkflowContractError(
            f"{label} must contain only letters, digits, '.', '_' or '-': {value!r}"
        )
    return value


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value.rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _parse_json_output(text: str, label: str) -> Any:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1]).strip()
            if candidate.startswith("json\n"):
                candidate = candidate[5:].lstrip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise WorkflowContractError(f"{label} did not return valid JSON: {exc}") from exc


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowContractError(f"{path} must be an object")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise WorkflowContractError(f"{path} must be an array")
    return value


def _require_str(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowContractError(f"{path} must be a non-empty string")
    return value


def _require_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise WorkflowContractError(f"{path} must be a boolean")
    return value


def _require_keys(obj: dict[str, Any], keys: set[str], path: str) -> None:
    missing = sorted(keys - set(obj))
    if missing:
        raise WorkflowContractError(f"{path} is missing required keys: {', '.join(missing)}")


def _validate_source_ref(ref: Any, path: str, source_ids: set[str]) -> None:
    obj = _require_dict(ref, path)
    _require_keys(obj, {"source_id", "location"}, path)
    source_id = _require_str(obj["source_id"], f"{path}.source_id")
    _require_str(obj["location"], f"{path}.location")
    if source_id not in source_ids:
        raise WorkflowContractError(f"{path}.source_id references unknown source {source_id!r}")


def _validate_sourced_statement(value: Any, path: str, source_ids: set[str]) -> None:
    obj = _require_dict(value, path)
    _require_keys(obj, {"text", "sources"}, path)
    _require_str(obj["text"], f"{path}.text")
    refs = _require_list(obj["sources"], f"{path}.sources")
    if not refs:
        raise WorkflowContractError(f"{path}.sources must contain at least one source")
    for i, ref in enumerate(refs):
        _validate_source_ref(ref, f"{path}.sources[{i}]", source_ids)


def validate_planning_context(context: Any, ticket_id: str) -> list[str]:
    """Validate the contract-critical subset of planning-context.schema.json.

    Kept stdlib-only so the CAO workflow does not depend on jsonschema being
    installed in the workflow subprocess. The canonical JSON Schema remains the
    documentation/source contract; this validates the fields that control routing.
    Returns readiness blockers (structurally valid != ready to plan).
    """
    obj = _require_dict(context, "context")
    required_top = {
        "schema_version",
        "ticket",
        "sources",
        "problem_statement",
        "scope",
        "acceptance_criteria",
        "functional_requirements",
        "non_functional_requirements",
        "constraints",
        "dependencies",
        "open_questions",
        "contradictions",
        "retrieval_warnings",
    }
    _require_keys(obj, required_top, "context")
    if obj["schema_version"] != "1.0":
        raise WorkflowContractError("context.schema_version must equal '1.0'")

    ticket = _require_dict(obj["ticket"], "context.ticket")
    _require_keys(ticket, {"id", "summary"}, "context.ticket")
    if _require_str(ticket["id"], "context.ticket.id") != ticket_id:
        raise WorkflowContractError("normalized ticket id does not match workflow ticket_id")
    _require_str(ticket["summary"], "context.ticket.summary")

    sources = _require_list(obj["sources"], "context.sources")
    source_ids: set[str] = set()
    for i, source in enumerate(sources):
        s = _require_dict(source, f"context.sources[{i}]")
        _require_keys(s, {"source_id", "type", "title", "status"}, f"context.sources[{i}]")
        sid = _require_str(s["source_id"], f"context.sources[{i}].source_id")
        if sid in source_ids:
            raise WorkflowContractError(f"duplicate source_id {sid!r}")
        source_ids.add(sid)
        if s["type"] not in {"JIRA", "CONFLUENCE", "HUMAN", "OTHER"}:
            raise WorkflowContractError(f"invalid source type for {sid}")
        if s["status"] not in {"RETRIEVED", "PARTIAL", "UNAVAILABLE"}:
            raise WorkflowContractError(f"invalid source status for {sid}")

    _validate_sourced_statement(obj["problem_statement"], "context.problem_statement", source_ids)

    scope = _require_dict(obj["scope"], "context.scope")
    _require_keys(scope, {"in", "out", "uncertain"}, "context.scope")
    for bucket in ("in", "out", "uncertain"):
        for i, item in enumerate(_require_list(scope[bucket], f"context.scope.{bucket}")):
            _validate_sourced_statement(item, f"context.scope.{bucket}[{i}]", source_ids)

    for group in (
        "acceptance_criteria",
        "functional_requirements",
        "non_functional_requirements",
        "constraints",
    ):
        seen_ids: set[str] = set()
        for i, item in enumerate(_require_list(obj[group], f"context.{group}")):
            r = _require_dict(item, f"context.{group}[{i}]")
            _require_keys(r, {"id", "text", "origin", "sources"}, f"context.{group}[{i}]")
            rid = _require_str(r["id"], f"context.{group}[{i}].id")
            if rid in seen_ids:
                raise WorkflowContractError(f"duplicate requirement id {rid!r} in {group}")
            seen_ids.add(rid)
            _require_str(r["text"], f"context.{group}[{i}].text")
            if r["origin"] not in {"EXPLICIT", "NORMALIZED_FROM_SOURCE"}:
                raise WorkflowContractError(f"invalid origin for {group}[{i}]")
            refs = _require_list(r["sources"], f"context.{group}[{i}].sources")
            if not refs:
                raise WorkflowContractError(f"context.{group}[{i}].sources cannot be empty")
            for j, ref in enumerate(refs):
                _validate_source_ref(ref, f"context.{group}[{i}].sources[{j}]", source_ids)

    for i, dep in enumerate(_require_list(obj["dependencies"], "context.dependencies")):
        _validate_sourced_statement(dep, f"context.dependencies[{i}]", source_ids)

    blockers: list[str] = []
    open_questions = _require_list(obj["open_questions"], "context.open_questions")
    for i, item in enumerate(open_questions):
        q = _require_dict(item, f"context.open_questions[{i}]")
        _require_keys(q, {"id", "question", "reason", "blocking", "sources"}, f"context.open_questions[{i}]")
        qid = _require_str(q["id"], f"context.open_questions[{i}].id")
        _require_str(q["question"], f"context.open_questions[{i}].question")
        _require_str(q["reason"], f"context.open_questions[{i}].reason")
        blocking = _require_bool(q["blocking"], f"context.open_questions[{i}].blocking")
        for j, ref in enumerate(_require_list(q["sources"], f"context.open_questions[{i}].sources")):
            _validate_source_ref(ref, f"context.open_questions[{i}].sources[{j}]", source_ids)
        if blocking:
            blockers.append(f"blocking open question {qid}")

    contradictions = _require_list(obj["contradictions"], "context.contradictions")
    for i, item in enumerate(contradictions):
        c = _require_dict(item, f"context.contradictions[{i}]")
        _require_keys(c, {"id", "description", "blocking", "sources"}, f"context.contradictions[{i}]")
        cid = _require_str(c["id"], f"context.contradictions[{i}].id")
        _require_str(c["description"], f"context.contradictions[{i}].description")
        blocking = _require_bool(c["blocking"], f"context.contradictions[{i}].blocking")
        refs = _require_list(c["sources"], f"context.contradictions[{i}].sources")
        if len(refs) < 2:
            raise WorkflowContractError(f"context.contradictions[{i}].sources needs at least two sources")
        for j, ref in enumerate(refs):
            _validate_source_ref(ref, f"context.contradictions[{i}].sources[{j}]", source_ids)
        if blocking:
            blockers.append(f"blocking contradiction {cid}")

    warnings = _require_list(obj["retrieval_warnings"], "context.retrieval_warnings")
    for i, item in enumerate(warnings):
        w = _require_dict(item, f"context.retrieval_warnings[{i}]")
        _require_keys(w, {"source_id", "severity", "message", "blocking"}, f"context.retrieval_warnings[{i}]")
        sid = _require_str(w["source_id"], f"context.retrieval_warnings[{i}].source_id")
        if sid not in source_ids:
            raise WorkflowContractError(f"retrieval warning references unknown source {sid!r}")
        if w["severity"] not in {"INFO", "WARNING", "ERROR"}:
            raise WorkflowContractError(f"invalid retrieval warning severity for {sid}")
        _require_str(w["message"], f"context.retrieval_warnings[{i}].message")
        blocking = _require_bool(w["blocking"], f"context.retrieval_warnings[{i}].blocking")
        if blocking:
            blockers.append(f"blocking retrieval warning for {sid}")

    if not obj["acceptance_criteria"] and not obj["functional_requirements"]:
        blockers.append("no acceptance criteria or functional requirements are available")
    return blockers


def render_planning_context(context: dict[str, Any]) -> str:
    """Render a deterministic human-readable view from canonical JSON."""
    lines: list[str] = [
        f"# Planning Context — {context['ticket']['id']}",
        "",
        f"**Summary:** {context['ticket']['summary']}",
        "",
        "## Problem statement",
        context["problem_statement"]["text"],
        "",
        "## Scope",
    ]
    for label, key in (("In scope", "in"), ("Out of scope", "out"), ("Uncertain", "uncertain")):
        lines.extend([f"### {label}"])
        items = context["scope"][key]
        lines.extend([f"- {item['text']}" for item in items] or ["- None recorded."])
        lines.append("")

    for heading, key in (
        ("Acceptance criteria", "acceptance_criteria"),
        ("Functional requirements", "functional_requirements"),
        ("Non-functional requirements", "non_functional_requirements"),
        ("Constraints", "constraints"),
    ):
        lines.append(f"## {heading}")
        items = context[key]
        if not items:
            lines.append("- None recorded.")
        for item in items:
            refs = ", ".join(f"{r['source_id']}:{r['location']}" for r in item["sources"])
            lines.append(f"- **{item['id']}** — {item['text']}  _[{item['origin']}; {refs}]_")
        lines.append("")

    lines.append("## Open questions")
    for item in context["open_questions"]:
        lines.append(f"- **{item['id']}** ({'BLOCKING' if item['blocking'] else 'non-blocking'}) — {item['question']}")
    if not context["open_questions"]:
        lines.append("- None.")
    lines.append("")

    lines.append("## Contradictions")
    for item in context["contradictions"]:
        lines.append(f"- **{item['id']}** ({'BLOCKING' if item['blocking'] else 'non-blocking'}) — {item['description']}")
    if not context["contradictions"]:
        lines.append("- None.")
    lines.append("")

    lines.append("## Sources")
    for source in context["sources"]:
        lines.append(f"- `{source['source_id']}` — {source['title']} ({source['type']}, {source['status']})")
    return "\n".join(lines).rstrip() + "\n"


def validate_review(review: Any) -> dict[str, Any]:
    obj = _require_dict(review, "review")
    _require_keys(obj, {"review_status", "summary", "findings"}, "review")
    status = _require_str(obj["review_status"], "review.review_status")
    if status not in REVIEW_STATUSES:
        raise WorkflowContractError(f"invalid review_status {status!r}")
    _require_str(obj["summary"], "review.summary")
    findings = _require_list(obj["findings"], "review.findings")
    dispositions: list[str] = []
    for i, item in enumerate(findings):
        f = _require_dict(item, f"review.findings[{i}]")
        required = {
            "id", "impact", "category", "disposition", "plan_section",
            "description", "evidence", "required_action", "confidence",
        }
        _require_keys(f, required, f"review.findings[{i}]")
        _require_str(f["id"], f"review.findings[{i}].id")
        if f["impact"] not in IMPACTS:
            raise WorkflowContractError(f"invalid impact in finding {i}")
        if f["category"] not in CATEGORIES:
            raise WorkflowContractError(f"invalid category in finding {i}")
        if f["disposition"] not in DISPOSITIONS:
            raise WorkflowContractError(f"invalid disposition in finding {i}")
        dispositions.append(f["disposition"])
        _require_str(f["plan_section"], f"review.findings[{i}].plan_section")
        _require_str(f["description"], f"review.findings[{i}].description")
        evidence = _require_list(f["evidence"], f"review.findings[{i}].evidence")
        for j, evidence_item in enumerate(evidence):
            _require_str(evidence_item, f"review.findings[{i}].evidence[{j}]")
        _require_str(f["required_action"], f"review.findings[{i}].required_action")
        confidence = f["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise WorkflowContractError(f"review.findings[{i}].confidence must be between 0 and 1")

    expected = "PASS"
    if "HUMAN_DECISION_REQUIRED" in dispositions:
        expected = "HUMAN_DECISION_REQUIRED"
    elif "CONTEXT_RENORMALIZATION_REQUIRED" in dispositions:
        expected = "CONTEXT_RENORMALIZATION_REQUIRED"
    elif "PLAN_CHANGE_REQUIRED" in dispositions:
        expected = "CHANGES_REQUIRED"
    if status != expected:
        raise WorkflowContractError(
            f"review_status {status!r} conflicts with findings; expected {expected!r}"
        )
    return obj


def retrieve_fixture_sources(source_dir: Path, raw_dir: Path, ticket_id: str) -> dict[str, Any]:
    """Deterministic local adapter that stands in for Jira/Confluence retrieval."""
    manifest_path = source_dir / "context.json"
    if not manifest_path.is_file():
        raise WorkflowContractError(f"mock source manifest not found: {manifest_path}")
    manifest = _require_dict(_read_json(manifest_path), "source manifest")
    _require_keys(manifest, {"schema_version", "ticket", "confluence"}, "source manifest")
    if manifest["schema_version"] != "1.0":
        raise WorkflowContractError("source manifest schema_version must be '1.0'")

    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "sources").mkdir(parents=True, exist_ok=True)
    (raw_dir / "manifest.json").write_bytes(manifest_path.read_bytes())

    entries: list[dict[str, Any]] = []
    ticket = _require_dict(manifest["ticket"], "source manifest.ticket")
    if ticket.get("id") != ticket_id:
        raise WorkflowContractError("ticket id in source manifest does not match workflow input")
    all_entries = [ticket] + _require_list(manifest["confluence"], "source manifest.confluence")

    for index, entry_value in enumerate(all_entries):
        entry = _require_dict(entry_value, f"source manifest entry[{index}]")
        _require_keys(entry, {"source_id", "title", "file", "required"}, f"source manifest entry[{index}]")
        source_id = _require_str(entry["source_id"], f"source manifest entry[{index}].source_id")
        title = _require_str(entry["title"], f"source manifest entry[{index}].title")
        rel = Path(_require_str(entry["file"], f"source manifest entry[{index}].file"))
        required = _require_bool(entry["required"], f"source manifest entry[{index}].required")
        if rel.is_absolute() or ".." in rel.parts:
            raise WorkflowContractError(f"source file must stay inside source_dir: {rel}")
        source_path = source_dir / rel
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", source_id) + source_path.suffix
        copied_path = raw_dir / "sources" / safe_name
        if source_path.is_file():
            data = source_path.read_bytes()
            copied_path.write_bytes(data)
            status = "RETRIEVED"
            digest = _sha256_bytes(data)
            runtime_path: str | None = str(copied_path)
        else:
            status = "UNAVAILABLE"
            digest = None
            runtime_path = None
        entries.append({
            "source_id": source_id,
            "title": title,
            "type": "JIRA" if index == 0 else "CONFLUENCE",
            "required": required,
            "status": status,
            "content_digest": digest,
            "path": runtime_path,
            "original_relative_path": str(rel),
        })

    retrieval = {
        "schema_version": "1.0",
        "adapter": "local_fixture",
        "ticket_id": ticket_id,
        "sources": entries,
    }
    _write_json(raw_dir / "retrieval.json", retrieval)
    return retrieval


def retrieval_blockers(retrieval: dict[str, Any]) -> list[str]:
    return [
        f"required source unavailable: {item['source_id']}"
        for item in retrieval["sources"]
        if item["required"] and item["status"] == "UNAVAILABLE"
    ]


def build_normalizer_prompt(repo: Path, raw_dir: Path, schema: Path, contract: Path, previous: Path | None = None, review: Path | None = None) -> str:
    extras = ""
    if previous is not None:
        extras += f"\nPrevious normalized context: {previous}"
    if review is not None:
        extras += f"\nReviewer findings requiring context re-normalization: {review}"
    return f"""Normalize the retrieved Jira/Confluence package for Planning Workflow 1.

Repository root: {repo}
Raw retrieval manifest: {raw_dir / 'retrieval.json'}
Raw source directory: {raw_dir / 'sources'}
Planning Context JSON Schema: {schema}
Context Package contract: {contract}{extras}

Read the retrieval manifest first, then every available source listed there. Preserve unavailable required sources as blocking retrieval warnings. Return ONLY valid JSON conforming to the Planning Context schema. Do not inspect production code and do not propose an implementation."""


def build_analysis_prompt(repo: Path, context_json: Path, raw_dir: Path, baseline_sha: str, contract: Path, governance: Path) -> str:
    return f"""Analyse the repository against the validated Planning Context.

Repository root: {repo}
Repository baseline SHA: {baseline_sha}
Validated Planning Context: {context_json}
Raw source retrieval manifest (provenance checks only): {raw_dir / 'retrieval.json'}
Planning workflow contract: {contract}
Governance policy: {governance}

Read the supplied context and relevant repository files. Return only the Planning Analysis Markdown required by your profile. Do not design the final implementation plan and do not modify files."""


def build_author_prompt(repo: Path, ticket_id: str, context_json: Path, analysis_path: Path, template: Path, contract: Path, governance: Path, baseline_sha: str, base_branch: str, previous_plan: Path | None = None, review_path: Path | None = None) -> str:
    revision = ""
    if previous_plan is not None and review_path is not None:
        revision = f"""
This is a revision round.
Previous plan: {previous_plan}
Reviewer findings: {review_path}
Revise the full plan to address every PLAN_CHANGE_REQUIRED finding. Preserve valid prior decisions and do not alter the validated requirements."""
    return f"""Create the complete Development Plan for {ticket_id}.

Repository root: {repo}
Base branch: {base_branch}
Repository baseline SHA: {baseline_sha}
Validated Planning Context: {context_json}
Planning Analysis: {analysis_path}
Development Plan template: {template}
Planning workflow contract: {contract}
Governance policy: {governance}
{revision}

Return ONLY the complete Development Plan in Markdown. Do not modify repository files."""


def build_reviewer_prompt(repo: Path, context_json: Path, raw_dir: Path, analysis_path: Path, plan_path: Path, contract: Path, governance: Path, baseline_sha: str) -> str:
    return f"""Independently review the candidate Development Plan.

Repository root: {repo}
Repository baseline SHA: {baseline_sha}
Validated Planning Context: {context_json}
Raw source retrieval manifest: {raw_dir / 'retrieval.json'}
Raw source directory: {raw_dir / 'sources'}
Planning Analysis: {analysis_path}
Candidate Development Plan: {plan_path}
Planning workflow contract: {contract}
Governance policy: {governance}

Challenge the plan against the source provenance, context and repository evidence. Return ONLY the structured JSON required by your profile. Do not modify files and do not approve the plan."""


def _emit_human_needed(ticket_id: str, run_id: str, reason: str, blockers: list[str], runtime_dir: Path) -> None:
    emit_output({
        "workflow_outcome": "AWAITING_HUMAN_CLARIFICATION",
        "ticket_id": ticket_id,
        "run_id": run_id,
        "reason": reason,
        "blockers": blockers,
        "runtime_dir": str(runtime_dir),
    })


def main() -> None:
    inputs = get_inputs()
    ticket_id = _safe_component(_require_str(inputs["ticket_id"], "ticket_id"), "ticket_id")
    repo = Path(inputs["repository_root"]).resolve()
    source_dir = Path(inputs["source_dir"]).resolve()
    baseline_sha = _require_str(inputs["baseline_sha"], "baseline_sha")
    base_branch = _require_str(inputs.get("base_branch", "main"), "base_branch")
    max_review_rounds = inputs.get("max_review_rounds", 3)
    if isinstance(max_review_rounds, bool) or not isinstance(max_review_rounds, int) or not 1 <= max_review_rounds <= 10:
        raise WorkflowContractError("max_review_rounds must be an integer from 1 to 10")
    if not repo.is_dir():
        raise WorkflowContractError(f"repository_root is not a directory: {repo}")
    if not source_dir.is_dir():
        raise WorkflowContractError(f"source_dir is not a directory: {source_dir}")

    run_id = _safe_component(os.environ.get("CAO_WORKFLOW_RUN_ID", "unknown-run"), "CAO_WORKFLOW_RUN_ID")
    sdlc = repo / ".agentic-sdlc"
    runtime_dir = sdlc / "runtime" / ticket_id / run_id
    raw_dir = runtime_dir / "context" / "raw"
    normalized_dir = runtime_dir / "context" / "normalized"
    analysis_dir = runtime_dir / "analysis"
    planning_dir = runtime_dir / "planning"
    records_dir = sdlc / "records" / ticket_id

    schema = sdlc / "schemas" / "planning-context.schema.json"
    context_contract = sdlc / "contracts" / "context-package.md"
    planning_contract = sdlc / "contracts" / "planning-workflow.md"
    governance = sdlc / "policies" / "governance.md"
    plan_template = sdlc / "templates" / "development-plan.md"
    for required in (schema, context_contract, planning_contract, governance, plan_template):
        if not required.is_file():
            raise WorkflowContractError(f"required SDLC contract file is missing: {required}")

    approval_record = records_dir / "plan-approval-record.json"
    if approval_record.exists():
        approval = _read_json(approval_record)
        if isinstance(approval, dict) and approval.get("decision") == "APPROVED":
            raise WorkflowContractError(
                f"an APPROVED plan already exists for {ticket_id}; do not overwrite an approved planning record"
            )

    # 1. Deterministic retrieval adapter (fixture-based in v1).
    retrieval = retrieve_fixture_sources(source_dir, raw_dir, ticket_id)
    source_blockers = retrieval_blockers(retrieval)

    # 2. Agentic semantic normalization.
    context_version = 1
    normalizer = step(
        PROVIDER,
        CONTEXT_NORMALIZER,
        build_normalizer_prompt(repo, raw_dir, schema, context_contract),
        recovery="idempotent",
        step_id="context-normalize-v1",
        timeout=STEP_TIMEOUT_SECONDS,
        working_directory=str(repo),
    )
    context = _parse_json_output(normalizer.output, "Context Normalizer")
    context_json = normalized_dir / "planning-context-v1.json"
    _write_json(context_json, context)

    # 3. Deterministic structure + readiness validation.
    blockers = validate_planning_context(context, ticket_id) + source_blockers
    validation_path = normalized_dir / "validation-v1.json"
    _write_json(validation_path, {"valid": True, "ready": not blockers, "blockers": blockers})
    _write_text(normalized_dir / "planning-context-v1.md", render_planning_context(context))
    if blockers:
        _emit_human_needed(ticket_id, run_id, "context_not_ready", blockers, runtime_dir)
        return

    # 4. Repository analysis.
    analysis_handle = step(
        PROVIDER,
        PLANNING_ANALYST,
        build_analysis_prompt(repo, context_json, raw_dir, baseline_sha, planning_contract, governance),
        recovery="idempotent",
        step_id="planning-analysis-v1",
        timeout=STEP_TIMEOUT_SECONDS,
        working_directory=str(repo),
    )
    analysis_path = analysis_dir / "planning-analysis-v1.md"
    _write_text(analysis_path, analysis_handle.output)

    # 5. Initial Development Plan.
    plan_handle = step(
        PROVIDER,
        PLAN_AUTHOR,
        build_author_prompt(
            repo, ticket_id, context_json, analysis_path, plan_template,
            planning_contract, governance, baseline_sha, base_branch,
        ),
        recovery="idempotent",
        step_id="plan-author-r1-c1",
        timeout=STEP_TIMEOUT_SECONDS,
        working_directory=str(repo),
    )
    plan_path = planning_dir / "plan-r1.md"
    _write_text(plan_path, plan_handle.output)

    # 6. Independent review and bounded convergence.
    final_review: dict[str, Any] | None = None
    review_round = 1
    while review_round <= max_review_rounds:
        review_handle = step(
            PROVIDER,
            PLAN_REVIEWER,
            build_reviewer_prompt(
                repo, context_json, raw_dir, analysis_path, plan_path,
                planning_contract, governance, baseline_sha,
            ),
            recovery="idempotent",
            step_id=f"plan-review-r{review_round}-c{context_version}",
            timeout=STEP_TIMEOUT_SECONDS,
            working_directory=str(repo),
        )
        review = validate_review(_parse_json_output(review_handle.output, "Plan Reviewer"))
        review_path = planning_dir / f"review-r{review_round}-c{context_version}.json"
        _write_json(review_path, review)
        final_review = review
        status = review["review_status"]

        if status == "PASS":
            break

        if status == "HUMAN_DECISION_REQUIRED":
            blockers = [
                f"{f['id']}: {f['description']}"
                for f in review["findings"]
                if f["disposition"] == "HUMAN_DECISION_REQUIRED"
            ]
            _emit_human_needed(ticket_id, run_id, "plan_review_requires_human_decision", blockers, runtime_dir)
            return

        if review_round >= max_review_rounds:
            blockers = [f"{f['id']}: {f['description']}" for f in review["findings"]]
            _emit_human_needed(ticket_id, run_id, "review_convergence_limit_reached", blockers, runtime_dir)
            return

        if status == "CONTEXT_RENORMALIZATION_REQUIRED":
            context_version += 1
            renormalizer = step(
                PROVIDER,
                CONTEXT_NORMALIZER,
                build_normalizer_prompt(repo, raw_dir, schema, context_contract, context_json, review_path),
                recovery="idempotent",
                step_id=f"context-normalize-v{context_version}",
                timeout=STEP_TIMEOUT_SECONDS,
                working_directory=str(repo),
            )
            context = _parse_json_output(renormalizer.output, "Context Normalizer")
            context_json = normalized_dir / f"planning-context-v{context_version}.json"
            _write_json(context_json, context)
            blockers = validate_planning_context(context, ticket_id) + source_blockers
            _write_json(
                normalized_dir / f"validation-v{context_version}.json",
                {"valid": True, "ready": not blockers, "blockers": blockers},
            )
            _write_text(
                normalized_dir / f"planning-context-v{context_version}.md",
                render_planning_context(context),
            )
            if blockers:
                _emit_human_needed(ticket_id, run_id, "renormalized_context_not_ready", blockers, runtime_dir)
                return

            analysis_handle = step(
                PROVIDER,
                PLANNING_ANALYST,
                build_analysis_prompt(repo, context_json, raw_dir, baseline_sha, planning_contract, governance),
                recovery="idempotent",
                step_id=f"planning-analysis-v{context_version}",
                timeout=STEP_TIMEOUT_SECONDS,
                working_directory=str(repo),
            )
            analysis_path = analysis_dir / f"planning-analysis-v{context_version}.md"
            _write_text(analysis_path, analysis_handle.output)

        # CHANGES_REQUIRED and re-normalization both return to the Plan Author.
        next_round = review_round + 1
        plan_handle = step(
            PROVIDER,
            PLAN_AUTHOR,
            build_author_prompt(
                repo, ticket_id, context_json, analysis_path, plan_template,
                planning_contract, governance, baseline_sha, base_branch,
                previous_plan=plan_path, review_path=review_path,
            ),
            recovery="idempotent",
            step_id=f"plan-author-r{next_round}-c{context_version}",
            timeout=STEP_TIMEOUT_SECONDS,
            working_directory=str(repo),
        )
        plan_path = planning_dir / f"plan-r{next_round}-c{context_version}.md"
        _write_text(plan_path, plan_handle.output)
        review_round = next_round

    if final_review is None or final_review["review_status"] != "PASS":
        raise WorkflowContractError("planning loop ended without a passing independent review")

    # 7. Publish immutable candidate plan + durable execution manifest.
    records_dir.mkdir(parents=True, exist_ok=True)
    final_plan = records_dir / "development-plan.md"
    final_plan.write_bytes(plan_path.read_bytes())
    plan_sha = _sha256_file(final_plan)
    context_sha = _sha256_file(context_json)
    final_review_path = records_dir / "plan-review.json"
    review_record = {
        "schema_version": "1.0",
        "reviewed_plan_sha256": plan_sha,
        "review_round": review_round,
        "context_version": context_version,
        "review": final_review,
    }
    _write_json(final_review_path, review_record)
    execution_manifest = {
        "schema_version": "1.0",
        "ticket_id": ticket_id,
        "planning_workflow": "dev_plan",
        "workflow_run_id": run_id,
        "provider": PROVIDER,
        "repository_root": str(repo),
        "base_branch": base_branch,
        "repository_baseline_sha": baseline_sha,
        "planning_context_sha256": context_sha,
        "plan_path": str(final_plan.relative_to(repo)),
        "plan_sha256": plan_sha,
        "review_rounds": review_round,
        "context_versions": context_version,
        "state": "AWAITING_HUMAN_APPROVAL",
    }
    manifest_path = records_dir / "execution-manifest.json"
    _write_json(manifest_path, execution_manifest)

    emit_output({
        "workflow_outcome": "AWAITING_HUMAN_APPROVAL",
        "ticket_id": ticket_id,
        "run_id": run_id,
        "plan_path": str(final_plan),
        "plan_sha256": plan_sha,
        "execution_manifest": str(manifest_path),
        "review_rounds": review_round,
        "context_versions": context_version,
        "next_action": "Human reviews development-plan.md and records APPROVED or REJECTED with approve_plan.py.",
    })


if __name__ == "__main__":
    main()
