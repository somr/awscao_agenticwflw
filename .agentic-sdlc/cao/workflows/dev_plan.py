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
import threading
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cao_workflow import emit_output, get_inputs, step


INPUTS = {
    "ticket_id": {"type": "string", "required": True},
    "repository_root": {"type": "path", "required": True},
    "source_dir": {"type": "path", "required": True},
    "baseline_sha": {"type": "string", "required": True},
    "base_branch": {"type": "string", "required": False, "default": "main"},
    "max_review_rounds": {"type": "int", "required": False, "default": 3},
    # "local_fixture" (default) reads pre-exported files named by
    # source_dir/context.json, exactly as this workflow always has -
    # deterministic and network-free, so it stays the default for tests and
    # any environment without live Jira/Confluence access. "jira_confluence_live"
    # fetches the same manifest's entries from real Jira/Confluence APIs; see
    # retrieve_live_sources() below and JIRA_BASE_URL_ENV/CONFLUENCE_BASE_URL_ENV
    # for the credentials it requires. Both adapters produce an identical
    # retrieval.json/raw_dir["sources"] contract, so nothing downstream
    # (context-normalizer, validate_planning_context, etc.) needs to know or
    # care which one ran.
    "source_adapter": {"type": "string", "required": False, "default": "local_fixture"},
}

PROVIDER = "claude_code"
CONTEXT_NORMALIZER = "sdlc_context_normalizer"
PLANNING_ANALYST = "sdlc_planning_analyst"
PLAN_AUTHOR = "sdlc_plan_author"
PLAN_REVIEWER = "sdlc_plan_reviewer"
STEP_TIMEOUT_SECONDS = 1800
CAO_HTTP_TIMEOUT_SECONDS = 30.0
COMPLETION_INITIAL_SETTLE_SECONDS = 5.0
COMPLETION_POLL_SECONDS = 3.0
COMPLETION_MAX_POLLS = 100
COMPLETION_STABLE_POLLS = 2

# --- Step completion & answer delivery --------------------------------------
#
# SECURITY DESIGN — full writeup in .agentic-sdlc/cao/workflows/README.md
# under "Answer file delivery & the write-scope hook". Summary: agent
# profiles are granted fs_write (Claude Code's Edit/Write/NotebookEdit tools)
# so each step saves its JSON answer to a file instead of relying on
# terminal-text extraction (see below for why that was unreliable). CAO's own
# allowedTools enforcement is tool-name-level only, not path-scoped, and CAO
# always launches these workers with `--dangerously-skip-permissions`, which
# also bypasses Claude Code's own path-scoped permissions.allow/deny rules.
# The actual, non-bypassable restriction is a PreToolUse hook (matcher
# "Write|Edit|NotebookEdit") in the project's .claude/settings.json, which
# denies any write outside .agentic-sdlc/runtime/** — the only place these
# agents are ever asked to write. This specifically matters because these
# profiles read untrusted external content (Jira/Confluence text) that a
# prompt-injection payload could hide in. Do not remove or narrow that hook
# without keeping an equivalent restriction in place.
#
# CAO 2.5.0's Claude Code run-step path can also transiently report COMPLETED
# while the Claude Ink TUI is still processing, so a step's worker terminal is
# kept alive (teardown=False) and polled rather than torn down immediately.
#
# An earlier version of this workflow tried to detect step completion by
# parsing the terminal's own screen text (CAO's mode=last/mode=full output).
# That approach was abandoned as fundamentally unreliable:
#   - Claude Code's post-completion "next prompt" ghost-text suggestion left
#     stray text in the idle input box that CAO's mode=last misread as an
#     unanswered new turn.
#   - CAO's mode=last flags ANY idle prompt (ghost text or not) as
#     "[NO RESPONSE - ...]", even immediately after a real, complete answer —
#     confirmed reproducible even with the ghost-text feature turned off.
#   - mode=full is not rendered text — it is the raw PTY byte stream, and
#     Claude Code's TUI redraws the screen using cursor-addressing escape
#     codes (e.g. "\x1b[19G" to jump to column 19), so stripping escape codes
#     while keeping byte order produces garbled, reordered text (observed
#     live: "source_id" came back as "ource_id"). Correctly reconstructing
#     the screen would require a real terminal emulator.
# Polling the CAO-reported terminal *status* (not its screen text) plus a
# file the agent itself writes sidesteps all of this.

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


