"""The controlled execution layer (abstraction).

Only an ``ActionExecutor`` may perform an action, and only after the policy /
approval gate upstream has cleared it. The core never runs shell commands and the
LLM never reaches this layer — an executor receives a fully-validated
``ExecutionRequest`` (a catalog action + allow-listed target) and returns a
structured ``ExecutionResult``.

``MockActionExecutor`` (the default) simulates the lifecycle and produces
deterministic post-action telemetry; it never mutates real infrastructure. A real
executor would implement the same interface but is disabled by two independent
safety flags (see ``Settings.execution_is_mock``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.domain.models import TelemetrySnapshot
from app.remediation.catalog import ActionDefinition
from app.remediation.models import ActionProposal, ExecutionResult


@dataclass
class ExecutionRequest:
    incident_id: str
    proposal: ActionProposal
    action: ActionDefinition
    idempotency_key: str
    action_ref: str = ""
    attempt: int = 1
    before: TelemetrySnapshot | None = None
    approval_verified: bool = False
    mode: str = "mock"
    # TEST/DEMO ONLY — force a non-happy path to exercise failure/rollback.
    # One of: None | "execution_failed" | "timeout" | "no_recovery".
    outcome_override: str | None = None


class ActionExecutor(ABC):
    """Interface every executor (mock or real) must satisfy."""

    @property
    @abstractmethod
    def mode(self) -> str:  # "mock" | "real"
        ...

    def validate(self, request: ExecutionRequest) -> list[str]:
        """Deterministic pre-flight checks. Returns a list of blocking reasons
        (empty == OK). Never raises for an expected precondition failure."""
        problems: list[str] = []
        if not request.action.allows_target(request.proposal.target_service):
            problems.append(
                f"target {request.proposal.target_service} not allow-listed for {request.action.action_id}"
            )
        if not request.approval_verified:
            problems.append("approval/authorisation not verified for this action")
        if self.mode == "mock" and not request.action.allowed_in_mock:
            problems.append(f"{request.action.action_id} is not permitted in mock mode")
        if self.mode == "real" and not request.action.real_execution_enabled:
            problems.append(
                f"real execution of {request.action.action_id} is disabled (safety flag off)"
            )
        return problems

    @abstractmethod
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        ...

    @abstractmethod
    def rollback(
        self, request: ExecutionRequest, execution: ExecutionResult
    ) -> ExecutionResult:
        ...
