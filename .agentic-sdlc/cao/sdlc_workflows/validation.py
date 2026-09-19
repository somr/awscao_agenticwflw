"""Small structural validators shared by planning and delivery."""
from __future__ import annotations
import re
from typing import Any
from .errors import WorkflowContractError

def _safe_component(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise WorkflowContractError(
            f"{label} must contain only letters, digits, '.', '_' or '-': {value!r}"
        )
    return value


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
