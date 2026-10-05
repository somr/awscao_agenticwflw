"""Delivery: implement an approved plan, verify, review, remediate and hand off.

Shared execution support lives in runtime.py; build_workflow.py creates the
standalone CAO deployment without changing this workflow's policy or inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from cao_workflow import emit_output, get_inputs

from .verification import (
    VERIFICATION_TIMEOUT_SECONDS,
    _run_verification,
)
from .remediation import (
    _remediator_completion_validator,
)
from .errors import (
    NoSourceChangesError,
    WorkflowContractError,
)
from .artifacts import (
    RECORDS_DIR,
    _read_json,
    _write_json,
    _write_text,
    _sha256_file,
)
from .validation import (
    _safe_component,
    _require_dict,
    _require_list,
    _require_str,
)
from .runtime import (
    _run_json_contract_step,
    PROVIDER,
)
from .hybrid import (
    run_hybrid, load_specialists, validate_dispatch, verification_plan, hybrid_skill_context, MAX_PARALLEL_WORKERS,
)
from .resume import load_resumable_manifest, check_branch, hand_commits, base_drift
from .worktrees import _git
from .source_config import load_source_config, resolve_source_roots

INPUTS = {
    "ticket_id": {"type": "string", "required": True},
    "repository_root": {"type": "path", "required": True},
    "base_branch": {"type": "string", "required": False, "default": "main"},
    "implementation_mode": {"type": "string", "required": False, "default": "hybrid"},
    # CAO reads INPUTS statically, so the default must be a literal (equal to MAX_PARALLEL_WORKERS; a test checks it).
    "hybrid_max_parallel": {"type": "int", "required": False, "default": 4},
    # Continue a BLOCKED delivery from its manifest instead of starting again (see delivery.md).
    "resume": {"type": "bool", "required": False, "default": False},
    # On a resume from implementation whose saved tasks the current registry rejects: let the
    # supervisor assign only the remaining work instead of refusing.
    "resume_redispatch": {"type": "bool", "required": False, "default": False},
}

IMPLEMENTER = "sdlc_implementer"
PR_REVIEWER = "sdlc_pr_reviewer"
REMEDIATOR = "sdlc_remediator"
# pr-review.md: "Default maximum autonomous remediation rounds: 3."
MAX_REMEDIATION_ROUNDS = 3
# 1.1: everything a later run needs to resume a BLOCKED delivery (reason, resume_point,
# branch_head_sha, base_sha, bundle identity, hybrid progress, failed-command output).
MANIFEST_SCHEMA_VERSION = "1.1"

# PR review vocabulary — encodes .agentic-sdlc/policies/pr-review.md's finding
# model and routing policy as executable constants. classify_pr_review()
# recomputes automation_eligibility from these, never trusting the reviewer
# agent's own claim (mirrors dev_plan.py's validate_review not trusting the
# agent's self-reported review_status).
PR_REVIEW_CATEGORIES = {
    "ARCHITECTURE", "PUBLIC_API", "DB_SCHEMA", "AUTHN_AUTHZ", "CRYPTO_SECRETS",
    "DATA_LOSS", "CONCURRENCY", "BUSINESS_RULES", "INFRA_TOPOLOGY", "DEPENDENCY",
    "PLAN_DEVIATION", "CORRECTNESS", "TESTING", "STYLE", "DOCUMENTATION", "OTHER",
}
# "High-impact/protected areas include, at minimum: architecture changes;
# public API/event contract changes; database/schema migrations;
# authentication/authorization/IAM; cryptography/secrets handling;
# data-loss/corruption risks; concurrency/transaction semantics; critical
# financial/business rules; significant infrastructure topology changes;
# major dependency changes; changes that contradict or materially extend the
# approved plan." (pr-review.md) — always DEVELOPER_REQUIRED, any impact.
PROTECTED_PR_REVIEW_CATEGORIES = {
    "ARCHITECTURE", "PUBLIC_API", "DB_SCHEMA", "AUTHN_AUTHZ", "CRYPTO_SECRETS",
    "DATA_LOSS", "CONCURRENCY", "BUSINESS_RULES", "INFRA_TOPOLOGY", "DEPENDENCY",
    "PLAN_DEVIATION",
}
PR_REVIEW_IMPACTS = {"LOW", "MEDIUM", "HIGH"}
PR_REVIEW_AUTOMATION = {"AUTO_FIX", "DEVELOPER_REQUIRED"}
MEDIUM_AUTO_FIX_CONFIDENCE_THRESHOLD = 0.8


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
    agent write access to the source roots) is itself tracked in git. Checking out a
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


def _source_roots(repo: Path) -> list[str]:
    """The validated source roots: where agents may write and where Python commits, verifies and diffs.

    Configured by the registry keys source_roots/write_profiles (default: app). Raises before any
    agent runs when the configuration is invalid or a root resolves outside the repository.
    """
    config = load_source_config(repo)
    resolve_source_roots(repo, config["source_roots"])
    return config["source_roots"]


def _existing_roots(repo: Path, roots: list[str]) -> list[str]:
    # git add fails on a pathspec that matches nothing, and a new module may not exist yet.
    return [root for root in roots if (repo / root).exists()]


def _source_changes(repo: Path, roots: list[str]) -> str:
    existing = _existing_roots(repo, roots)
    if not existing:
        return ""  # an empty pathspec would list the whole repository
    return _git(["status", "--porcelain", "--", *existing], cwd=repo)


def _stage_source_changes(repo: Path, roots: list[str]) -> None:
    _git(["add", "--", *_existing_roots(repo, roots)], cwd=repo)


def _roots_line(repo: Path) -> str:
    return "Source roots (write only under these): " + ", ".join(_source_roots(repo))


def build_implementer_prompt(repo: Path, plan_path: Path, contract: Path, governance: Path) -> str:
    return f"""Implement the approved Development Plan for repository {repo}.

Approved Development Plan: {plan_path}
Delivery workflow contract: {contract}
Governance policy: {governance}
{_roots_line(repo)}

