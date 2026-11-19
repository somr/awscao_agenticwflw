"""CAO agent lifecycle, stable answer delivery and bounded JSON repair.

Review profiles need fs_write for answer delivery. The workflow's trusted hook
must restrict writes; provider permission rules alone are insufficient. See
agentic-sdlc-docs/reference/write-scope-hook.md.

Terminal screen text is not a reliable completion signal. Keep each worker alive,
poll its status and answer file, then clean up only that worker's terminal.
"""
from __future__ import annotations
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from cao_workflow import step
from .errors import WorkflowContractError, IncompleteAgentExecutionError
from .artifacts import _write_json, _write_text, _sha256_bytes
from .agent_settings import DEFAULT_PROVIDER, load_agent_settings, step_options
from .source_config import PROJECT_RELATIVE_PATH

# The provider when a project declares none; per-profile choices come from agent_settings.
PROVIDER = DEFAULT_PROVIDER
# The project's resolved agent_settings for this run (one workflow per process), set once before the first agent
# so retries, repair turns and parallel calls all use the same values; None means "as before".
_AGENT_SETTINGS: dict[str, dict[str, Any]] | None = None
# SHA-256 of the project file the settings were read from (None without one), for the run's records.
_AGENT_SETTINGS_FILE_SHA256: str | None = None
# Upper bound for read-only agent steps run at the same time (reviewers); shares CAO server capacity.
MAX_PARALLEL_REVIEWS = 4
STEP_TIMEOUT_SECONDS = 1800
CAO_HTTP_TIMEOUT_SECONDS = 30.0
COMPLETION_INITIAL_SETTLE_SECONDS = 5.0
COMPLETION_POLL_SECONDS = 3.0
# CAO can report a Claude Code terminal COMPLETED (or IDLE) while the agent is still
# working, so this wait, not CAO's, is the real budget for the rest of a long step.
# It keeps going while the agent shows activity (its streamed output or the answer
# file changes, or CAO reports processing) and gives up after
# COMPLETION_INACTIVITY_SECONDS without any, never later than the step's own budget.
# Evidence: agentic-sdlc-docs/verification/activity-wait-spike.md.
COMPLETION_MAX_POLLS = int(STEP_TIMEOUT_SECONDS / COMPLETION_POLL_SECONDS)
COMPLETION_INACTIVITY_SECONDS = 300.0
COMPLETION_INACTIVITY_POLLS = int(COMPLETION_INACTIVITY_SECONDS / COMPLETION_POLL_SECONDS)
COMPLETION_STABLE_POLLS = 2


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


