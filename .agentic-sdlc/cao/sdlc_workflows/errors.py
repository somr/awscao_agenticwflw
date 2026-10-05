"""Shared workflow contract and execution errors."""

class WorkflowContractError(ValueError):
    """Raised when deterministic validation rejects workflow data."""


class IncompleteAgentExecutionError(WorkflowContractError):
    """Raised when CAO returns before the agent has produced a final response."""


class NoSourceChangesError(WorkflowContractError):
    """Raised when an implementation step finished but left no change under the source roots."""