Read the plan and implement every task in its Implementation Tasks section against the application source under the source roots listed above. Follow your profile's boundaries exactly: implement only what the plan asks for, make no test/build/git changes yourself, and disclose any assumption or deviation in your output rather than silently choosing."""


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
    roots = _source_roots(repo)  # invalid configuration stops here, before the agent runs
    completion = _run_json_contract_step(
        preserve_source=False,
        agent=IMPLEMENTER,
        prompt=prompt,
        label="Implementer",
        step_id=step_id,
        repo=repo,
        evidence_dir=evidence_dir,
        validator=_implementer_completion_validator,
    )
    if not _source_changes(repo, roots).strip():
        raise NoSourceChangesError(f"{step_id} completed but left no changes under the source roots ({', '.join(roots)})")
    _stage_source_changes(repo, roots)
    task_list = ", ".join(completion.get("tasks_completed", [])) or "(no tasks reported)"
    _git(["commit", "-m", f"[{ticket_id}] {action_label} (tasks: {task_list})"], cwd=repo)
    return completion


def _hybrid_and_commit(
    *, repo: Path, prompt: str, evidence_dir: Path, ticket_id: str, run_id: str, max_parallel: int,
    progress: dict[str, Any] | None = None, resume_from: dict[str, Any] | None = None, redispatch: bool = False,
) -> tuple[dict[str, Any], list[list[str]], str, list[dict[str, Any]]]:
    """Run hybrid implementation; it commits once per task and once for integration."""
    if _git(["diff", "--cached", "--name-only"], cwd=repo).strip():
        raise WorkflowContractError("Hybrid implementation requires an empty Git index")
    roots = _source_roots(repo)
    if _source_changes(repo, roots).strip():
        raise WorkflowContractError(f"Hybrid implementation requires clean source roots ({', '.join(roots)})")
    completion, commands, context, commits = run_hybrid(
        repo=repo, prompt=prompt, evidence_dir=evidence_dir,
        completion_validator=_implementer_completion_validator,
        ticket_id=ticket_id, run_id=run_id, max_parallel=max_parallel, progress=progress, resume_from=resume_from,
        redispatch=redispatch,
    )
    if not commits:
        raise WorkflowContractError(f"Hybrid implementation left no changes under the source roots ({', '.join(roots)})")
    return completion, commands, context, commits


# Fixed, deterministic verification commands — no agent, no judgment. A
# command's exit code and captured output IS the evidence; see the module
# docstring / plan for why this is a trusted Python subprocess call rather
# than something delegated to an agent with execute_bash. The commands come from the
# project's "verification" suites (agentic-sdlc-project.json, merged by load_specialists):
# the "application" suite in single mode, the union of worker and skill suites in hybrid mode.




def build_implementer_repair_prompt(
    repo: Path, plan_path: Path, contract: Path, governance: Path, verification: dict[str, Any]
) -> str:
    failed = [c for c in verification["commands"] if not c["passed"]]
    return f"""Your previous implementation attempt for repository {repo} did not pass verification.

Approved Development Plan: {plan_path}
Delivery workflow contract: {contract}
Governance policy: {governance}

{_roots_line(repo)}

Failed verification commands, each with the end of its output (output_tail):
{json.dumps(failed, indent=2)}

Fix the implementation under the source roots so verification passes. Do not weaken, skip, or delete any test assertion to make it pass — if a test looks wrong given the plan, say so in your output instead of changing the test. Keep changes scoped to fixing the failure; do not otherwise expand scope beyond the plan."""


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _bundle_identity(registry: dict[str, Any]) -> dict[str, Any]:
    """Which workflow code and settings ran: compared when a later run resumes this one."""
    bundle = globals().get("SDLC_BUNDLE_MANIFEST")  # defined only in the generated CAO bundle
    return {"manifest_sha256": _json_sha256(bundle) if bundle else None, "registry_sha256": _json_sha256(registry)}


def _hybrid_record(progress: dict[str, Any]) -> dict[str, Any]:
    """The manifest's copy of hybrid progress; a conflict's patch stays in the runtime evidence."""
    return {
        "dispatch": progress.get("dispatch"),
        "waves": progress.get("waves"),
        "tasks": [{key: value for key, value in entry.items() if key != "conflict_patch"}
                  for entry in progress.get("tasks", [])],
        "integration": progress.get("integration"),
    }


def _end_blocked(manifest_path: Path, manifest: dict[str, Any], repo: Path, reason: str, resume_point: str) -> None:
    """Write a complete BLOCKED manifest: the only record a later resume run can read."""
    manifest.update(state="BLOCKED", reason=reason, resume_point=resume_point, branch_head_sha=_current_head_sha(repo))
    _write_json(manifest_path, manifest)
    emit_output({
        "workflow_outcome": "BLOCKED",
        "ticket_id": manifest["ticket_id"],
        "run_id": manifest["workflow_run_id"],
        "reason": reason,
        "resume_point": resume_point,
        "delivery_manifest": str(manifest_path),
        "next_action": f"Fix the cause, commit the fix on {manifest['delivery_branch']}, then run Delivery again "
                       "with resume=true; it continues at the resume point.",
    })


def _record_delivery_commits(manifest_path: Path, entries: list[dict[str, str]]) -> None:
    """Append commits Python made for this delivery; a resume checks they are still in the branch."""
    manifest = _read_json(manifest_path)
    recorded = manifest.setdefault("delivery_commits", [])
    new = [entry for entry in entries if entry["sha"] not in {r["sha"] for r in recorded}]
    if new:
        recorded.extend(new)
        _write_json(manifest_path, manifest)


def _task_commits(progress: dict[str, Any]) -> list[dict[str, str]]:
    entries = [{"sha": e["commit"], "step": f"task {e['task']}"} for e in progress.get("tasks", [])
               if e.get("commit") and e.get("status") != "conflict"]
    integration = progress.get("integration") or {}
    if integration.get("commit"):
        entries.append({"sha": integration["commit"], "step": "integration"})
    return entries


def _resume_notes(manifest: dict[str, Any]) -> list[str]:
    """Facts a reviewer and the human must know about a resumed delivery, rendered by Python."""
    previous = manifest.get("resumed_from")
    if not previous:
        return []
    notes = [f"This delivery resumed run {previous['run_id']} (BLOCKED: {previous['reason']}) at {previous['resume_point']}."]
    for commit in manifest.get("hand_commits", []):
        notes.append(f"Commit {commit['sha'][:12]} was added by hand, not by an agent: {commit['subject']} "
                     f"(changes {', '.join(commit['paths']) or 'no files'}).")
    drift = manifest.get("base_drift") or {}
    if drift.get("commits_behind"):
        notes.append(f"The base branch is {drift['commits_behind']} commit(s) ahead of the delivery branch; they share no "
                     "changed files, but the merged result was not verified.")
    return notes


