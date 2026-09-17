"""Delivery Workflow 2 for an agentic SDLC — v1 (minimal: READY -> IMPLEMENTING).

Flow (full contract, .agentic-sdlc/contracts/delivery-workflow.md):
  READY -> IMPLEMENTING -> VERIFYING -> PR_CREATED -> AGENT_REVIEWING ->
  REMEDIATING -> AWAITING_HUMAN_REVIEW -> HUMAN_APPROVED

This v1 implements only READY and IMPLEMENTING; later stages are added
incrementally, each live-tested against a running cao-server before the
next is written (see the project's build plan/status memory).

Consumes the approved Development Plan Workflow 1 publishes under
.agentic-sdlc/records/<ticket>/ (development-plan.md, execution-manifest.json,
plan-approval-record.json). Never modifies those durable governance records;
delivery's own state lives in a sibling delivery-manifest.json.

Reuses the answer-file delivery mechanism and its security design verbatim
from .agentic-sdlc/cao/workflows/dev_plan.py (see that file's module-level
comment, and cao/workflows/README.md, "Answer file delivery & the
write-scope hook"). What's new here: the sdlc_implementer profile is
additionally granted write access under app/** (not just the answer-file
channel), enforced by the write-scope hook's role-aware widening, which
independently confirms agent-profile identity via CAO's own terminal
metadata before granting it — never by trusting anything the terminal
claims about itself. All git operations (branch creation, staging,
committing) are performed by this trusted Python process; the implementer
agent never runs git itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cao_workflow import emit_output, get_inputs, step

INPUTS = {
    "ticket_id": {"type": "string", "required": True},
    "repository_root": {"type": "path", "required": True},
    "base_branch": {"type": "string", "required": False, "default": "main"},
}

PROVIDER = "claude_code"
IMPLEMENTER = "sdlc_implementer"
STEP_TIMEOUT_SECONDS = 1800
CAO_HTTP_TIMEOUT_SECONDS = 30.0
COMPLETION_INITIAL_SETTLE_SECONDS = 5.0
COMPLETION_POLL_SECONDS = 3.0
COMPLETION_MAX_POLLS = 100
COMPLETION_STABLE_POLLS = 2


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

    See dev_plan.py's identical helper for why this polls terminal *status*
    (not its screen text) alongside the file.
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
                f"{step_id} requires an interactive human answer; headless delivery agents must not block on prompts"
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

    See dev_plan.py's identical function for the execution-completeness-
    before-JSON-parsing rationale (IncompleteAgentExecutionError never
    enters the repair path).
    """
    current_prompt = prompt
    previous_output = ""
    last_error: WorkflowContractError | None = None

    for attempt in range(max_repairs + 1):
        current_step_id = step_id if attempt == 0 else f"{step_id}-repair-{attempt}"
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


def _git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise WorkflowContractError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _current_head_sha(repo: Path) -> str:
    return _git(["rev-parse", "--verify", "HEAD"], cwd=repo).strip()


def _baseline_is_ancestor(repo: Path, baseline_sha: str, base_branch: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", baseline_sha, base_branch],
        cwd=str(repo),
        capture_output=True,
    )
    return result.returncode == 0


def _ensure_delivery_branch(repo: Path, branch: str, base_branch: str) -> None:
    """Create (or resume) the delivery branch from the CURRENT tip of base_branch.

    Deliberately NOT rooted at the plan's historical approval-baseline
    commit. Discovered live: this repository's own tooling (notably
    .claude/hooks/restrict-write-scope.py, which grants the implementer
    agent write access to app/**) is itself tracked in git. Checking out a
    branch rooted at an old commit reverts the working tree's copy of that
    hook back to whatever it was at that commit — silently taking away the
    very write access this workflow's agents depend on, with no error, just
    denied writes. Compatibility with the approved baseline is instead
    checked via ancestry (see _check_plan_approved), not by freezing HEAD at
    that exact historical commit.
    """
    existing = subprocess.run(["git", "rev-parse", "--verify", branch], cwd=str(repo), capture_output=True)
    if existing.returncode == 0:
        _git(["checkout", branch], cwd=repo)
        return
    _git(["checkout", base_branch], cwd=repo)
    _git(["checkout", "-b", branch], cwd=repo)


