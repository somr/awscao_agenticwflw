"""Bounded model-directed task allocation; Python owns dispatch and evidence."""
from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .errors import WorkflowContractError
from .artifacts import _read_json, _write_json, _write_text, _sha256_file
from .runtime import _run_json_contract_step
from .source_config import (
    validate_source_config, load_project_file, select_source_settings, PROJECT_KEYS, PROJECT_RELATIVE_PATH,
    _normalize_root, _inside_any,
)
from .worktrees import (
    add_worktree, changed_paths, check_trusted_files, cherry_pick, commit_task, head, outside_owned,
    prune_worktrees, remove_worktree, task_patch,
)

SUPERVISOR = "sdlc_code_supervisor"
MAX_HYBRID_TASKS = 16
# CAO step ids are at most 64 characters; "worker-<id>-rerun-repair-1" must fit.
MAX_TASK_ID_LENGTH = 40
MAX_OWNED_PATHS = 64
MAX_PARALLEL_WORKERS = 4
# Task results that count as finished when a BLOCKED run is resumed ("conflict" is followed by
# its "rerun" entry when the rerun finished).
FINISHED_STATUSES = ("committed", "merged", "rerun", "no_changes")


def _hybrid_asset(repo: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise WorkflowContractError("Hybrid asset path must be a nonempty string")
    root = (repo / ".agentic-sdlc" / "cao").resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise WorkflowContractError(f"Invalid hybrid asset: {relative}")
    return path


def load_specialists(repo: Path) -> dict[str, Any]:
    """The common registry merged with the project's settings.

    The project file (agentic-sdlc-project.json), when present, supplies source_roots,
    write_profiles and verification. A skill whose verification suites the project does not
    define is unavailable: it is left out of the catalog and listed in "unavailable_skills".
    """
    registry = _read_json(_hybrid_asset(repo, "specialists.json"))
    if not isinstance(registry, dict) or registry.get("version") != 1:
        raise WorkflowContractError("Unsupported specialist registry")
    project = load_project_file(repo)
    if project is not None:
        select_source_settings(registry, project)  # refuses keys set in both files
        registry = dict(registry, **{key: project[key] for key in PROJECT_KEYS if key in project})
    source = PROJECT_RELATIVE_PATH if project is not None else "the registry"
    if not isinstance(registry.get("workers"), dict) or not registry["workers"]:
        raise WorkflowContractError("Registry requires workers")
    if not isinstance(registry.get("verification"), dict) or not registry["verification"]:
        raise WorkflowContractError(f"Verification suites are required in {source}")
    if not isinstance(registry.get("skills", {}), dict):
        raise WorkflowContractError("Registry skills must be an object")
    available: dict[str, Any] = {}
    unavailable: dict[str, list[str]] = {}
    for name, skill in registry.get("skills", {}).items():
        if not isinstance(skill, dict) or not isinstance(skill.get("description"), str):
            raise WorkflowContractError(f"Invalid skill {name}")
        _hybrid_asset(repo, skill.get("path"))
        suites = skill.get("verification")
        if not isinstance(suites, list) or not suites or any(not isinstance(s, str) or not s for s in suites):
            raise WorkflowContractError(f"Skill {name} requires registered verification")
        missing = [suite for suite in suites if suite not in registry["verification"]]
        if missing:
            unavailable[name] = missing
        else:
            available[name] = skill
    registry["skills"] = available
    registry["unavailable_skills"] = unavailable
    for name, worker in registry["workers"].items():
        if isinstance(worker, dict) and isinstance(worker.get("skills"), list):
            worker = registry["workers"][name] = dict(worker, skills=[s for s in worker["skills"] if s not in unavailable])
    for name, worker in registry["workers"].items():
        if not isinstance(worker, dict) or not isinstance(worker.get("profile"), str):
            raise WorkflowContractError(f"Invalid worker {name}")
        if not isinstance(worker.get("description"), str):
            raise WorkflowContractError(f"Missing routing description: {name}")
        for field in ("skills", "verification"):
            values = worker.get(field)
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise WorkflowContractError(f"Invalid {field} for {name}")
        if any(skill not in registry["skills"] for skill in worker["skills"]):
            raise WorkflowContractError(f"Invalid skills for {name}")
        undefined = [suite for suite in worker["verification"] if suite not in registry["verification"]]
        if undefined:
            raise WorkflowContractError(f"Worker {name} needs verification suite(s) {', '.join(undefined)}, not defined in {source}")
        if not worker["verification"]:
            raise WorkflowContractError(f"Worker {name} requires verification")
    for name, commands in registry["verification"].items():
        if not isinstance(commands, list) or not commands:
            raise WorkflowContractError(f"Empty verification suite {name}")
        for command in commands:
            if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
                raise WorkflowContractError(f"Invalid verification command in {name}")
    # A worker whose profile may not write could never implement its assignment.
    write_profiles = validate_source_config(registry)["write_profiles"]
    for name, worker in registry["workers"].items():
        if worker["profile"] not in write_profiles:
            raise WorkflowContractError(f"Worker {name} uses profile {worker['profile']}, which is not listed in write_profiles")
    return registry


def validate_dispatch(value: Any, registry: dict[str, Any]) -> dict[str, Any]:
    roots = validate_source_config(registry)["source_roots"]
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
        if not isinstance(task_id, str) or len(task_id) > MAX_TASK_ID_LENGTH or not task_id.isascii() or not task_id.replace("-", "").isalnum() or task_id in seen:
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
        owns = task.get("owns")
        if not isinstance(owns, list) or not 1 <= len(owns) <= MAX_OWNED_PATHS:
            raise WorkflowContractError(f"Task {task_id} requires owns: 1..{MAX_OWNED_PATHS} paths")
        for path in owns:
            _normalize_root(path, f"Task {task_id} owns entry")
            if not _inside_any(path, roots):
                raise WorkflowContractError(f"Task {task_id} owns {path!r}, which is outside the source roots")
        seen.add(task_id)
    return value


def _paths_overlap(first: list[str], second: list[str]) -> list[str]:
    pairs = [(a, b) for a in first for b in second if a == b or a.startswith(b + "/") or b.startswith(a + "/")]
    return sorted({path for pair in pairs for path in pair})


def build_schedule(tasks: list[dict[str, Any]], max_parallel: int) -> dict[str, Any]:
    """Group validated tasks into waves that may run concurrently.

    Tasks that own overlapping paths and have no dependency path between them
    get an added dependency (earlier -> later), so they never share a wave.
    Waves are dependency levels in the supervisor's order, split into chunks
    of max_parallel. Deterministic for the same input.
    """
    if not 1 <= max_parallel <= MAX_PARALLEL_WORKERS:
        raise WorkflowContractError(f"max_parallel must be 1..{MAX_PARALLEL_WORKERS}")
    order = [task["id"] for task in tasks]
    by_id = {task["id"]: task for task in tasks}
    depends = {task["id"]: list(task["depends_on"]) for task in tasks}
    ancestors: dict[str, set[str]] = {}
    added = []
    for index, task_id in enumerate(order):
        ancestors[task_id] = set()
        for dep in depends[task_id]:
            ancestors[task_id] |= {dep} | ancestors[dep]
        for earlier in order[:index]:
            if earlier in ancestors[task_id]:
                continue
            shared = _paths_overlap(by_id[earlier]["owns"], by_id[task_id]["owns"])
            if shared:
                depends[task_id].append(earlier)
                ancestors[task_id] |= {earlier} | ancestors[earlier]
                added.append({"task": task_id, "depends_on": earlier, "overlapping_paths": shared})
    level: dict[str, int] = {}
    for task_id in order:
        level[task_id] = 1 + max((level[dep] for dep in depends[task_id]), default=-1)
    waves: list[list[str]] = []
    for depth in range(max(level.values(), default=-1) + 1):
        members = [task_id for task_id in order if level[task_id] == depth]
        waves.extend(members[start:start + max_parallel] for start in range(0, len(members), max_parallel))
    return {
        "max_parallel": max_parallel,
        "waves": waves,
        "depends_on": depends,
        "ancestors": {task_id: [a for a in order if a in ancestors[task_id]] for task_id in order},
        "added_dependencies": added,
    }


def verification_plan(registry: dict[str, Any], tasks: list[dict[str, Any]]) -> tuple[list[list[str]], list[str]]:
    """The verification commands for these tasks (worker and skill suites, in order, once each) and their skills."""
    commands: list[list[str]] = []
    used_skills: list[str] = []
    for task in tasks:
        worker = registry["workers"][task["worker"]]
        used_skills.extend(task["skills"])
        suites = worker["verification"] + [suite for skill in task["skills"] for suite in registry["skills"][skill]["verification"]]
        for suite in suites:
            for command in registry["verification"][suite]:
                if command not in commands:
                    commands.append(command)
    return commands, used_skills


def hybrid_skill_context(repo: Path, registry: dict[str, Any], skills: list[str]) -> str:
    sections = []
    for name in dict.fromkeys(skills):
        path = _hybrid_asset(repo, registry["skills"][name]["path"])
        sections.append(f"Required skill {name} (source={path}, sha256={_sha256_file(path)}):\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(sections)


def _worker_prompt(prompt: str, task: dict[str, Any], prior: list[dict[str, Any]], skills: str, tree: Path | None) -> str:
    isolated = "" if tree is None else (
        f"\nYou work in an isolated working copy of the repository at {tree}. Make every source edit inside it, "
        "under its source roots; the repository path above is for reading the plan and policies only. "
        "Other workers implement other tasks at the same time in their own copies."
    )
    return (prompt + isolated + "\nImplement ONLY this assigned portion of the approved plan:\n" + json.dumps(task) +
            "\nChange only the paths listed in owns; report any other change you must make as a deviation." +
            "\nResults of the tasks this one depends on (inspect actual files too):\n" + json.dumps(prior) +
            "\n" + skills)


def _commit_message(ticket_id: str, task: dict[str, Any]) -> str:
    reference = " ".join(task["plan_reference"].split())
    return f"[{ticket_id}] {task['id']}: implement hybrid task\n\nPlan reference: {reference}"


def run_hybrid(*, repo: Path, prompt: str, evidence_dir: Path, completion_validator: Any, ticket_id: str,
               run_id: str, max_parallel: int,
               progress: dict[str, Any] | None = None,
               resume_from: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[list[str]], str, list[dict[str, Any]]]:
    """Supervisor -> waves of workers -> integration; commits once per task (and once for integration).

    A wave of one task runs in the main checkout. A wave of two or more runs each
    task in its own worktree concurrently; after the wave Python commits each task
    there and cherry-picks the commits onto the delivery branch in task order. A
    conflicting task is rerun once, alone, in the main checkout. A worker failure
    lets its wave finish, merges nothing from that wave and stops the run.

    progress, when given, is filled as the run goes (dispatch, waves, one entry per
    finished task), so a caller still has it when a later step raises.

    resume_from, the hybrid record of a BLOCKED run, replaces the supervisor with its saved
    tasks (checked against the current registry) and skips the tasks it finished; their
    commits must already be in the branch (the caller checks).
    """
    progress = {} if progress is None else progress
    registry = load_specialists(repo)
    roots = validate_source_config(registry)["source_roots"]
    _write_json(evidence_dir / "registry.json", registry)
    catalog = {"workers": registry["workers"], "skills": registry["skills"]}
    if resume_from is not None:
        dispatch = validate_dispatch(resume_from["dispatch"], registry)
    else:
        dispatch = _run_json_contract_step(
            agent=SUPERVISOR, label="Code supervisor", step_id="dispatch-v1", repo=repo,
            evidence_dir=evidence_dir,
            prompt=prompt + "\nAllocate the approved work using this catalog:\n" + json.dumps(catalog) +
            '\nReturn {"tasks":[{"id":"T1","worker":"developer","plan_reference":"approved task reference",'
            '"instructions":"bounded assignment and interface contracts","depends_on":[],"skills":[],'
            '"owns":["<source-root>/path/or/folder"]}]}. '
            'Every owns path must be inside these configured source roots: ' + json.dumps(roots) + '. '
            "Cover every approved task, list dependencies before consumers, and use at most 16 tasks. "
            "Python runs tasks that do not depend on each other at the same time, each in an isolated copy of the "
            "repository, and merges them afterwards: declare depends_on only for real producer/consumer links, as in "
            "the plan's dependency table. List in owns the files or folders each task will change, and give each "
            "file to one task where possible; tasks whose owns overlap never run at the same time. "
            "Do not change source. Select applicable skills explicitly.",
            validator=lambda value: validate_dispatch(value, registry),
        )
    _write_json(evidence_dir / "dispatch.json", dispatch)
    schedule = build_schedule(dispatch["tasks"], max_parallel)
    _write_json(evidence_dir / "schedule.json", schedule)
    tasks = {task["id"]: task for task in dispatch["tasks"]}

    commands, used_skills = verification_plan(registry, dispatch["tasks"])

    results: list[dict[str, Any]] = []
    by_task: dict[str, dict[str, Any]] = {}
    progress.update(dispatch=dispatch, waves=schedule["waves"], tasks=results)
    for entry in (resume_from or {}).get("tasks", []):
        if entry["status"] in FINISHED_STATUSES and entry["task"] in tasks:
            results.append(entry)
            by_task[entry["task"]] = entry

    def run_worker(task: dict[str, Any], tree: Path | None, step_id: str, extra: str = "") -> dict[str, Any]:
        prior = [{"task": a, "result": by_task[a]["result"]} for a in schedule["ancestors"][task["id"]]]
        return _run_json_contract_step(
            agent=registry["workers"][task["worker"]]["profile"], label=f"Worker {task['id']}", step_id=step_id,
            repo=tree or repo, evidence_dir=evidence_dir if tree is None else tree / ".agentic-sdlc" / "runtime" / "answer",
            preserve_source=False, validator=completion_validator,
            prompt=_worker_prompt(prompt, task, prior, hybrid_skill_context(repo, registry, task["skills"]), tree) + extra,
        )

    def record(entry: dict[str, Any]) -> None:
        results.append(entry)
        by_task[entry["task"]] = entry
        _write_json(evidence_dir / "worker-results.json", results)

    def run_in_main(task: dict[str, Any], wave_no: int, step_id: str, extra: str = "", status: str = "committed") -> None:
        result = run_worker(task, None, step_id, extra)
        changed = changed_paths(repo, roots)
        sha = commit_task(repo, roots, _commit_message(ticket_id, task))
        record({"task": task["id"], "wave": wave_no, "workspace": "main", "status": status if sha else "no_changes",
                "commit": sha, "changed_paths": changed, "outside_owns": outside_owned(changed, task["owns"]),
                "result": result})

    prune_worktrees(repo)
    for wave_no, wave in enumerate(schedule["waves"], start=1):
        wave = [task_id for task_id in wave if task_id not in by_task]  # finished before a resume
        if not wave:
            continue
        if len(wave) == 1:
            run_in_main(tasks[wave[0]], wave_no, f"worker-{wave[0]}")
            continue
        trees: dict[str, Path] = {}
        branches = {task_id: f"sdlc-work/{run_id}/{task_id}" for task_id in wave}
        merged: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        cleanup_warnings: list[str] = []
        try:
            for task_id in wave:
                trees[task_id] = add_worktree(repo, evidence_dir.parent / "worktrees" / task_id, branches[task_id])
                check_trusted_files(repo, trees[task_id])
            base = head(repo)
            with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                futures = {task_id: pool.submit(run_worker, tasks[task_id], trees[task_id], f"worker-{task_id}")
                           for task_id in wave}
            outcomes: dict[str, dict[str, Any]] = {}
            failures: list[str] = []
            for task_id in wave:
                try:
                    outcomes[task_id] = futures[task_id].result()
                except Exception as exc:  # recorded, then the whole wave is stopped below (D10)
                    failures.append(f"Worker {task_id} failed: {exc}")
            if failures:
                _write_json(evidence_dir / f"wave-{wave_no}-failures.json", failures)
                raise WorkflowContractError("; ".join(failures))
            for task_id in wave:
                tree, task = trees[task_id], tasks[task_id]
                changed = changed_paths(tree, roots)
                sha = commit_task(tree, roots, _commit_message(ticket_id, task))
                patch = task_patch(tree, base) if sha else ""
                _write_text(evidence_dir / f"{task_id}.patch", patch)
                entry = {"task": task_id, "wave": wave_no, "workspace": "worktree", "commit": sha,
                         "changed_paths": changed, "outside_owns": outside_owned(changed, task["owns"]),
                         "result": outcomes[task_id]}
                _write_json(evidence_dir / f"{task_id}.ownership.json",
                            {"owns": task["owns"], "changed_paths": changed, "outside_owns": entry["outside_owns"]})
                if sha is None:
                    entry["status"] = "no_changes"
                elif cherry_pick(repo, sha):
                    entry["status"] = "merged"
                    entry["commit"] = head(repo)
                else:
                    entry["status"] = "conflict"
                    entry["conflict_patch"] = patch
                    conflicts.append(entry)
                    continue
                merged.append(entry)
        finally:
            for task_id, tree in trees.items():
                answers = tree / ".agentic-sdlc" / "runtime" / "answer"
                if answers.is_dir():
                    for item in answers.iterdir():
                        if item.is_file():
                            shutil.copy2(item, evidence_dir / item.name)
                cleanup_warnings.extend(remove_worktree(repo, tree, branches[task_id]))
            if cleanup_warnings:
                _write_text(evidence_dir / f"wave-{wave_no}.cleanup-warning.txt", "\n".join(cleanup_warnings))
        for entry in merged:
            record(entry)
        for entry in conflicts:
            record(entry)
            run_in_main(tasks[entry["task"]], wave_no, f"worker-{entry['task']}-rerun", status="rerun", extra=(
                "\nYour earlier attempt in an isolated copy conflicted with work merged since. Redo the task on top of "
                "the current source; this was your earlier change:\n" + entry["conflict_patch"]))

    context = hybrid_skill_context(repo, registry, used_skills)
    _write_text(evidence_dir / "required-skills.md", context)
    # A single existing implementer owns integration across all assignments.
    integrated = _run_json_contract_step(
        agent="sdlc_implementer", label="Integration", step_id="integrate-v1", repo=repo,
        evidence_dir=evidence_dir, preserve_source=False, validator=completion_validator,
        prompt=prompt + "\nIntegrate the completed assignments. Inspect actual source against EVERY approved task; "
        "resolve interface mismatches and missing approved work. Do not execute commands. "
        "Report completion for the whole feature, including unresolved deviations.\n" +
        json.dumps({"dispatch": dispatch, "schedule": schedule["waves"], "results": results}) + "\n" + context,
    )
    integration_sha = commit_task(repo, roots, f"[{ticket_id}] Integrate hybrid assignments")
    progress["integration"] = {"commit": integration_sha, "result": integrated}
    commits = [{"task": entry["task"], "commit": entry["commit"], "status": entry["status"]}
               for entry in results if entry["commit"] and entry["status"] != "conflict"]
    if integration_sha:
        commits.append({"task": "integration", "commit": integration_sha, "status": "committed"})

    combined = {key: [] for key in ("tasks_completed", "files_changed", "assumptions", "deviations")}
    for entry in results:
        if entry["status"] == "conflict":
            continue
        for key in combined:
            combined[key].extend(entry["result"][key])
        if entry["outside_owns"]:
            combined["deviations"].append(f"Task {entry['task']} changed paths outside owns: {', '.join(entry['outside_owns'])}")
    for key in combined:
        combined[key] = list(dict.fromkeys(combined[key] + integrated[key]))
    return combined, commands, context, commits
