"""Per-project provider and model for each agent profile, from the agent_settings key of agentic-sdlc-project.json.

Shape: {"default": {"provider": ..., "model": ...}, "profiles": {"<profile>": {"provider": ..., "model": ...}}}.
Every part is optional. A profile entry overrides the default field by field; a field set nowhere is "unset" and
the call runs as before (the default provider, no model sent, so Claude Code picks its own). Effort is deliberately
not configurable: it is provider-specific and some providers have none. Only providers whose write enforcement has
been verified are allowed; see agentic-sdlc-docs/plans/per-project-agent-settings.md and the "Change one profile"
section of agentic-sdlc-docs/reference/agent-profiles.md.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import WorkflowContractError
from .source_config import REGISTRY_RELATIVE_PATH, _read_optional, load_project_file, select_source_settings

DEFAULT_PROVIDER = "claude_code"
# A provider is added here only together with verified write enforcement for it.
ALLOWED_PROVIDERS = ("claude_code",)
# Every installed profile of this project; tests/test_agent_settings.py checks it against the profile files.
KNOWN_PROFILES = frozenset({
    "sdlc_code_supervisor",
    "sdlc_context_normalizer",
    "sdlc_implementer",
    "sdlc_plan_author",
    "sdlc_plan_reviewer",
    "sdlc_planning_analyst",
    "sdlc_pr_reviewer",
    "sdlc_remediator",
    "sdlc_source_correctness",
    "sdlc_source_feedback",
    "sdlc_source_fix_reviewer",
    "sdlc_source_followup_changes",
    "sdlc_source_followup_judge",
    "sdlc_source_mapper",
    "sdlc_source_security",
    "sdlc_source_standards",
    "sdlc_source_validator",
    "sdlc_standards_reviewer",
})
ENTRY_KEYS = ("provider", "model")
# Aliases (sonnet), ids (claude-opus-5-5, gpt-6-luna) and suffixes such as [1m]; no spaces or shell characters.
MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,99}")


def _validate_entry(raw: Any, label: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise WorkflowContractError(f"{label} must be an object with optional provider and model")
    unknown = sorted(set(raw) - set(ENTRY_KEYS))
    if unknown:
        raise WorkflowContractError(
            f"{label} has unknown keys: {', '.join(unknown)} (only provider and model can be set per profile)"
        )
    entry: dict[str, str] = {}
    if "provider" in raw:
        provider = raw["provider"]
        if provider not in ALLOWED_PROVIDERS:
            raise WorkflowContractError(
                f"{label}.provider {provider!r} is not allowed; allowed: {', '.join(ALLOWED_PROVIDERS)}. Another "
                "provider needs its own verified write enforcement first, see the \"Change one profile\" section "
                "of agentic-sdlc-docs/reference/agent-profiles.md"
            )
        entry["provider"] = provider
    if "model" in raw:
        model = raw["model"]
        if not isinstance(model, str) or not MODEL_PATTERN.fullmatch(model):
            raise WorkflowContractError(f"{label}.model must be a model name or id without spaces: {model!r}")
        entry["model"] = model
    return entry


def validate_agent_settings(raw: Any) -> dict[str, Any]:
    """Check the agent_settings object and return it normalized: {"default": {...}, "profiles": {...}}."""
    if not isinstance(raw, dict):
        raise WorkflowContractError("agent_settings must be an object with optional default and profiles")
    unknown = sorted(set(raw) - {"default", "profiles"})
    if unknown:
        raise WorkflowContractError(f"agent_settings has unknown keys: {', '.join(unknown)}")
    default = _validate_entry(raw.get("default", {}), "agent_settings.default")
    profiles_raw = raw.get("profiles", {})
    if not isinstance(profiles_raw, dict):
        raise WorkflowContractError("agent_settings.profiles must be an object keyed by profile name")
    profiles: dict[str, dict[str, str]] = {}
    for name in sorted(profiles_raw):
        if name not in KNOWN_PROFILES:
            raise WorkflowContractError(f"agent_settings.profiles names an unknown profile: {name!r}")
        profiles[name] = _validate_entry(profiles_raw[name], f"agent_settings.profiles.{name}")
    return {"default": default, "profiles": profiles}


def resolve_agent_settings(raw: Any) -> dict[str, dict[str, Any]]:
    """The effective provider and model of every known profile, and where each came from.

    raw is the agent_settings object, or None when the project declares none.
    """
    settings = validate_agent_settings(raw) if raw is not None else {"default": {}, "profiles": {}}
    resolved: dict[str, dict[str, Any]] = {}
    for name in sorted(KNOWN_PROFILES):
        own = settings["profiles"].get(name, {})
        entry: dict[str, Any] = {}
        for key, fallback in (("provider", DEFAULT_PROVIDER), ("model", None)):
            if key in own:
                entry[key], entry[f"{key}_from"] = own[key], "profile"
            elif key in settings["default"]:
                entry[key], entry[f"{key}_from"] = settings["default"][key], "default"
            else:
                entry[key], entry[f"{key}_from"] = fallback, "unset"
        resolved[name] = entry
    return resolved


def step_options(resolved: dict[str, dict[str, Any]] | None, agent: str) -> tuple[str, dict[str, str]]:
    """The provider and the extra step() options for one call; nothing extra when no model is set."""
    entry = (resolved or {}).get(agent)
    if entry is None:
        return DEFAULT_PROVIDER, {}
    return entry["provider"], ({"model": entry["model"]} if entry["model"] else {})


def load_agent_settings(repo: Path) -> dict[str, dict[str, Any]]:
    """Read agent_settings from the trusted checkout (project file, else the registry) and resolve it."""
    settings_source = select_source_settings(_read_optional(repo, REGISTRY_RELATIVE_PATH), load_project_file(repo))
    return resolve_agent_settings(settings_source.get("agent_settings") if isinstance(settings_source, dict) else None)
