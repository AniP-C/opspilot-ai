"""The allow-listed action catalog.

This is the *only* set of actions the system can ever propose or execute. There
is no free-form / LLM-generated command path: the planner may only select an
``action_id`` that exists here, targeting a service that is explicitly
allow-listed for that action. The deterministic policy engine then decides
whether the selected action is permitted for the situation.

Each definition also carries the metadata the rest of Phase 3 needs: risk, blast
radius, rollback support, idempotency, a verification-plan template, and the
mock/real execution flags (real execution is OFF for every action by default).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.remediation.models import (
    ActionRisk,
    BlastRadius,
    MetricDirection,
    MetricExpectation,
    VerificationPlan,
)


@dataclass(frozen=True)
class ActionDefinition:
    action_id: str
    name: str
    description: str
    target_type: str                       # service | deployment | consumer | cache
    allowed_targets: tuple[str, ...]        # allow-list of service ids (never "*")
    risk_level: ActionRisk
    blast_radius: BlastRadius
    required_permissions: tuple[str, ...]
    approval_policy: str                    # advisory hint; the policy engine is authoritative
    preconditions: tuple[str, ...]
    verification_metrics: tuple[MetricExpectation, ...]
    verification_window_seconds: int = 300
    timeout_seconds: int = 600
    retry_policy: dict = field(default_factory=lambda: {"max_attempts": 1, "backoff_seconds": 0})
    rollback_supported: bool = False
    rollback_action_id: str | None = None
    idempotent: bool = True
    allowed_in_mock: bool = True
    real_execution_enabled: bool = False    # HARD default: never real without explicit opt-in

    def allows_target(self, service: str) -> bool:
        return service in self.allowed_targets

    def build_verification_plan(
        self, target_service: str, *, window_seconds: int | None = None
    ) -> VerificationPlan:
        return VerificationPlan(
            target_service=target_service,
            metrics=list(self.verification_metrics),
            require_health_recovered=True,
            observation_window_seconds=window_seconds or self.verification_window_seconds,
            timeout_seconds=self.timeout_seconds,
            success_criteria=(
                "Error rate and health recover to nominal within the observation window; "
                "no metric regresses."
            ),
        )


# Reusable verification templates ------------------------------------------- #
def _error_and_health_recovered(error_threshold: float = 2.0) -> tuple[MetricExpectation, ...]:
    return (
        MetricExpectation(
            metric="error_rate",
            direction=MetricDirection.BELOW,
            threshold=error_threshold,
            source="telemetry",
            description=f"error rate returns below {error_threshold}%",
        ),
        MetricExpectation(
            metric="error_rate",
            direction=MetricDirection.DECREASE,
            source="telemetry",
            description="error rate strictly decreases vs. the incident reading",
        ),
    )


# --------------------------------------------------------------------------- #
# THE CATALOG
# --------------------------------------------------------------------------- #
_ACTIONS: dict[str, ActionDefinition] = {
    "restart_service": ActionDefinition(
        action_id="restart_service",
        name="Restart service",
        description=(
            "Rolling restart of a stateless application service to drop stuck "
            "connections / reset in-process state (e.g. relieve OMS connection-pool "
            "exhaustion)."
        ),
        target_type="service",
        # Stateful stores (databases, kafka) and the revenue-critical payment
        # gateway are deliberately NOT restartable via this action.
        allowed_targets=("svc-oms", "svc-wms", "svc-frontend", "svc-inventory"),
        risk_level=ActionRisk.LOW,
        blast_radius=BlastRadius.SINGLE_SERVICE,
        required_permissions=("ops:service:restart",),
        approval_policy="auto_eligible",
        preconditions=("target is allow-listed", "rolling restart supported"),
        verification_metrics=_error_and_health_recovered(2.0)
        + (
            MetricExpectation(
                metric="connection_usage_percent",
                direction=MetricDirection.BELOW,
                threshold=85.0,
                source="metadata",
                description="DB connection utilisation returns below 85%",
            ),
        ),
        rollback_supported=False,      # a restart cannot be "un-restarted"; recovery is verified instead
        idempotent=True,
    ),
    "restart_consumer": ActionDefinition(
        action_id="restart_consumer",
        name="Restart message consumer",
        description=(
            "Restart a Kafka consumer group to resume processing after a stall / "
            "rebalance (e.g. WMS not consuming order events)."
        ),
        target_type="consumer",
        allowed_targets=("svc-wms",),
        risk_level=ActionRisk.LOW,
        blast_radius=BlastRadius.SINGLE_SERVICE,
        required_permissions=("ops:consumer:restart",),
        approval_policy="auto_eligible",
        preconditions=("consumer group known", "no in-flight rebalance"),
        verification_metrics=(
            MetricExpectation(
                metric="partition_lag",
                direction=MetricDirection.BELOW,
                threshold=1000.0,
                source="metadata",
                description="consumer lag drains below 1000",
            ),
            MetricExpectation(
                metric="error_rate",
                direction=MetricDirection.BELOW,
                threshold=2.0,
                source="telemetry",
                description="error rate returns below 2%",
            ),
        ),
        rollback_supported=False,
        idempotent=True,
    ),
    "clear_cache": ActionDefinition(
        action_id="clear_cache",
        name="Clear / reconfigure cache",
        description=(
            "Adjust eviction policy or flush a cache tier under memory pressure "
            "(e.g. Redis OOM). Loses cached sessions — not auto-eligible."
        ),
        target_type="cache",
        allowed_targets=("svc-redis",),
        risk_level=ActionRisk.MEDIUM,
        blast_radius=BlastRadius.DEPENDENTS,
        required_permissions=("ops:cache:flush",),
        approval_policy="human_required",
        preconditions=("cache tier identified", "downstream sessions can be re-established"),
        verification_metrics=(
            MetricExpectation(
                metric="memory_utilization",
                direction=MetricDirection.BELOW,
                threshold=90.0,
                source="metadata",
                description="cache memory utilisation returns below 90%",
            ),
            MetricExpectation(
                metric="error_rate",
                direction=MetricDirection.BELOW,
                threshold=2.0,
                source="telemetry",
                description="error rate returns below 2%",
            ),
        ),
        rollback_supported=False,
        idempotent=True,
    ),
    "scale_service": ActionDefinition(
        action_id="scale_service",
        name="Scale service capacity",
        description="Horizontally scale a service (add replicas / raise connection limits).",
        target_type="service",
        allowed_targets=("svc-oms", "svc-wms", "svc-frontend", "svc-inventory"),
        risk_level=ActionRisk.MEDIUM,
        blast_radius=BlastRadius.SINGLE_SERVICE,
        required_permissions=("ops:service:scale",),
        approval_policy="human_required",
        preconditions=("capacity headroom available",),
        verification_metrics=_error_and_health_recovered(2.0),
        rollback_supported=True,
        rollback_action_id="scale_service",   # scale back down
        idempotent=False,
    ),
    "rollback_deployment": ActionDefinition(
        action_id="rollback_deployment",
        name="Roll back deployment",
        description=(
            "Redeploy the previous known-good version of a service after a "
            "deployment regression. Requires a correlated recent deployment."
        ),
        target_type="deployment",
        allowed_targets=(
            "svc-oms",
            "svc-wms",
            "svc-frontend",
            "svc-inventory",
            "svc-payment",
        ),
        risk_level=ActionRisk.MEDIUM,
        blast_radius=BlastRadius.DEPENDENTS,
        required_permissions=("ci:deploy:rollback",),
        approval_policy="human_required",
        preconditions=(
            "a specific recent deployment is correlated with the incident",
            "a previous known-good version exists",
        ),
        verification_metrics=_error_and_health_recovered(2.0),
        rollback_supported=True,
        rollback_action_id="rollback_deployment",  # re-apply the newer version if needed
        idempotent=True,
    ),
    "failover_payment": ActionDefinition(
        action_id="failover_payment",
        name="Payment failover / circuit breaker",
        description=(
            "Trip the payment circuit breaker and fail over to the backup provider. "
            "Directly affects live revenue — never auto-executed."
        ),
        target_type="service",
        allowed_targets=("svc-payment",),
        risk_level=ActionRisk.HIGH,
        blast_radius=BlastRadius.NEIGHBORHOOD,
        required_permissions=("ops:payment:failover", "oncall:payments"),
        approval_policy="escalate_only",
        preconditions=("backup provider healthy", "reconciliation plan ready"),
        verification_metrics=(
            MetricExpectation(
                metric="error_rate",
                direction=MetricDirection.BELOW,
                threshold=2.0,
                source="telemetry",
                description="payment error rate returns below 2%",
            ),
        ),
        rollback_supported=True,
        rollback_action_id="failover_payment",  # fail back to primary
        idempotent=False,
    ),
    "freeze_writes": ActionDefinition(
        action_id="freeze_writes",
        name="Freeze writes (data-integrity hold)",
        description=(
            "Halt writes on a data path suspected of corruption (e.g. negative order "
            "totals) to stop the bleeding pending human investigation. Platform-wide "
            "impact — escalate only."
        ),
        target_type="service",
        allowed_targets=("svc-oms", "svc-pg-oms", "svc-pg-wms"),
        risk_level=ActionRisk.CRITICAL,
        blast_radius=BlastRadius.GLOBAL,
        required_permissions=("ops:data:freeze", "oncall:ic", "oncall:data"),
        approval_policy="escalate_only",
        preconditions=("incident commander engaged", "customer-comms plan ready"),
        verification_metrics=(
            MetricExpectation(
                metric="anomalous_data_inserts",
                direction=MetricDirection.BELOW,
                threshold=1.0,
                source="metadata",
                description="anomalous data inserts stop",
            ),
        ),
        rollback_supported=True,
        rollback_action_id="freeze_writes",   # unfreeze
        idempotent=True,
    ),
}


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
def get_action(action_id: str) -> ActionDefinition | None:
    return _ACTIONS.get(action_id)


def all_actions() -> list[ActionDefinition]:
    return list(_ACTIONS.values())


def action_ids() -> list[str]:
    return list(_ACTIONS.keys())


def is_target_allowed(action_id: str, service: str) -> bool:
    action = _ACTIONS.get(action_id)
    return bool(action and action.allows_target(service))