def _check_plan_approved(records_dir: Path, plan_path: Path, repo: Path, base_branch: str) -> dict[str, Any]:
    """Precondition gate: recompute and verify, never trust a stored claim.

    Mirrors approve_plan.py's own hash-integrity check. Also verifies the
    approved baseline SHA is still an ancestor of base_branch — see
    _ensure_delivery_branch for why ancestry (not an exact-HEAD match, and
    not merely "this commit exists somewhere") is the actual compatibility
    bar for v1.
    """
    manifest_path = records_dir / "execution-manifest.json"
    approval_path = records_dir / "plan-approval-record.json"
    if not manifest_path.is_file() or not approval_path.is_file():
        raise WorkflowContractError(
            f"no approved plan found for this ticket; expected {manifest_path} and {approval_path}"
        )

    manifest = _require_dict(_read_json(manifest_path), "execution-manifest.json")
    approval = _require_dict(_read_json(approval_path), "plan-approval-record.json")

    if manifest.get("state") != "APPROVED":
        raise WorkflowContractError(f"plan is not APPROVED (state={manifest.get('state')!r})")
    if approval.get("decision") != "APPROVED":
        raise WorkflowContractError("plan-approval-record.json does not record an APPROVED decision")

    actual_plan_sha = _sha256_file(plan_path)
    expected_plan_sha = manifest.get("plan_sha256")
    if actual_plan_sha != expected_plan_sha:
        raise WorkflowContractError(
            "development-plan.md has changed since it was reviewed; do not implement a modified plan"
        )
    if approval.get("plan_sha256") != actual_plan_sha:
        raise WorkflowContractError("plan-approval-record.json does not match the current plan hash")

    baseline_sha = _require_str(manifest.get("repository_baseline_sha"), "execution-manifest.json.repository_baseline_sha")
    if not _baseline_is_ancestor(repo, baseline_sha, base_branch):
        raise WorkflowContractError(
            f"approved repository_baseline_sha {baseline_sha} is not an ancestor of {base_branch}; "
            "material drift reconciliation is out of scope for this workflow version"
        )

    return manifest


def build_implementer_prompt(repo: Path, plan_path: Path, contract: Path, governance: Path) -> str:
    return f"""Implement the approved Development Plan for repository {repo}.

Approved Development Plan: {plan_path}
Delivery workflow contract: {contract}
Governance policy: {governance}

Read the plan and implement every task in its Implementation Tasks section against the application source under app/. Follow your profile's boundaries exactly: implement only what the plan asks for, make no test/build/git changes yourself, and disclose any assumption or deviation in your output rather than silently choosing."""


def _implementer_completion_validator(value: Any) -> Any:
    value = _require_dict(value, "implementer completion summary")
    for key in ("tasks_completed", "files_changed", "assumptions", "deviations"):
        items = _require_list(value.get(key), key)
        for index, item in enumerate(items):
            if not isinstance(item, str):
                raise WorkflowContractError(f"{key}[{index}] must be a string")
    return value


def _implement_and_commit(
    *,
    prompt: str,
    step_id: str,
    repo: Path,
    evidence_dir: Path,
    ticket_id: str,
    action_label: str,
) -> dict[str, Any]:
    completion = _run_json_contract_step(
        agent=IMPLEMENTER,
        prompt=prompt,
        label="Implementer",
        step_id=step_id,
        repo=repo,
        evidence_dir=evidence_dir,
        validator=_implementer_completion_validator,
    )
    changed = _git(["status", "--porcelain", "--", "app"], cwd=repo)
    if not changed.strip():
        raise WorkflowContractError(f"{step_id} completed but left no changes under app/")
    _git(["add", "--", "app"], cwd=repo)
    task_list = ", ".join(completion.get("tasks_completed", [])) or "(no tasks reported)"
    _git(["commit", "-m", f"[{ticket_id}] {action_label} (tasks: {task_list})"], cwd=repo)
    return completion


# Fixed, deterministic verification commands — no agent, no judgment. A
# command's exit code and captured output IS the evidence; see the module
# docstring / plan for why this is a trusted Python subprocess call rather
# than something delegated to an agent with execute_bash.
VERIFICATION_COMMANDS: list[list[str]] = [
    ["python3", "-m", "compileall", "-q", "app"],
    ["python3", "-m", "unittest", "discover", "-t", "app", "-s", "app/tests", "-v"],
]
VERIFICATION_TIMEOUT_SECONDS = 300


def _run_verification(repo: Path, evidence_dir: Path, label: str) -> dict[str, Any]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    command_results: list[dict[str, Any]] = []
    all_passed = True
    for index, command in enumerate(VERIFICATION_COMMANDS, start=1):
        try:
            completed = subprocess.run(
                command,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=VERIFICATION_TIMEOUT_SECONDS,
            )
            returncode: int | None = completed.returncode
            stdout, stderr = completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            returncode = None
            stdout = exc.stdout or ""
            stderr = f"{exc.stderr or ''}\n[timed out after {VERIFICATION_TIMEOUT_SECONDS}s]"
        passed = returncode == 0
        all_passed = all_passed and passed
        command_results.append({"command": command, "returncode": returncode, "passed": passed})
        _write_text(
            evidence_dir / f"{label}-cmd{index}.log",
            f"$ {' '.join(command)}\n\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
        )
    summary = {"passed": all_passed, "commands": command_results}
    _write_json(evidence_dir / f"{label}.json", summary)
    return summary


def build_implementer_repair_prompt(
    repo: Path, plan_path: Path, contract: Path, governance: Path, verification: dict[str, Any]
) -> str:
    failed = [c for c in verification["commands"] if not c["passed"]]
    return f"""Your previous implementation attempt for repository {repo} did not pass verification.

Approved Development Plan: {plan_path}
Delivery workflow contract: {contract}
Governance policy: {governance}

Failed verification commands:
{json.dumps(failed, indent=2)}

Fix the implementation under app/ so verification passes. Do not weaken, skip, or delete any test assertion to make it pass — if a test looks wrong given the plan, say so in your output instead of changing the test. Keep changes scoped to fixing the failure; do not otherwise expand scope beyond the plan."""


