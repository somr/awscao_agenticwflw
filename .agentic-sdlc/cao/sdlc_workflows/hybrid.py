"""Bounded model-directed task allocation; Python owns dispatch and evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import WorkflowContractError
from .artifacts import _read_json, _write_json, _write_text, _sha256_file
from .runtime import _run_json_contract_step

SUPERVISOR = "sdlc_code_supervisor"
MAX_HYBRID_TASKS = 16


def _hybrid_asset(repo: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise WorkflowContractError("Hybrid asset path must be a nonempty string")
    root = (repo / ".agentic-sdlc" / "cao").resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise WorkflowContractError(f"Invalid hybrid asset: {relative}")
    return path


def load_specialists(repo: Path) -> dict[str, Any]:
    registry = _read_json(_hybrid_asset(repo, "specialists.json"))
    if not isinstance(registry, dict) or registry.get("version") != 1:
        raise WorkflowContractError("Unsupported specialist registry")
    for section in ("workers", "skills", "verification"):
        if not isinstance(registry.get(section), dict) or not registry[section]:
            raise WorkflowContractError(f"Registry requires {section}")
    for name, skill in registry["skills"].items():
        if not isinstance(skill, dict) or not isinstance(skill.get("description"), str):
            raise WorkflowContractError(f"Invalid skill {name}")
        _hybrid_asset(repo, skill.get("path"))
        suites = skill.get("verification")
        if not isinstance(suites, list) or not suites or any(not isinstance(s, str) or s not in registry["verification"] for s in suites):
            raise WorkflowContractError(f"Skill {name} requires registered verification")
    for name, worker in registry["workers"].items():
        if not isinstance(worker, dict) or not isinstance(worker.get("profile"), str):
            raise WorkflowContractError(f"Invalid worker {name}")
        if not isinstance(worker.get("description"), str):
            raise WorkflowContractError(f"Missing routing description: {name}")
        for field, catalog in (("skills", "skills"), ("verification", "verification")):
            values = worker.get(field)
            if not isinstance(values, list) or any(not isinstance(v, str) or v not in registry[catalog] for v in values):
                raise WorkflowContractError(f"Invalid {field} for {name}")
        if not worker["verification"]:
            raise WorkflowContractError(f"Worker {name} requires verification")
    for name, commands in registry["verification"].items():
        if not isinstance(commands, list) or not commands:
            raise WorkflowContractError(f"Empty verification suite {name}")
        for command in commands:
            if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
                raise WorkflowContractError(f"Invalid verification command in {name}")
    return registry


def validate_dispatch(value: Any, registry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
        raise WorkflowContractError("Supervisor must return a tasks array")
    tasks = value["tasks"]
    if not 1 <= len(tasks) <= MAX_HYBRID_TASKS:
        raise WorkflowContractError(f"Expected 1..{MAX_HYBRID_TASKS} tasks")
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise WorkflowContractError("Task must be an object")
        task_id = task.get("id")
        if not isinstance(task_id, str) or len(task_id) > 128 or not task_id.isascii() or not task_id.replace("-", "").isalnum() or task_id in seen:
            raise WorkflowContractError("Task IDs must be unique ASCII alphanumeric/hyphen strings")
        worker = task.get("worker")
        if not isinstance(worker, str) or worker not in registry["workers"]:
            raise WorkflowContractError("Unregistered worker")
        for key in ("instructions", "plan_reference"):
            if not isinstance(task.get(key), str) or not task[key].strip():
                raise WorkflowContractError(f"Task requires {key}")
        dependencies = task.get("depends_on")
        if not isinstance(dependencies, list) or any(not isinstance(d, str) or d not in seen for d in dependencies):
            raise WorkflowContractError("Dependencies must refer to earlier tasks (no cycles)")
        skills = task.get("skills")
        if not isinstance(skills, list) or any(not isinstance(s, str) or s not in registry["workers"][worker]["skills"] for s in skills):
            raise WorkflowContractError("Worker cannot use unregistered skills")
        seen.add(task_id)
    return value


def hybrid_skill_context(repo: Path, registry: dict[str, Any], skills: list[str]) -> str:
    sections = []
    for name in dict.fromkeys(skills):
        path = _hybrid_asset(repo, registry["skills"][name]["path"])
        sections.append(f"Required skill {name} (source={path}, sha256={_sha256_file(path)}):\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(sections)


def run_hybrid(*, repo: Path, prompt: str, evidence_dir: Path, completion_validator: Any) -> tuple[dict[str, Any], list[list[str]], str]:
    registry = load_specialists(repo)
    _write_json(evidence_dir / "registry.json", registry)
    catalog = {"workers": registry["workers"], "skills": registry["skills"]}
    dispatch = _run_json_contract_step(
        agent=SUPERVISOR, label="Code supervisor", step_id="dispatch-v1", repo=repo,
        evidence_dir=evidence_dir,
        prompt=prompt + "\nAllocate the approved work using this catalog:\n" + json.dumps(catalog) +
        '\nReturn {"tasks":[{"id":"T1","worker":"developer","plan_reference":"approved task reference",'
        '"instructions":"bounded assignment and interface contracts","depends_on":[],"skills":[]}]}. '
        "Cover every approved task, list dependencies before consumers, and use at most 16 tasks. "
        "Do not change source. Python dispatches your assignments sequentially. Select applicable skills explicitly.",
        validator=lambda value: validate_dispatch(value, registry),
    )
    _write_json(evidence_dir / "dispatch.json", dispatch)
    combined = {key: [] for key in ("tasks_completed", "files_changed", "assumptions", "deviations")}
    results = []
    commands: list[list[str]] = []
    used_skills: list[str] = []
    for index, task in enumerate(dispatch["tasks"], start=1):
        worker = registry["workers"][task["worker"]]
        used_skills.extend(task["skills"])
        suites = worker["verification"] + [suite for skill in task["skills"] for suite in registry["skills"][skill]["verification"]]
        for suite in suites:
            for command in registry["verification"][suite]:
                if command not in commands:
                    commands.append(command)
        result = _run_json_contract_step(
            agent=worker["profile"], label=f"Worker {task['id']}", step_id=f"worker-{index}",
            repo=repo, evidence_dir=evidence_dir, preserve_source=False,
            prompt=prompt + "\nImplement ONLY this assigned portion of the approved plan:\n" + json.dumps(task) +
            "\nPrior worker results (inspect actual files too):\n" + json.dumps(results) +
            "\n" + hybrid_skill_context(repo, registry, task["skills"]),
            validator=completion_validator,
        )
        results.append({"task": task["id"], "result": result})
        for key in combined:
            combined[key].extend(result[key])
        _write_json(evidence_dir / "worker-results.json", results)
    context = hybrid_skill_context(repo, registry, used_skills)
    _write_text(evidence_dir / "required-skills.md", context)
    # A single existing implementer owns integration across all assignments.
    integrated = _run_json_contract_step(
        agent="sdlc_implementer", label="Integration", step_id="integrate-v1", repo=repo,
        evidence_dir=evidence_dir, preserve_source=False, validator=completion_validator,
        prompt=prompt + "\nIntegrate the completed assignments. Inspect actual source against EVERY approved task; "
        "resolve interface mismatches and missing approved work. Do not execute commands. "
        "Report completion for the whole feature, including unresolved deviations.\n" +
        json.dumps({"dispatch": dispatch, "results": results}) + "\n" + context,
    )
    for key in combined:
        combined[key] = list(dict.fromkeys(combined[key] + integrated[key]))
    return combined, commands, context