class IncompleteAgentExecutionError(WorkflowContractError):
    """Raised when CAO returns before the agent has produced a final response."""


def _wait(seconds: float) -> None:
    """Sleep without importing the workflow-linter's nondeterministic `time` module."""
    threading.Event().wait(seconds)


def _cao_base_url() -> str:
    value = os.environ.get("CAO_API_BASE_URL", "").strip().rstrip("/")
    if not value:
        raise WorkflowContractError(
            "CAO_API_BASE_URL is unavailable; stabilized workflow steps must run under `cao workflow run`"
        )
    return value


def _cao_json_request(
    path: str,
    *,
    method: str = "GET",
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{_cao_base_url()}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    data = None if method == "GET" else b""
    request = Request(url, data=data, method=method, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=CAO_HTTP_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise WorkflowContractError(
            f"CAO API {method} {path} failed with HTTP {exc.code}: {body[:500]}"
        ) from exc
    except URLError as exc:
        raise WorkflowContractError(f"CAO API {method} {path} failed: {exc}") from exc
    if not payload.strip():
        return {}
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WorkflowContractError(
            f"CAO API {method} {path} returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise WorkflowContractError(f"CAO API {method} {path} returned a non-object JSON payload")
    return value


def _cao_terminal_status(terminal_id: str) -> str:
    terminal = _cao_json_request(f"/terminals/{terminal_id}")
    return str(terminal.get("status", "unknown")).lower()


def _cleanup_step_terminal(terminal_id: str, evidence_dir: Path, step_id: str) -> None:
    """Best-effort graceful exit + delete for a teardown=False workflow worker."""
    errors: list[str] = []
    try:
        _cao_json_request(f"/terminals/{terminal_id}/exit", method="POST")
    except WorkflowContractError as exc:
        errors.append(f"exit: {exc}")
    _wait(0.25)
    try:
        _cao_json_request(f"/terminals/{terminal_id}", method="DELETE")
    except WorkflowContractError as exc:
        errors.append(f"delete: {exc}")
    if errors:
        _write_text(evidence_dir / f"{step_id}.cleanup-warning.txt", "\n".join(errors))


def _answer_file_delivery_instructions(answer_path: Path) -> str:
    return f"""## OUTPUT DELIVERY

Use your file-write tool to save your complete final answer to exactly this path (create parent directories if needed; overwrite the file if it already exists):
{answer_path}

Do not print the answer in your chat reply. After writing the file, reply with a short one-line confirmation only (e.g. "Written."). You are permitted to write to this one file for this purpose only."""


def _wait_for_answer_file(
    *,
    terminal_id: str,
    answer_path: Path,
    step_id: str,
    evidence_dir: Path,
) -> str:
    """Wait for the agent to write its answer file and for its content to settle.

    CAO's own Claude E2E tests re-check completion after a delay because the
    TUI can transiently report COMPLETED, so this still polls terminal
    *status* (not its screen text — see the module-level comment above this
    function's neighborhood for why that was abandoned) alongside the file.
    """
    evidence_dir.mkdir(parents=True, exist_ok=True)

    polls: list[dict[str, Any]] = []
    previous_content: str | None = None
    stable_polls = 0
    status = "unknown"

    _wait(COMPLETION_INITIAL_SETTLE_SECONDS)
    for poll_no in range(1, COMPLETION_MAX_POLLS + 1):
        status = _cao_terminal_status(terminal_id)
        content = answer_path.read_text(encoding="utf-8") if answer_path.is_file() else None
        polls.append({
            "poll": poll_no,
            "status": status,
            "answer_file_exists": content is not None,
            "answer_length": len(content) if content is not None else None,
            "answer_sha256": _sha256_bytes(content.encode("utf-8")) if content is not None else None,
        })

        if status == "error":
            raise IncompleteAgentExecutionError(
                f"{step_id} terminal entered ERROR while waiting for {answer_path.name}"
            )
        if status == "waiting_user_answer":
            raise IncompleteAgentExecutionError(
                f"{step_id} requires an interactive human answer; headless planning agents must not block on prompts"
            )

        if content is not None:
            if content == previous_content:
                stable_polls += 1
            else:
                previous_content = content
                stable_polls = 1
            if stable_polls >= COMPLETION_STABLE_POLLS:
                _write_json(
                    evidence_dir / f"{step_id}.stabilization.json",
                    {
                        "stabilized": True,
                        "required_stable_polls": COMPLETION_STABLE_POLLS,
                        "polls": polls,
                    },
                )
                return content
        else:
            previous_content = None
            stable_polls = 0

        _wait(COMPLETION_POLL_SECONDS)

    _write_json(
        evidence_dir / f"{step_id}.stabilization.json",
        {
            "stabilized": False,
            "required_stable_polls": COMPLETION_STABLE_POLLS,
            "polls": polls,
        },
    )
    raise IncompleteAgentExecutionError(
        f"{step_id} did not write a stable {answer_path.name} after "
        f"{COMPLETION_INITIAL_SETTLE_SECONDS + COMPLETION_MAX_POLLS * COMPLETION_POLL_SECONDS:.0f}s "
        f"(last CAO status: {status})"
    )


def _run_stabilized_step(
    *,
    agent: str,
    prompt: str,
    step_id: str,
    repo: Path,
    evidence_dir: Path,
    answer_path: Path,
) -> str:
    """Run one CAO step without teardown, then wait for its answer file and clean up."""
    handle = step(
        PROVIDER,
        agent,
        prompt,
        recovery="idempotent",
        step_id=step_id,
        timeout=STEP_TIMEOUT_SECONDS,
        working_directory=str(repo),
        teardown=False,
    )

    # A replayed handle names a terminal that no longer exists, so there is no
    # terminal left to poll. Accept only an answer file already on disk from
    # the earlier attempt; an incomplete replay requires a new run (or an
    # explicit CAO recovery decision), not polling a dead terminal id.
    if handle.replayed:
        if answer_path.is_file():
            return answer_path.read_text(encoding="utf-8")
        raise IncompleteAgentExecutionError(
            f"{step_id} replayed a terminal with no {answer_path.name} on disk; "
            "start a fresh run or rerun this step explicitly"
        )

    try:
        return _wait_for_answer_file(
            terminal_id=handle.terminal_id,
            answer_path=answer_path,
            step_id=step_id,
            evidence_dir=evidence_dir,
        )
    finally:
        _cleanup_step_terminal(handle.terminal_id, evidence_dir, step_id)


def _run_delivered_step(
    *,
    agent: str,
    prompt: str,
    step_id: str,
    repo: Path,
    evidence_dir: Path,
    answer_suffix: str = ".answer.json",
) -> str:
    """Run one stabilized step, appending file-delivery instructions to its prompt.

    Every agent step in this workflow delivers its answer as a file (see the
    security-design comment above _wait_for_answer_file); this is the single
    place that appends the delivery instructions and computes the answer
    path, so every call site — JSON-contract steps and free-form Markdown
    steps alike — stays consistent.
    """
    answer_path = evidence_dir / f"{step_id}{answer_suffix}"
    return _run_stabilized_step(
        agent=agent,
        prompt=f"{prompt}\n\n{_answer_file_delivery_instructions(answer_path)}",
        step_id=step_id,
        repo=repo,
        evidence_dir=evidence_dir,
        answer_path=answer_path,
    )


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


def _run_json_contract_step(
    *,
    agent: str,
    prompt: str,
    label: str,
    step_id: str,
    repo: Path,
    evidence_dir: Path,
    validator=None,
    max_repairs: int = 1,
) -> Any:
    """Run a stabilized LLM step that must return machine-readable JSON.

    Execution completeness is established *before* JSON parsing. Live TUI or
    transport-truncated output raises IncompleteAgentExecutionError and never
    enters the JSON-repair path. Once a stable final response exists, one
    bounded contract-repair turn is allowed for genuine JSON/shape defects.
    """
    current_prompt = prompt
    previous_output = ""
    last_error: WorkflowContractError | None = None

    for attempt in range(max_repairs + 1):
        current_step_id = step_id if attempt == 0 else f"{step_id}-repair-{attempt}"
        # current_step_id (not step_id) gives each attempt a fresh,
        # attempt-unique answer path, so a repair attempt never reads a stale
        # file left over from the earlier attempt before it has written its
        # own answer.
        output = _run_delivered_step(
            agent=agent,
            prompt=current_prompt,
            step_id=current_step_id,
            repo=repo,
            evidence_dir=evidence_dir,
        )

        evidence_dir.mkdir(parents=True, exist_ok=True)
        _write_text(evidence_dir / f"{current_step_id}.raw.txt", output)

        try:
            value = _parse_json_output(output, label)
            if validator is not None:
                value = validator(value)
            return value
        except WorkflowContractError as exc:
            last_error = exc
            previous_output = output
            if attempt >= max_repairs:
                break

            current_prompt = f"""{prompt}

## CONTRACT REPAIR REQUIRED

The previous response completed normally but failed the deterministic JSON contract.
Repair the response and return the COMPLETE corrected JSON document.

Validation error:
{exc}

Previous response:
<previous_response>
{previous_output}
</previous_response>

Rules for this repair:
- Preserve the meaning and provenance of the source material.
- Do not invent requirements, evidence, defaults, thresholds or decisions.
- Correct JSON syntax and/or the reported contract-shape problem only.
- Return strict RFC 8259 JSON only.
- Use double quotes for every object key and string value.
- Do not use comments, trailing commas, single-quoted strings, NaN, Infinity, ellipses or Markdown fences.
- Return no prose before or after the JSON.
"""

    assert last_error is not None
    raise WorkflowContractError(
        f"{label} failed its JSON contract after {max_repairs + 1} attempts: {last_error}"
    ) from last_error


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


# --- Live Jira/Confluence adapter --------------------------------------
#
# Reads the SAME manifest shape retrieve_fixture_sources() does (a "ticket"
# entry plus a "confluence" list, each with source_id/title/required), except
# each entry names a remote id (ticket.jira_key / confluence[].page_id)
# instead of a local file path. Both adapters write an identical
# retrieval.json/raw_dir["sources"] shape, so this is a drop-in swap
# selected by the source_adapter workflow input - see SOURCE_ADAPTERS below.
#
# Credentials are read from environment variables, never from the manifest
# itself, so a per-ticket manifest can be safely committed to records/
# without leaking a token. There is no live Jira/Confluence instance to test
# this against in this environment, so it is covered by unit tests against a
# fake local HTTP server (tests/test_dev_plan.py) rather than a live run -
# same technique already used for .claude/hooks/restrict-write-scope.py's
# CAO terminal-metadata calls. Treat this adapter as unverified against a
# real Atlassian tenant until it has been.

JIRA_BASE_URL_ENV = "JIRA_BASE_URL"
JIRA_API_TOKEN_ENV = "JIRA_API_TOKEN"
CONFLUENCE_BASE_URL_ENV = "CONFLUENCE_BASE_URL"
CONFLUENCE_API_TOKEN_ENV = "CONFLUENCE_API_TOKEN"
LIVE_HTTP_TIMEOUT_SECONDS = 15.0


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise WorkflowContractError(
            f"missing required environment variable: {name} (needed by the jira_confluence_live source adapter)"
        )
    return value


def _adf_to_text(node: Any) -> str:
    """Best-effort flattening of an Atlassian Document Format node (Jira
    v3's `fields.description` shape) to plain text. Walks `content`
    children and joins `text` nodes; not a full ADF renderer - tables,
    panels, and inline mentions/emoji collapse to whatever plain text they
    carry, nothing fancier. Good enough for an LLM reader, not for display."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text", ""))
    children = node.get("content", []) or []
    joined = "".join(_adf_to_text(child) for child in children)
    if node.get("type") in {"paragraph", "heading", "listItem", "codeBlock"}:
        return joined + "\n\n"
    return joined


class _StorageFormatTextExtractor(HTMLParser):
    """Strips Confluence storage-format XHTML down to plain text. Not a
    full HTML-to-markdown converter (no stdlib dependency for that exists,
    and this repo stays stdlib-only) - tables/macros/panels collapse to
    their visible text only, losing structure but preserving content."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def _confluence_storage_to_text(storage_html: str) -> str:
    parser = _StorageFormatTextExtractor()
    parser.feed(storage_html)
    return parser.text()


def _live_http_get_json(url: str, *, token: str) -> dict[str, Any]:
    request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=LIVE_HTTP_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise WorkflowContractError(f"GET {url} failed with HTTP {exc.code}: {body[:500]}") from exc
    except URLError as exc:
        raise WorkflowContractError(f"GET {url} failed: {exc}") from exc
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WorkflowContractError(f"GET {url} returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowContractError(f"GET {url} returned a non-object JSON payload")
    return value


def _fetch_jira_ticket(
    *, base_url: str, token: str, jira_key: str, source_id: str, title: str, raw_dir: Path
) -> dict[str, Any]:
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", source_id) + ".md"
    copied_path = raw_dir / "sources" / safe_name
    try:
        payload = _live_http_get_json(
            f"{base_url}/rest/api/3/issue/{jira_key}", token=token,
        )
        fields = _require_dict(payload.get("fields", {}), f"Jira {jira_key}.fields")
        summary = str(fields.get("summary", ""))
        description = _adf_to_text(fields.get("description")).strip()
        text = f"# {summary}\n\n{description}\n"
        data = text.encode("utf-8")
        copied_path.write_bytes(data)
        status = "RETRIEVED"
        digest: str | None = _sha256_bytes(data)
        runtime_path: str | None = str(copied_path)
    except WorkflowContractError:
        status = "UNAVAILABLE"
        digest = None
        runtime_path = None
    return {
        "source_id": source_id,
        "title": title,
        "type": "JIRA",
        "required": True,
        "status": status,
        "content_digest": digest,
        "path": runtime_path,
        "original_relative_path": f"jira:{jira_key}",
    }


def _fetch_confluence_page(
    *, base_url: str, token: str, page_id: str, source_id: str, title: str, required: bool, raw_dir: Path
) -> dict[str, Any]:
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", source_id) + ".md"
    copied_path = raw_dir / "sources" / safe_name
    try:
        payload = _live_http_get_json(
            f"{base_url}/wiki/rest/api/content/{page_id}?expand=body.storage", token=token,
        )
        page_title = str(payload.get("title", title))
        body = _require_dict(payload.get("body", {}), f"Confluence {page_id}.body")
        storage = _require_dict(body.get("storage", {}), f"Confluence {page_id}.body.storage")
        text = _confluence_storage_to_text(str(storage.get("value", ""))).strip()
        data = f"# {page_title}\n\n{text}\n".encode("utf-8")
        copied_path.write_bytes(data)
        status = "RETRIEVED"
        digest: str | None = _sha256_bytes(data)
        runtime_path: str | None = str(copied_path)
    except WorkflowContractError:
        status = "UNAVAILABLE"
        digest = None
        runtime_path = None
    return {
        "source_id": source_id,
        "title": title,
        "type": "CONFLUENCE",
        "required": required,
        "status": status,
        "content_digest": digest,
        "path": runtime_path,
        "original_relative_path": f"confluence:{page_id}",
    }


def retrieve_live_sources(source_dir: Path, raw_dir: Path, ticket_id: str) -> dict[str, Any]:
    """Live adapter: fetches the manifest's ticket from Jira and each
    confluence[] entry from Confluence by page_id, over real HTTP. See the
    module comment above this section for the manifest shape and the
    stdlib-only text-extraction caveats."""
    manifest_path = source_dir / "context.json"
    if not manifest_path.is_file():
        raise WorkflowContractError(f"source manifest not found: {manifest_path}")
    manifest = _require_dict(_read_json(manifest_path), "source manifest")
    _require_keys(manifest, {"schema_version", "ticket", "confluence"}, "source manifest")
    if manifest["schema_version"] != "1.0":
        raise WorkflowContractError("source manifest schema_version must be '1.0'")

    ticket = _require_dict(manifest["ticket"], "source manifest.ticket")
    if ticket.get("id") != ticket_id:
        raise WorkflowContractError("ticket id in source manifest does not match workflow input")
    jira_key = _require_str(ticket.get("jira_key", ""), "source manifest.ticket.jira_key")
    ticket_source_id = _require_str(ticket.get("source_id", f"JIRA:{jira_key}"), "source manifest.ticket.source_id")
    ticket_title = _require_str(ticket.get("title", jira_key), "source manifest.ticket.title")

    jira_base_url = _require_env(JIRA_BASE_URL_ENV).rstrip("/")
    jira_token = _require_env(JIRA_API_TOKEN_ENV)
    confluence_base_url = _require_env(CONFLUENCE_BASE_URL_ENV).rstrip("/")
    confluence_token = _require_env(CONFLUENCE_API_TOKEN_ENV)

    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "sources").mkdir(parents=True, exist_ok=True)
    (raw_dir / "manifest.json").write_bytes(manifest_path.read_bytes())

    entries: list[dict[str, Any]] = [
        _fetch_jira_ticket(
            base_url=jira_base_url, token=jira_token, jira_key=jira_key,
            source_id=ticket_source_id, title=ticket_title, raw_dir=raw_dir,
        )
    ]

    confluence_entries = _require_list(manifest["confluence"], "source manifest.confluence")
    for index, entry_value in enumerate(confluence_entries):
        entry = _require_dict(entry_value, f"source manifest.confluence[{index}]")
        _require_keys(entry, {"source_id", "title", "page_id", "required"}, f"source manifest.confluence[{index}]")
        entries.append(_fetch_confluence_page(
            base_url=confluence_base_url, token=confluence_token,
            page_id=_require_str(entry["page_id"], f"source manifest.confluence[{index}].page_id"),
            source_id=_require_str(entry["source_id"], f"source manifest.confluence[{index}].source_id"),
            title=_require_str(entry["title"], f"source manifest.confluence[{index}].title"),
            required=_require_bool(entry["required"], f"source manifest.confluence[{index}].required"),
            raw_dir=raw_dir,
        ))

    retrieval = {
        "schema_version": "1.0",
        "adapter": "jira_confluence_live",
        "ticket_id": ticket_id,
        "sources": entries,
    }
    _write_json(raw_dir / "retrieval.json", retrieval)
    return retrieval


SOURCE_ADAPTERS = {
    "local_fixture": retrieve_fixture_sources,
    "jira_confluence_live": retrieve_live_sources,
}


def retrieve_sources(adapter: str, source_dir: Path, raw_dir: Path, ticket_id: str) -> dict[str, Any]:
    try:
        adapter_fn = SOURCE_ADAPTERS[adapter]
    except KeyError:
        raise WorkflowContractError(
            f"unknown source_adapter {adapter!r}; expected one of {sorted(SOURCE_ADAPTERS)}"
        ) from None
    return adapter_fn(source_dir, raw_dir, ticket_id)


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
    source_adapter = _require_str(inputs.get("source_adapter", "local_fixture"), "source_adapter")
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

    # 1. Deterministic retrieval adapter, selected by the source_adapter
    # input (default local_fixture; jira_confluence_live for production).
    retrieval = retrieve_sources(source_adapter, source_dir, raw_dir, ticket_id)
    source_blockers = retrieval_blockers(retrieval)

    # 2. Agentic semantic normalization.
    context_version = 1
    def _context_contract_validator(value: Any) -> Any:
        # validate_planning_context raises on structural/contract defects and
        # returns semantic readiness blockers separately. Only the former are
        # repaired by the LLM boundary helper.
        validate_planning_context(value, ticket_id)
        return value

    context = _run_json_contract_step(
        agent=CONTEXT_NORMALIZER,
        prompt=build_normalizer_prompt(repo, raw_dir, schema, context_contract),
        label="Context Normalizer",
        step_id="context-normalize-v1",
        repo=repo,
        evidence_dir=normalized_dir / "agent-output",
        validator=_context_contract_validator,
    )
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
    analysis_output = _run_delivered_step(
        agent=PLANNING_ANALYST,
        prompt=build_analysis_prompt(repo, context_json, raw_dir, baseline_sha, planning_contract, governance),
        step_id="planning-analysis-v1",
        repo=repo,
        evidence_dir=analysis_dir / "agent-output",
        answer_suffix=".answer.md",
    )
    analysis_path = analysis_dir / "planning-analysis-v1.md"
    _write_text(analysis_path, analysis_output)

    # 5. Initial Development Plan.
    plan_output = _run_delivered_step(
        agent=PLAN_AUTHOR,
        prompt=build_author_prompt(
            repo, ticket_id, context_json, analysis_path, plan_template,
            planning_contract, governance, baseline_sha, base_branch,
        ),
        step_id="plan-author-r1-c1",
        repo=repo,
        evidence_dir=planning_dir / "agent-output",
        answer_suffix=".answer.md",
    )
    plan_path = planning_dir / "plan-r1.md"
    _write_text(plan_path, plan_output)

    # 6. Independent review and bounded convergence.
    final_review: dict[str, Any] | None = None
    review_round = 1
    while review_round <= max_review_rounds:
        review = _run_json_contract_step(
            agent=PLAN_REVIEWER,
            prompt=build_reviewer_prompt(
                repo, context_json, raw_dir, analysis_path, plan_path,
                planning_contract, governance, baseline_sha,
            ),
            label="Plan Reviewer",
            step_id=f"plan-review-r{review_round}-c{context_version}",
            repo=repo,
            evidence_dir=planning_dir / "agent-output",
            validator=validate_review,
        )
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
            context = _run_json_contract_step(
                agent=CONTEXT_NORMALIZER,
                prompt=build_normalizer_prompt(
                    repo, raw_dir, schema, context_contract, context_json, review_path
                ),
                label="Context Normalizer",
                step_id=f"context-normalize-v{context_version}",
                repo=repo,
                evidence_dir=normalized_dir / "agent-output",
                validator=_context_contract_validator,
            )
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

            analysis_output = _run_delivered_step(
                agent=PLANNING_ANALYST,
                prompt=build_analysis_prompt(repo, context_json, raw_dir, baseline_sha, planning_contract, governance),
                step_id=f"planning-analysis-v{context_version}",
                repo=repo,
                evidence_dir=analysis_dir / "agent-output",
                answer_suffix=".answer.md",
            )
            analysis_path = analysis_dir / f"planning-analysis-v{context_version}.md"
            _write_text(analysis_path, analysis_output)

        # CHANGES_REQUIRED and re-normalization both return to the Plan Author.
        next_round = review_round + 1
        plan_output = _run_delivered_step(
            agent=PLAN_AUTHOR,
            prompt=build_author_prompt(
                repo, ticket_id, context_json, analysis_path, plan_template,
                planning_contract, governance, baseline_sha, base_branch,
                previous_plan=plan_path, review_path=review_path,
            ),
            step_id=f"plan-author-r{next_round}-c{context_version}",
            repo=repo,
            evidence_dir=planning_dir / "agent-output",
            answer_suffix=".answer.md",
        )
        plan_path = planning_dir / f"plan-r{next_round}-c{context_version}.md"
        _write_text(plan_path, plan_output)
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
        "planning_workflow": "sdlc_dev_plan",
        "planning_workflow_version": "1.3",
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