def _merge_completions(*completions: dict[str, Any] | None) -> dict[str, Any]:
    keys = ("tasks_completed", "files_changed", "assumptions", "deviations")
    present = [c for c in completions if c]
    return {key: list(dict.fromkeys(item for c in present for item in c.get(key, []))) for key in keys}


def _compute_delivery_diff(repo: Path, base_branch: str, delivery_branch: str) -> str:
    return _git(["diff", f"{base_branch}...{delivery_branch}"], cwd=repo)


def render_pr_title(ticket_id: str) -> str:
    return f"[{ticket_id}] Implement approved development plan"


def render_pr_body(
    *,
    ticket_id: str,
    plan_path: Path,
    plan_sha256: str,
    completion: dict[str, Any],
    verification: dict[str, Any],
    pr_head_sha: str,
    implementation_commits: list[dict[str, Any]] | None = None,
    resume_notes: list[str] | None = None,
) -> str:
    """Pure Python templating — no agent involved, no judgment needed to
    format already-known facts. Mirrors render_planning_context in
    dev_plan.py: canonical data is JSON, this is one deterministic view of
    it."""
    tasks = ", ".join(completion.get("tasks_completed", [])) or "(none reported)"
    files = "\n".join(f"- `{f}`" for f in completion.get("files_changed", [])) or "- (none reported)"
    assumptions = "\n".join(f"- {a}" for a in completion.get("assumptions", [])) or "- (none)"
    deviations = "\n".join(f"- {d}" for d in completion.get("deviations", [])) or "- (none)"
    verification_lines = "\n".join(
        f"- `{' '.join(c['command'])}` — {'PASS' if c['passed'] else 'FAIL'} (exit {c['returncode']})"
        for c in verification.get("commands", [])
    ) or "- (no verification evidence)"
    commit_section = ""
    if implementation_commits:
        commit_lines = "\n".join(f"- {c['task']}: `{c['commit'][:12]}` ({c['status']})" for c in implementation_commits)
        commit_section = f"**Task commits (hybrid, in merge order):**\n{commit_lines}\n\n"
    if resume_notes:
        commit_section += "**Resumed delivery:**\n" + "\n".join(f"- {note}" for note in resume_notes) + "\n\n"
    return f"""## {ticket_id}

Implements the approved Development Plan (`{plan_path.name}`, sha256 `{plan_sha256}`).

**Tasks completed:** {tasks}

**Files changed:**
{files}

**Assumptions:**
{assumptions}

**Deviations:**
{deviations}

{commit_section}**Verification:**
{verification_lines}

**PR HEAD SHA:** `{pr_head_sha}`

---
This PR was prepared by the agentic delivery workflow. Final approval must be granted by a human reviewer in source control, not by any agent.
"""


def build_pr_reviewer_prompt(
    *,
    repo: Path,
    plan_path: Path,
    contract: Path,
    governance: Path,
    pr_review_policy: Path,
    diff_text: str,
    verification: dict[str, Any],
    pr_head_sha: str,
    resume_notes: list[str] | None = None,
) -> str:
    history = ""
    if resume_notes:
        history = "\nDelivery history (recorded by the workflow; review hand-made commits like any other change):\n" + \
            "\n".join(f"- {note}" for note in resume_notes) + "\n"
    return f"""Independently review the candidate PR diff below for repository {repo}. You did not write this diff.

Approved Development Plan: {plan_path}
Delivery workflow contract: {contract}
Governance policy: {governance}
PR review and remediation policy: {pr_review_policy}
PR HEAD SHA under review: {pr_head_sha}
Source roots (the only places the remediator may edit): {", ".join(_source_roots(repo))}
{history}
Verification evidence (already run independently by the workflow, not by you):
{json.dumps(verification, indent=2)}

Candidate diff (already computed by the workflow; do not run git yourself):
```diff
{diff_text}
```

Classify every finding per the PR review and remediation policy. Mark a finding AUTO_FIX only when an edit to files under the source roots can resolve it: the remediator cannot run commands, and a finding about missing verification evidence or about the PR artifacts is DEVELOPER_REQUIRED. Be honest about impact/category/confidence — the workflow independently enforces the policy's routing rules regardless of what you claim."""


def classify_pr_review(value: Any, *, pr_head_sha: str) -> dict[str, Any]:
    """Recompute automation_eligibility from the policy, never trust the
    reviewer agent's own claim — same discipline as dev_plan.py's
    validate_review not trusting the agent's self-reported review_status.
    """
    value = _require_dict(value, "PR review")
    raw_findings = _require_list(value.get("findings", []), "findings")

    findings: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_findings):
        finding = _require_dict(raw, f"findings[{index}]")
        path = f"findings[{index}]"
        _require_str(finding.get("id"), f"{path}.id")
        _require_str(finding.get("file"), f"{path}.file")
        _require_str(finding.get("failure_scenario"), f"{path}.failure_scenario")
        _require_str(finding.get("reason"), f"{path}.reason")

        category = finding.get("category")
        if category not in PR_REVIEW_CATEGORIES:
            raise WorkflowContractError(f"{path}.category must be one of {sorted(PR_REVIEW_CATEGORIES)}")
        impact = finding.get("impact")
        if impact not in PR_REVIEW_IMPACTS:
            raise WorkflowContractError(f"{path}.impact must be one of {sorted(PR_REVIEW_IMPACTS)}")
        claimed_eligibility = finding.get("automation_eligibility")
        if claimed_eligibility not in PR_REVIEW_AUTOMATION:
            raise WorkflowContractError(f"{path}.automation_eligibility must be one of {sorted(PR_REVIEW_AUTOMATION)}")
        confidence = finding.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
            raise WorkflowContractError(f"{path}.confidence must be a number in [0, 1]")
        localized_and_bounded = bool(finding.get("localized_and_bounded", False))
        deterministically_verifiable = bool(finding.get("deterministically_verifiable", False))

        protected = category in PROTECTED_PR_REVIEW_CATEGORIES
        if impact == "HIGH" or protected:
            # pr-review.md: "High impact: Always DEVELOPER_REQUIRED" and
            # protected areas are DEVELOPER_REQUIRED regardless of impact.
            resolved_eligibility = "DEVELOPER_REQUIRED"
        elif impact == "MEDIUM":
            # pr-review.md's conjunctive MEDIUM conditions: confidence
            # threshold, localized/bounded, not protected (checked above),
            # deterministically verifiable. ("does not reinterpret an
            # approved requirement" and "no material plan deviation" are
            # enforced via the protected-category check on PLAN_DEVIATION.)
            resolved_eligibility = (
                "AUTO_FIX"
                if (
                    claimed_eligibility == "AUTO_FIX"
                    and confidence >= MEDIUM_AUTO_FIX_CONFIDENCE_THRESHOLD
                    and localized_and_bounded
                    and deterministically_verifiable
                )
                else "DEVELOPER_REQUIRED"
            )
        else:  # LOW
            # pr-review.md: "Normally eligible ... when deterministic and
            # local." Honor the agent's own claim unless it already asked
            # for DEVELOPER_REQUIRED itself.
            resolved_eligibility = claimed_eligibility

        findings.append({
            **finding,
            "automation_eligibility": resolved_eligibility,
            "policy_overrode_agent_classification": resolved_eligibility != claimed_eligibility,
            "pr_head_sha_reviewed": pr_head_sha,
        })

    summary = value.get("summary")
    summary = summary if isinstance(summary, str) and summary.strip() else "(no summary provided)"
    return {
        "summary": summary,
        "findings": findings,
        "has_developer_required": any(f["automation_eligibility"] == "DEVELOPER_REQUIRED" for f in findings),
        "has_auto_fix": any(f["automation_eligibility"] == "AUTO_FIX" for f in findings),
    }


