"""Shared remediator completion contract; workflow policy stays with the caller."""
from __future__ import annotations
from typing import Any
from .validation import _require_dict, _require_list
from .errors import WorkflowContractError

def _remediator_completion_validator(value: Any) -> Any:
    value = _require_dict(value, "remediator completion summary")
    for key in ("findings_addressed", "files_changed", "assumptions", "deviations"):
        items = _require_list(value.get(key), key)
        for index, item in enumerate(items):
            if not isinstance(item, str):
                raise WorkflowContractError(f"{key}[{index}] must be a string")
    return value