def _cao_terminal_output_digest(terminal_id: str) -> str | None:
    """SHA-256 of the terminal's streamed output, or None when it cannot be read.

    Used only as a sign of activity (the Claude Code TUI redraws while it works and
    is still when idle), never to decide that a step is complete or to read results.
    """
    try:
        output = _cao_json_request(f"/terminals/{terminal_id}/output").get("output", "")
    except WorkflowContractError:
        return None
    return _sha256_bytes(str(output).encode("utf-8"))


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

    Completion is only ever the answer file staying identical for
    COMPLETION_STABLE_POLLS polls. Terminal status and streamed output are used
    only to decide whether it is too early to give up: CAO's status can read
    COMPLETED or IDLE while Claude Code is still working, so each poll counts as
    activity when the output or the answer changed or the status is processing,
    and the wait fails after COMPLETION_INACTIVITY_POLLS polls without any.
    """
    evidence_dir.mkdir(parents=True, exist_ok=True)

    polls: list[dict[str, Any]] = []
    previous_content: str | None = None
    stable_polls = 0
    status = "unknown"
    previous_output: str | None = None
    quiet_polls = 0

    _wait(COMPLETION_INITIAL_SETTLE_SECONDS)
    for poll_no in range(1, COMPLETION_MAX_POLLS + 1):
        status = _cao_terminal_status(terminal_id)
        output = _cao_terminal_output_digest(terminal_id)
        content = answer_path.read_text(encoding="utf-8") if answer_path.is_file() else None
        activity = []
        if output is not None and previous_output is not None and output != previous_output:
            activity.append("output")
        if content is not None and content != previous_content:
            activity.append("answer")
        if status == "processing":
            activity.append("processing")
        if output is not None:
            previous_output = output
        quiet_polls = 0 if activity else quiet_polls + 1
        polls.append({
            "poll": poll_no,
            "status": status,
            "output_sha256": output,
            "activity": activity,
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
                f"{step_id} requires an interactive human answer; headless workflow agents must not block on prompts"
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

        if quiet_polls >= COMPLETION_INACTIVITY_POLLS:
            _write_json(
                evidence_dir / f"{step_id}.stabilization.json",
                {
                    "stabilized": False,
                    "stopped": "inactive",
                    "required_stable_polls": COMPLETION_STABLE_POLLS,
                    "inactivity_polls": COMPLETION_INACTIVITY_POLLS,
                    "polls": polls,
                },
            )
            raise IncompleteAgentExecutionError(
                f"{step_id} showed no activity for {COMPLETION_INACTIVITY_SECONDS:.0f}s and did not write a "
                f"stable {answer_path.name} (last CAO status: {status})"
            )

        _wait(COMPLETION_POLL_SECONDS)

    _write_json(
        evidence_dir / f"{step_id}.stabilization.json",
        {
            "stabilized": False,
            "stopped": "step_budget",
            "required_stable_polls": COMPLETION_STABLE_POLLS,
            "polls": polls,
        },
    )
    raise IncompleteAgentExecutionError(
        f"{step_id} did not write a stable {answer_path.name} after "
        f"{COMPLETION_INITIAL_SETTLE_SECONDS + COMPLETION_MAX_POLLS * COMPLETION_POLL_SECONDS:.0f}s "
        f"(last CAO status: {status})"
    )


def installed_library_manifest() -> dict[str, str] | None:
    """The manifest of the frozen library this code runs from, or None when it runs from the repository.

    An installed library is <id>/manifest.json beside <id>/sdlc_workflows/ (build_library.py); the entry script has
    already checked every file against it before importing this module.
    """
    path = Path(__file__).resolve().parents[1] / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def set_agent_settings(resolved: dict[str, dict[str, Any]] | None) -> None:
    """Fix the provider and model of every profile for the rest of this run."""
    global _AGENT_SETTINGS
    _AGENT_SETTINGS = resolved


def configure_agent_settings(repo: Path) -> dict[str, dict[str, Any]]:
    """Read agent_settings from the trusted checkout, validate it and use it for this run's agent calls.

    Call it before the first agent; an invalid setting stops the run there.
    """
    global _AGENT_SETTINGS_FILE_SHA256
    resolved = load_agent_settings(repo)
    set_agent_settings(resolved)
    project_file = repo / PROJECT_RELATIVE_PATH
    _AGENT_SETTINGS_FILE_SHA256 = _sha256_bytes(project_file.read_bytes()) if project_file.is_file() else None
    return resolved


def agent_settings_record() -> dict[str, Any]:
    """The run's agent settings for its records: only profiles with a provider or model set, and the file's hash.

    A profile that is not listed ran on the default provider with the provider's own default model.
    """
    profiles = {name: dict(entry) for name, entry in sorted((_AGENT_SETTINGS or {}).items())
                if entry["provider_from"] != "unset" or entry["model_from"] != "unset"}
    return {"default_provider": PROVIDER, "profiles": profiles, "project_file_sha256": _AGENT_SETTINGS_FILE_SHA256}


def agent_settings_lines(profiles: dict[str, dict[str, Any]]) -> list[str]:
    """One readable line per profile with settings, for briefs and notes."""
    return [f"`{name}`: provider `{entry['provider']}` ({entry['provider_from']}), "
            f"model `{entry['model'] or 'provider default'}` ({entry['model_from']})"
            for name, entry in sorted(profiles.items())]


def _agent_settings_evidence(agent: str) -> dict[str, Any]:
    entry = (_AGENT_SETTINGS or {}).get(agent) or {
        "provider": PROVIDER, "provider_from": "unset", "model": None, "model_from": "unset"}
    return {"profile": agent, **entry}


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
    provider, options = step_options(_AGENT_SETTINGS, agent)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    _write_json(evidence_dir / f"{step_id}.agent-settings.json", _agent_settings_evidence(agent))
    handle = step(
        provider,
        agent,
        prompt,
        recovery="idempotent",
        step_id=step_id,
        timeout=STEP_TIMEOUT_SECONDS,
        working_directory=str(repo),
        teardown=False,
        **options,
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
    preserve_source: bool = True,
    final_validator=None,
) -> Any:
    """Run a stabilized LLM step that must return machine-readable JSON.

    Execution completeness is established *before* JSON parsing. Live TUI or
    transport-truncated output raises IncompleteAgentExecutionError and never
    enters the JSON-repair path. Once a stable final response exists, one
    bounded contract-repair turn is allowed for genuine JSON/shape defects.
    final_validator, when given, replaces validator on the last attempt (a more lenient check that
    records what is still missing instead of failing the step).
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
            check = final_validator if final_validator is not None and attempt == max_repairs else validator
            if check is not None:
                value = check(value)
            return value
        except WorkflowContractError as exc:
            last_error = exc
            previous_output = output
            if attempt >= max_repairs:
                break

            provenance_rules = (
                "- Preserve the meaning and provenance of the source material.\n"
                "- Do not invent requirements, evidence, defaults, thresholds or decisions.\n"
                if preserve_source else ""
            )
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
{provenance_rules}- Correct JSON syntax and/or the reported contract-shape problem only.
- Return strict RFC 8259 JSON only.
- Use double quotes for every object key and string value.
- Do not use comments, trailing commas, single-quoted strings, NaN, Infinity, ellipses or Markdown fences.
- Return no prose before or after the JSON.
"""

    assert last_error is not None
    raise WorkflowContractError(
        f"{label} failed its JSON contract after {max_repairs + 1} attempts: {last_error}"
    ) from last_error


def run_parallel_steps(calls: list[Callable[[], Any]], max_parallel: int) -> list[Any]:
    """Run independent read-only agent steps, at most max_parallel at a time, and return results in call order.

    Each call must use its own fixed step_id and evidence folder, so CAO replay and the evidence stay
    deterministic whatever order the steps finish in. max_parallel 1 runs them one by one, as before.
    Every step is awaited; if any failed, the first failure (in call order) is raised.
    """
    if not 1 <= max_parallel <= MAX_PARALLEL_REVIEWS:
        raise WorkflowContractError(f"max_parallel must be 1..{MAX_PARALLEL_REVIEWS}")
    if max_parallel == 1 or len(calls) < 2:
        return [call() for call in calls]
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(calls))) as pool:
        futures = [pool.submit(call) for call in calls]
    errors = [future.exception() for future in futures]
    for error in errors:
        if error is not None:
            raise error
    return [future.result() for future in futures]