def build_remediator_prompt(
    repo: Path,
    plan_path: Path,
    contract: Path,
    pr_review_policy: Path,
    governance: Path,
    findings_to_fix: list[dict[str, Any]],
) -> str:
    return f"""Apply fixes for exactly the findings listed below in repository {repo}. Do not touch anything else.

Approved Development Plan: {plan_path}
Delivery workflow contract: {contract}
PR review and remediation policy: {pr_review_policy}
Governance policy: {governance}
{_roots_line(repo)}

Findings to fix (already filtered to AUTO_FIX-eligible only by the workflow):
{json.dumps(findings_to_fix, indent=2)}

Fix exactly these findings. Do not weaken, skip, or delete any test assertion to make a finding go away — if a finding cannot legitimately be fixed, say so in your output as a deviation instead. Do not touch files or code unrelated to these findings."""


def escalate_unremediated_findings(
    review: dict[str, Any],
    attempted: list[dict[str, Any]],
    remediation_completion: dict[str, Any],
) -> dict[str, Any]:
    """Re-route AUTO_FIX findings the remediator left untouched to DEVELOPER_REQUIRED,
    carrying the remediator's stated reasons into the finding the human reads."""
    attempted_ids = {f["id"] for f in attempted}
    deviations = remediation_completion.get("deviations", [])
    explanation = " ".join(deviations) if deviations else "The remediator gave no reason."
    findings = []
    for finding in review["findings"]:
        if finding["id"] in attempted_ids and finding["automation_eligibility"] == "AUTO_FIX":
            finding = {
                **finding,
                "automation_eligibility": "DEVELOPER_REQUIRED",
                "escalated_after_remediation": True,
                "reason": f"{finding['reason']} Escalated: automatic remediation changed no source file. {explanation}",
            }
        findings.append(finding)
    return {
        **review,
        "findings": findings,
        "has_developer_required": any(f["automation_eligibility"] == "DEVELOPER_REQUIRED" for f in findings),
        "has_auto_fix": any(f["automation_eligibility"] == "AUTO_FIX" for f in findings),
    }




def render_human_review_brief(
    *,
    ticket_id: str,
    pr_reference: str | None,
    pr_head_sha: str,
    plan_sha256: str,
    final_completion: dict[str, Any],
    review: dict[str, Any],
    remediation_history: list[dict[str, Any]],
    verification: dict[str, Any],
    convergence_limit_reached: bool,
    resume_notes: list[str] | None = None,
) -> str:
    """Deterministic Python rendering of the human-review-brief template from
    canonical JSON — same pattern as render_planning_context in dev_plan.py:
    Claude never authors this document, it only produces the structured data
    Python renders from."""
    findings = review["findings"]
    developer_required = [f for f in findings if f["automation_eligibility"] == "DEVELOPER_REQUIRED"]
    auto_remediated_count = sum(len(h["findings_addressed"]) for h in remediation_history)
    remaining_count = len(developer_required)
    findings_detected = auto_remediated_count + remaining_count

    tasks = ", ".join(final_completion.get("tasks_completed", [])) or "(none reported)"
    files = ", ".join(final_completion.get("files_changed", [])) or "(none reported)"
    implementation_summary = f"Implements the approved Development Plan (tasks: {tasks}). Files changed: {files}."
    fix_rounds = [h for h in remediation_history if h.get("commit_sha")]
    if fix_rounds:
        implementation_summary += (
            f" {len(fix_rounds)} autonomous remediation round(s) applied additional fixes"
            " for findings the workflow classified as auto-eligible."
        )
    escalated = [finding_id for h in remediation_history for finding_id in h.get("escalated_findings", [])]
    if escalated:
        implementation_summary += (
            f" Automatic remediation changed no source file for {', '.join(escalated)},"
            " so those findings were escalated to the human reviewer."
        )

    if developer_required:
        attention_text = "\n\n".join(
            f"""### {index}. {finding['id']}: {finding['location']}

- Location: `{finding['file']}`
- Impact: `{finding['impact']}`
- Why human attention is required: {finding['reason']}
- Requirement/plan reference: {finding.get('related_acceptance_criterion') or '(none cited)'}
- Original finding: {finding['failure_scenario']} — {finding['consequence']}
- Developer response: (pending — awaiting human review)
- Verification evidence: see "Verification evidence" below (reviewed at PR HEAD `{finding['pr_head_sha_reviewed']}`)
- Reviewer recommendation: {finding['remediation_direction']}"""
            for index, finding in enumerate(developer_required, start=1)
        )
    else:
        attention_text = "### (none)\n\nNo DEVELOPER_REQUIRED findings remain."

    def _bucket(impact: str, pool: list[dict[str, Any]]) -> str:
        lines = [f"- {f['id']}: {f['location']}" for f in pool if f["impact"] == impact]
        return "\n".join(lines) or "- (none)"

    high_text = _bucket("HIGH", developer_required)
    medium_text = _bucket("MEDIUM", developer_required)
    low_text = _bucket("LOW", findings)

    build_line = "(not run)"
    tests_line = "(not run)"
    other_lines: list[str] = []
    for command in verification.get("commands", []):
        cmd_str = " ".join(command["command"])
        status = "PASS" if command["passed"] else "FAIL"
        rendered = f"`{cmd_str}` — {status} (exit {command['returncode']})"
        if "compileall" in cmd_str:
            build_line = rendered
        elif "unittest" in cmd_str:
            tests_line = rendered
        else:
            other_lines.append(f"- {rendered}")
    other_text = "\n".join(other_lines) or "- (none)"

    residual = list(final_completion.get("deviations", [])) + list(resume_notes or [])
    if convergence_limit_reached:
        residual.append(
            f"Remediation round limit ({MAX_REMEDIATION_ROUNDS}) was reached with unresolved "
            "AUTO_FIX-eligible findings still outstanding — review the remaining findings directly."
        )
    residual_text = "\n".join(f"- {r}" for r in residual) or "- (none disclosed)"

    pr_display = pr_reference or "(local, not yet created)"

    return f"""# Human Review Brief — {ticket_id} / PR {pr_display}

## Review target

- Jira ticket: `{ticket_id}`
- Pull request: `{pr_display}`
- Current PR HEAD SHA: `{pr_head_sha}`
- Approved plan digest: `{plan_sha256}`

## Implementation summary

{implementation_summary}

## Automated review summary

- Findings detected: {findings_detected}
- Automatically remediated: {auto_remediated_count}
- Developer-remediated: 0
- Remaining/escalated: {remaining_count}
- Autonomous remediation rounds: {len(remediation_history)}

## Human attention required

{attention_text}

## Recommended review priority

### High attention
{high_text}

### Medium attention
{medium_text}

### Low-risk / mechanically verified areas
{low_text}

## Verification evidence

- Build: {build_line}
- Static/lint checks: (not run — no separate lint step configured in this version)
- Tests: {tests_line}
- Other checks:
{other_text}

## Residual risks / known limitations

{residual_text}

## Reviewer decision

Final PR approval must be performed by a human in the source-control system.
"""


