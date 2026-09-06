"""Phase 3 — Remediation OS.

Turns a Phase 2 ``InvestigationResult`` into a *controlled* remediation decision:

    InvestigationResult
        -> ActionPlanner        (propose an allow-listed action, or nothing)
        -> RiskPolicyEngine     (deterministic AUTO_EXECUTE / ASK_HUMAN / ESCALATE / REJECT)
        -> Approval gate        (persisted human decision when required)
        -> ActionExecutor       (mock by default — NEVER mutates real infra)
        -> Verification          (telemetry says whether the incident actually recovered)
        -> Rollback              (when verification/execution fails and rollback is supported)
        -> ServiceNow write-back + Audit trail

CRITICAL INVARIANT: the LLM never controls infrastructure. It (or the deterministic
heuristic) only *proposes* a structured action; the deterministic policy engine
decides whether it is allowed, and only the validated executor can act.
"""