def main() -> None:
    inputs = get_inputs()
    ticket_id = _safe_component(_require_str(inputs["ticket_id"], "ticket_id"), "ticket_id")
    repo = Path(inputs["repository_root"]).resolve()
    base_branch = _require_str(inputs.get("base_branch", "main"), "base_branch")
    if not repo.is_dir():
        raise WorkflowContractError(f"repository_root is not a directory: {repo}")

    run_id = _safe_component(os.environ.get("CAO_WORKFLOW_RUN_ID", "unknown-run"), "CAO_WORKFLOW_RUN_ID")
    sdlc = repo / ".agentic-sdlc"
    records_dir = sdlc / "records" / ticket_id
    runtime_dir = sdlc / "runtime" / ticket_id / run_id
    implementing_dir = runtime_dir / "implementation"

    delivery_contract = sdlc / "contracts" / "delivery-workflow.md"
    governance = sdlc / "policies" / "governance.md"
    plan_path = records_dir / "development-plan.md"
    for required in (delivery_contract, governance, plan_path):
        if not required.is_file():
            raise WorkflowContractError(f"required SDLC file is missing: {required}")

    manifest = _check_plan_approved(records_dir, plan_path, repo, base_branch)
    baseline_sha = manifest["repository_baseline_sha"]
    delivery_branch = f"sdlc/{ticket_id}"
    delivery_manifest_path = records_dir / "delivery-manifest.json"

    # 1. READY
    _ensure_delivery_branch(repo, delivery_branch, base_branch)
    _write_json(delivery_manifest_path, {
        "schema_version": "1.0",
        "ticket_id": ticket_id,
        "delivery_workflow": "deliver",
        "delivery_workflow_version": "1.0",
        "workflow_run_id": run_id,
        "provider": PROVIDER,
        "repository_root": str(repo),
        "base_branch": base_branch,
        "delivery_branch": delivery_branch,
        "plan_sha256": manifest["plan_sha256"],
        "repository_baseline_sha": baseline_sha,
        "state": "READY",
    })

    # 2. IMPLEMENTING
    completion = _implement_and_commit(
        prompt=build_implementer_prompt(repo, plan_path, delivery_contract, governance),
        step_id="implement-v1",
        repo=repo,
        evidence_dir=implementing_dir / "agent-output",
        ticket_id=ticket_id,
        action_label="Implement approved plan",
    )
    _write_json(implementing_dir / "completion-v1.json", completion)
    commit_sha = _current_head_sha(repo)

    delivery_manifest = _read_json(delivery_manifest_path)
    delivery_manifest["state"] = "IMPLEMENTED"
    delivery_manifest["implementation_commit_sha"] = commit_sha
    delivery_manifest["implementer_summary"] = completion
    _write_json(delivery_manifest_path, delivery_manifest)

    # 3. VERIFYING
    verifying_dir = runtime_dir / "verification"
    verification = _run_verification(repo, verifying_dir, "verify-v1")
    repair_completion: dict[str, Any] | None = None
    if not verification["passed"]:
        repair_completion = _implement_and_commit(
            prompt=build_implementer_repair_prompt(repo, plan_path, delivery_contract, governance, verification),
            step_id="implement-v1-repair-1",
            repo=repo,
            evidence_dir=implementing_dir / "agent-output",
            ticket_id=ticket_id,
            action_label="Repair after verification failure",
        )
        _write_json(implementing_dir / "completion-v1-repair-1.json", repair_completion)
        commit_sha = _current_head_sha(repo)
        verification = _run_verification(repo, verifying_dir, "verify-v1-repair-1")

    delivery_manifest = _read_json(delivery_manifest_path)
    delivery_manifest["implementation_commit_sha"] = commit_sha
    if repair_completion is not None:
        delivery_manifest["implementer_repair_summary"] = repair_completion
    delivery_manifest["verification"] = verification

    if not verification["passed"]:
        delivery_manifest["state"] = "BLOCKED"
        _write_json(delivery_manifest_path, delivery_manifest)
        emit_output({
            "workflow_outcome": "BLOCKED",
            "ticket_id": ticket_id,
            "run_id": run_id,
            "reason": "verification_failed_after_one_repair_attempt",
            "delivery_manifest": str(delivery_manifest_path),
        })
        return

    delivery_manifest["state"] = "VERIFIED"
    _write_json(delivery_manifest_path, delivery_manifest)

    emit_output({
        "workflow_outcome": "VERIFIED",
        "ticket_id": ticket_id,
        "run_id": run_id,
        "delivery_branch": delivery_branch,
        "implementation_commit_sha": commit_sha,
        "delivery_manifest": str(delivery_manifest_path),
        "next_action": "Extend deliver.py with PR_CREATED (not yet implemented in this version).",
    })


if __name__ == "__main__":
    main()