def main() -> None:
    inputs = get_inputs()
    ticket_id = _safe_component(_require_str(inputs["ticket_id"], "ticket_id"), "ticket_id")
    repo = Path(inputs["repository_root"]).resolve()
    base_branch = _require_str(inputs.get("base_branch", "main"), "base_branch")
    implementation_mode = inputs.get("implementation_mode", "hybrid")
    if implementation_mode not in ("hybrid", "single"):
        raise WorkflowContractError("implementation_mode must be hybrid or single")
    max_parallel = inputs.get("hybrid_max_parallel", MAX_PARALLEL_WORKERS)
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or not 1 <= max_parallel <= MAX_PARALLEL_WORKERS:
        raise WorkflowContractError(f"hybrid_max_parallel must be an integer from 1 to {MAX_PARALLEL_WORKERS}")
    resume = inputs.get("resume", False)
    redispatch = inputs.get("resume_redispatch", False)
    if not isinstance(resume, bool) or not isinstance(redispatch, bool):
        raise WorkflowContractError("resume and resume_redispatch must be true or false")
    if redispatch and not resume:
        raise WorkflowContractError("resume_redispatch needs resume=true")
    if not repo.is_dir():
        raise WorkflowContractError(f"repository_root is not a directory: {repo}")

    run_id = _safe_component(os.environ.get("CAO_WORKFLOW_RUN_ID", "unknown-run"), "CAO_WORKFLOW_RUN_ID")
    sdlc = repo / ".agentic-sdlc"
    records_dir = repo / RECORDS_DIR / ticket_id
    runtime_dir = sdlc / "runtime" / ticket_id / run_id
    implementing_dir = runtime_dir / "implementation"

    delivery_contract = sdlc / "contracts" / "delivery-workflow.md"
    governance = sdlc / "policies" / "governance.md"
    pr_review_policy = sdlc / "policies" / "pr-review.md"
    plan_path = records_dir / "development-plan.md"
    for required in (delivery_contract, governance, pr_review_policy, plan_path):
        if not required.is_file():
            raise WorkflowContractError(f"required SDLC file is missing: {required}")

    manifest = _check_plan_approved(records_dir, plan_path, repo, base_branch)
    delivery_manifest_path = records_dir / "delivery-manifest.json"
    # A resume reads the earlier manifest before anything overwrites it, and takes its mode.
    previous = load_resumable_manifest(delivery_manifest_path, ticket_id=ticket_id, plan_sha256=manifest["plan_sha256"]) if resume else None
    if previous is not None:
        implementation_mode = previous["implementation_mode"]
    # Both modes: validate the source roots and the registry before any agent runs.
    source_roots = _source_roots(repo)
    registry = load_specialists(repo)
    application_commands = registry["verification"].get("application")
    if implementation_mode == "single" and not application_commands:
        raise WorkflowContractError("single mode needs the registry's 'application' verification suite")
    if implementation_mode == "hybrid":
        if _source_changes(repo, source_roots).strip() or _git(["diff", "--cached", "--name-only"], cwd=repo).strip():
            raise WorkflowContractError(
                f"Hybrid delivery requires clean source roots ({', '.join(source_roots)}) and an empty Git index"
            )
    baseline_sha = manifest["repository_baseline_sha"]
    delivery_branch = f"sdlc/{ticket_id}"

    # 1. READY
    carried: dict[str, Any] = {}
    verification_commands = application_commands
    skill_context = ""
    if previous is not None:
        if subprocess.run(["git", "rev-parse", "--verify", delivery_branch], cwd=str(repo), capture_output=True).returncode:
            raise WorkflowContractError(f"resume needs the delivery branch {delivery_branch}; it does not exist")
        _ensure_delivery_branch(repo, delivery_branch, base_branch)
        # Every check runs before the new manifest replaces the earlier one, so a refused
        # resume leaves the BLOCKED manifest in place for the next attempt.
        check_branch(repo, previous, source_roots)
        saved_dispatch = (previous.get("hybrid") or {}).get("dispatch")
        redispatching = redispatch and previous["resume_point"] == "implementation"
        if implementation_mode == "hybrid" and not redispatching and (saved_dispatch or previous["resume_point"] == "verification"):
            # D2: the saved tasks must pass the current bundle's rules and registry.
            dispatch = validate_dispatch(saved_dispatch, registry)
            verification_commands, used_skills = verification_plan(registry, dispatch["tasks"])
            skill_context = hybrid_skill_context(repo, registry, used_skills)
        totals = dict(previous.get("totals") or {"runs": 1, "repair_turns": 0, "remediation_rounds": 0})
        totals["runs"] += 1
        carried = {key: previous[key] for key in (
            "implementation_commits", "hybrid", "implementer_summary", "implementer_repair_summary",
            "implementation_commit_sha", "verification", "review_rounds", "delivery_commits") if key in previous}
        carried.update(
            base_sha=previous["base_sha"],
            totals=totals,
            resumed_from={key: previous.get(key) for key in ("workflow_run_id", "state", "reason", "resume_point", "bundle")},
            hand_commits=previous.get("hand_commits", []) + hand_commits(repo, previous["branch_head_sha"]),
            base_drift=base_drift(repo, base_branch, previous["base_sha"]),
        )
        carried["resumed_from"]["run_id"] = carried["resumed_from"].pop("workflow_run_id")
    else:
        _ensure_delivery_branch(repo, delivery_branch, base_branch)
    _write_json(delivery_manifest_path, {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "ticket_id": ticket_id,
        "delivery_workflow": "sdlc_deliver",
        "delivery_workflow_version": "1.0",
        "workflow_run_id": run_id,
        "provider": PROVIDER,
        "repository_root": str(repo),
        "base_branch": base_branch,
        "delivery_branch": delivery_branch,
        "plan_sha256": manifest["plan_sha256"],
        "repository_baseline_sha": baseline_sha,
        "base_sha": _git(["merge-base", base_branch, delivery_branch], cwd=repo).strip(),
        "bundle": _bundle_identity(registry),
        "totals": {"runs": 1, "repair_turns": 0, "remediation_rounds": 0},
        "state": "READY",
        "implementation_mode": implementation_mode,
        "hybrid_max_parallel": max_parallel if implementation_mode == "hybrid" else None,
        "source_roots": source_roots,
        **carried,
    })
    resume_notes = _resume_notes(carried)
    if previous is not None and carried["base_drift"]["overlapping_paths"]:
        # D6: the base branch changed files this delivery changed; a human merges it (or plans again).
        _end_blocked(delivery_manifest_path, _read_json(delivery_manifest_path), repo,
                     "base_drift_overlap: " + ", ".join(carried["base_drift"]["overlapping_paths"]), previous["resume_point"])
        return

    # 2. IMPLEMENTING (a resume from verification keeps the earlier run's implementation)
    implementation_commits: list[dict[str, Any]] = []
    progress: dict[str, Any] = {}
    if previous is not None and previous["resume_point"] == "verification":
        completion = _merge_completions(previous.get("implementer_summary"), previous.get("implementer_repair_summary"))
        implementation_commits = previous.get("implementation_commits", [])
    elif implementation_mode == "hybrid":
        # A resume from implementation continues from the saved tasks; without them (the
        # supervisor itself failed) it starts the hybrid implementation again.
        saved = (previous or {}).get("hybrid") or {}
        resume_from = saved if previous is not None and saved.get("dispatch") else None
        try:
            completion, verification_commands, skill_context, implementation_commits = _hybrid_and_commit(
                repo=repo, prompt=build_implementer_prompt(repo, plan_path, delivery_contract, governance),
                evidence_dir=implementing_dir / "agent-output", ticket_id=ticket_id,
                run_id=run_id, max_parallel=max_parallel, progress=progress, resume_from=resume_from,
                redispatch=redispatch,
            )
        except (WorkflowContractError, OSError) as exc:
            _record_delivery_commits(delivery_manifest_path, _task_commits(progress))
            failed_manifest = _read_json(delivery_manifest_path)
            failed_manifest["hybrid"] = _hybrid_record(progress)
            _end_blocked(delivery_manifest_path, failed_manifest, repo, f"hybrid_implementation_failed: {exc}", "implementation")
            return
    else:
        try:
            completion = _implement_and_commit(
                prompt=build_implementer_prompt(repo, plan_path, delivery_contract, governance),
                step_id="implement-v1",
                repo=repo,
                evidence_dir=implementing_dir / "agent-output",
                ticket_id=ticket_id,
                action_label="Implement approved plan",
            )
        except (WorkflowContractError, OSError) as exc:
            _end_blocked(delivery_manifest_path, _read_json(delivery_manifest_path), repo,
                         f"implementation_failed: {exc}", "implementation")
            return
        _record_delivery_commits(delivery_manifest_path, [{"sha": _current_head_sha(repo), "step": "implement-v1"}])
    if progress:
        _record_delivery_commits(delivery_manifest_path, _task_commits(progress))
    _write_json(implementing_dir / "completion-v1.json", completion)
    commit_sha = _current_head_sha(repo)

    delivery_manifest = _read_json(delivery_manifest_path)
    delivery_manifest["state"] = "IMPLEMENTED"
    if previous is None or previous["resume_point"] == "implementation":
        delivery_manifest["implementation_commit_sha"] = commit_sha
    if implementation_commits:
        delivery_manifest["implementation_commits"] = implementation_commits
    if progress:
        delivery_manifest["hybrid"] = _hybrid_record(progress)
    delivery_manifest["implementer_summary"] = completion
    _write_json(delivery_manifest_path, delivery_manifest)

    # 3. VERIFYING
    verifying_dir = runtime_dir / "verification"
    verification = _run_verification(repo, verifying_dir, "verify-v1", verification_commands)
    repair_completion: dict[str, Any] | None = None
    if not verification["passed"]:
        try:
            repair_completion = _implement_and_commit(
                prompt=build_implementer_repair_prompt(repo, plan_path, delivery_contract, governance, verification) + "\n" + skill_context,
                step_id="implement-v1-repair-1",
                repo=repo,
                evidence_dir=implementing_dir / "agent-output",
                ticket_id=ticket_id,
                action_label="Repair after verification failure",
            )
        except (WorkflowContractError, OSError) as exc:
            failed_manifest = _read_json(delivery_manifest_path)
            failed_manifest["verification"] = verification
            failed_manifest["totals"]["repair_turns"] += 1
            reason = "repair_changed_nothing" if isinstance(exc, NoSourceChangesError) else f"repair_failed: {exc}"
            _end_blocked(delivery_manifest_path, failed_manifest, repo, reason, "verification")
            return
        _write_json(implementing_dir / "completion-v1-repair-1.json", repair_completion)
        commit_sha = _current_head_sha(repo)
        _record_delivery_commits(delivery_manifest_path, [{"sha": commit_sha, "step": "implement-v1-repair-1"}])
        verification = _run_verification(repo, verifying_dir, "verify-v1-repair-1", verification_commands)

    delivery_manifest = _read_json(delivery_manifest_path)
    delivery_manifest["implementation_commit_sha"] = commit_sha
    if repair_completion is not None:
        delivery_manifest["implementer_repair_summary"] = repair_completion
        delivery_manifest["totals"]["repair_turns"] += 1
    delivery_manifest["verification"] = verification

    if not verification["passed"]:
        _end_blocked(delivery_manifest_path, delivery_manifest, repo, "verification_failed_after_one_repair_attempt", "verification")
        return

    delivery_manifest["state"] = "VERIFIED"
    _write_json(delivery_manifest_path, delivery_manifest)

    # 4. PR_CREATED — local/simulated only: no gh pr create, no push. This is
    # the one seam a later phase swaps for real PR creation; everything else
    # in this workflow is unaffected by that later change.
    final_completion = completion
    if repair_completion is not None:
        final_completion = {key: list(dict.fromkeys(completion[key] + repair_completion[key]))
                            for key in ("tasks_completed", "files_changed", "assumptions", "deviations")}
    pr_head_sha = commit_sha
    diff_text = _compute_delivery_diff(repo, base_branch, delivery_branch)
    pr_title = render_pr_title(ticket_id)
    pr_body = render_pr_body(
        ticket_id=ticket_id,
        plan_path=plan_path,
        plan_sha256=manifest["plan_sha256"],
        completion=final_completion,
        verification=verification,
        pr_head_sha=pr_head_sha,
        implementation_commits=implementation_commits,
        resume_notes=resume_notes,
    )
    pr_title_path = records_dir / "pr-title.txt"
    pr_body_path = records_dir / "pr-body.md"
    pr_diff_path = records_dir / "pr-diff.patch"
    _write_text(pr_title_path, pr_title)
    _write_text(pr_body_path, pr_body)
    pr_diff_path.parent.mkdir(parents=True, exist_ok=True)
    pr_diff_path.write_text(diff_text, encoding="utf-8")

    delivery_manifest = _read_json(delivery_manifest_path)
    delivery_manifest["state"] = "PR_CREATED"
    delivery_manifest["pr_reference"] = None
    delivery_manifest["pr_head_sha"] = pr_head_sha
    delivery_manifest["pr_title_path"] = str(pr_title_path.relative_to(repo))
    delivery_manifest["pr_body_path"] = str(pr_body_path.relative_to(repo))
    delivery_manifest["pr_diff_path"] = str(pr_diff_path.relative_to(repo))
    _write_json(delivery_manifest_path, delivery_manifest)

    # 5. AGENT_REVIEWING
    review_round = 1
    # Review records continue the earlier run's numbering; the remediation budget starts again (D7).
    review_offset = (previous or {}).get("review_rounds") or 0
    reviews_written = [0]  # reviews of this run with a record, for the numbering of a later resume
    reviewing_dir = runtime_dir / "review"

    def _review(round_no: int, sha: str, diff: str, verif: dict[str, Any]) -> dict[str, Any]:
        result = _run_json_contract_step(
            preserve_source=False,
            agent=PR_REVIEWER,
            prompt=build_pr_reviewer_prompt(
                repo=repo,
                plan_path=plan_path,
                contract=delivery_contract,
                governance=governance,
                pr_review_policy=pr_review_policy,
                diff_text=diff,
                verification=verif,
                pr_head_sha=sha,
                resume_notes=resume_notes,
            ),
            label="PR Reviewer",
            step_id=f"pr-review-r{round_no}",
            repo=repo,
            evidence_dir=reviewing_dir / "agent-output",
            validator=lambda value, sha=sha: classify_pr_review(value, pr_head_sha=sha),
        )
        _write_json(records_dir / f"pr-review-r{review_offset + round_no}.json", result)
        reviews_written[0] = round_no
        return result

    remediating_dir = runtime_dir / "remediation"
    remediation_history: list[dict[str, Any]] = []
    blocked_reason: str | None = None
    try:
        review = _review(review_round, pr_head_sha, diff_text, verification)

        # 6. REMEDIATING — bounded loop, max MAX_REMEDIATION_ROUNDS (pr-review.md
        # default: 3). Only findings the workflow itself classified AUTO_FIX are
        # ever handed to the remediator — never a DEVELOPER_REQUIRED finding
        # (governance #6/#7). Every remediation batch is followed by independent
        # re-verification (governance #8) and a fresh review of the new HEAD
        # (governance #9/#10) — the remediator's own completion claim is never
        # trusted as proof a finding is actually fixed.
        while review["has_auto_fix"] and review_round < MAX_REMEDIATION_ROUNDS:
            auto_fix_findings = [f for f in review["findings"] if f["automation_eligibility"] == "AUTO_FIX"]
            remediation_step_id = f"remediate-r{review_round}"
            remediation_completion = _run_json_contract_step(
                preserve_source=False,
                agent=REMEDIATOR,
                prompt=build_remediator_prompt(repo, plan_path, delivery_contract, pr_review_policy, governance, auto_fix_findings) + "\n" + skill_context,
                label="Remediator",
                step_id=remediation_step_id,
                repo=repo,
                evidence_dir=remediating_dir / "agent-output",
                validator=_remediator_completion_validator,
            )
            _write_json(remediating_dir / f"{remediation_step_id}-completion.json", remediation_completion)

            if not _source_changes(repo, source_roots).strip():
                # pr-review.md convergence rule: escalate when verification cannot
                # establish correctness. A remediator that changes nothing is
                # saying no source change can fix these findings (for example
                # missing verification evidence), so they go to the human
                # instead of failing a run whose HEAD already passed verification.
                review = escalate_unremediated_findings(review, auto_fix_findings, remediation_completion)
                remediation_history.append({
                    "round": review_offset + review_round,
                    "remediation_step_id": remediation_step_id,
                    "findings_addressed": [],
                    "commit_sha": None,
                    "verification_passed": None,
                    "escalated_findings": [f["id"] for f in auto_fix_findings],
                    "remediator_deviations": remediation_completion.get("deviations", []),
                })
                break
            _stage_source_changes(repo, source_roots)
            addressed = ", ".join(remediation_completion.get("findings_addressed", [])) or "(none reported)"
            _git(["commit", "-m", f"[{ticket_id}] Remediate findings ({addressed})"], cwd=repo)
            commit_sha = _current_head_sha(repo)
            _record_delivery_commits(delivery_manifest_path, [{"sha": commit_sha, "step": remediation_step_id}])
            pr_head_sha = commit_sha

            # Governance #8: verify after every remediation batch. A remediation
            # that breaks verification is escalation-worthy, not something to
            # retry blindly — stop the loop rather than looping on a regression.
            verification = _run_verification(repo, verifying_dir, f"verify-remediate-r{review_round}", verification_commands)
            diff_text = _compute_delivery_diff(repo, base_branch, delivery_branch)

            remediation_history.append({
                "round": review_offset + review_round,
                "remediation_step_id": remediation_step_id,
                "findings_addressed": remediation_completion.get("findings_addressed", []),
                "commit_sha": commit_sha,
                "verification_passed": verification["passed"],
            })

            if not verification["passed"]:
                blocked_reason = "verification_failed_after_remediation"
                break

            review_round += 1
            # Governance #9: fresh review evidence for the new HEAD — never
            # trust the remediator's own claim that a finding is resolved.
            review = _review(review_round, pr_head_sha, diff_text, verification)
    except (WorkflowContractError, OSError) as exc:
        # A reviewer or remediator that breaks its contract ends a defined state that a
        # later run can resume from verification; any remediation commits stay on the branch.
        failed_manifest = _read_json(delivery_manifest_path)
        failed_manifest.update(review_rounds=review_offset + reviews_written[0], remediation_history=remediation_history, verification=verification)
        failed_manifest["totals"]["remediation_rounds"] += len(remediation_history)
        _end_blocked(delivery_manifest_path, failed_manifest, repo, f"review_failed: {exc}", "verification")
        return

    convergence_limit_reached = review_round >= MAX_REMEDIATION_ROUNDS and review["has_auto_fix"]

    delivery_manifest = _read_json(delivery_manifest_path)
    delivery_manifest["implementation_commit_sha"] = commit_sha
    delivery_manifest["pr_head_sha"] = pr_head_sha
    delivery_manifest["review_rounds"] = review_offset + review_round
    delivery_manifest["latest_review_path"] = str((records_dir / f"pr-review-r{review_offset + review_round}.json").relative_to(repo))
    delivery_manifest["latest_review_has_developer_required"] = review["has_developer_required"]
    delivery_manifest["latest_review_has_auto_fix"] = review["has_auto_fix"]
    delivery_manifest["remediation_history"] = remediation_history
    delivery_manifest["escalated_findings"] = [
        finding_id for h in remediation_history for finding_id in h.get("escalated_findings", [])
    ]
    delivery_manifest["convergence_limit_reached"] = convergence_limit_reached

    delivery_manifest["totals"]["remediation_rounds"] += len(remediation_history)

    if blocked_reason is not None:
        _end_blocked(delivery_manifest_path, delivery_manifest, repo, blocked_reason, "verification")
        return

    # Re-render the PR artifacts against the final post-remediation HEAD/diff
    # before producing the brief. Live-testing surfaced this concretely: the
    # PR reviewer itself flagged a stale PR HEAD SHA and a "Deviations: none"
    # claim left over from before remediation as its own LOW finding — the
    # PR package must reflect what a human is actually about to review, not
    # what existed right after IMPLEMENTING.
    pr_body = render_pr_body(
        ticket_id=ticket_id,
        plan_path=plan_path,
        plan_sha256=manifest["plan_sha256"],
        completion=final_completion,
        verification=verification,
        pr_head_sha=pr_head_sha,
        implementation_commits=implementation_commits,
        resume_notes=resume_notes,
    )
    _write_text(pr_title_path, render_pr_title(ticket_id))
    _write_text(pr_body_path, pr_body)
    pr_diff_path.write_text(diff_text, encoding="utf-8")

    # 7. AWAITING_HUMAN_REVIEW — render the brief and stop; main() never
    # blocks waiting for the human, same shape as dev_plan.py's
    # AWAITING_HUMAN_APPROVAL ending.
    brief_text = render_human_review_brief(
        ticket_id=ticket_id,
        pr_reference=delivery_manifest.get("pr_reference"),
        pr_head_sha=pr_head_sha,
        plan_sha256=manifest["plan_sha256"],
        final_completion=final_completion,
        review=review,
        remediation_history=remediation_history,
        verification=verification,
        convergence_limit_reached=convergence_limit_reached,
        resume_notes=resume_notes,
    )
    brief_path = records_dir / "human-review-brief.md"
    _write_text(brief_path, brief_text)

    delivery_manifest["state"] = "AWAITING_HUMAN_REVIEW"
    delivery_manifest["human_review_brief_path"] = str(brief_path.relative_to(repo))
    _write_json(delivery_manifest_path, delivery_manifest)

    emit_output({
        "workflow_outcome": "AWAITING_HUMAN_REVIEW",
        "ticket_id": ticket_id,
        "run_id": run_id,
        "delivery_branch": delivery_branch,
        "resumed_from": (carried.get("resumed_from") or {}).get("run_id"),
        "pr_head_sha": pr_head_sha,
        "review_round": review_offset + review_round,
        "findings_count": len(review["findings"]),
        "has_developer_required_findings": review["has_developer_required"],
        "has_auto_fix_findings": review["has_auto_fix"],
        "convergence_limit_reached": convergence_limit_reached,
        "remediation_rounds": len(remediation_history),
        "escalated_findings": delivery_manifest["escalated_findings"],
        "human_review_brief": str(brief_path),
        "delivery_manifest": str(delivery_manifest_path),
        "next_action": "Human reviews human-review-brief.md and records HUMAN_APPROVED or REJECTED with record_pr_approval.py.",
    })


if __name__ == "__main__":
    main()
