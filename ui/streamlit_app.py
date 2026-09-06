"""OpsPilot — Ops Console (Streamlit).

Three tabs:
  * OPERATIONAL CONTEXT (Phase 1) — gathered context, no diagnosis.
  * INVESTIGATION (Phase 2) — ranked hypotheses, evidence, deployment
    correlation, topology reasoning, sufficiency + human-in-the-loop, and an
    advisory recommendation.
  * REMEDIATION (Phase 3) — the control plane: an allow-listed action proposal,
    the deterministic policy decision (AUTO_EXECUTE / ASK_HUMAN / ESCALATE /
    REJECT), a real approval gate, controlled (mock) execution, telemetry-based
    verification, rollback, ServiceNow write-back and the audit trail.

The system is an operational control plane, not a chatbot: the LLM never controls
infrastructure and nothing real is mutated in the default mock mode.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

DEFAULT_API = os.environ.get("API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="OpsPilot — Ops Console", layout="wide")

# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.title("OpsPilot")
    st.caption("AI Operations OS · Phases 1–3")
    api_base = st.text_input("API base URL", value=DEFAULT_API).rstrip("/")
    st.divider()
    st.markdown(
        "**Scope**\n\nPhase 1 gathers context. Phase 2 investigates and diagnoses. "
        "Phase 3 proposes a remediation, applies a deterministic risk/policy gate, "
        "and (mock) executes + verifies it under human approval. **No real "
        "infrastructure is mutated** — execution is mock/dry-run by default."
    )
    try:
        h = httpx.get(f"{api_base}/health", timeout=5.0).json()
        badge = "🟢" if h.get("status") == "ok" else "🟡"
        st.markdown(f"{badge} **Backend:** {h.get('status')}")
        st.caption(
            f"db: {h.get('database')} · chroma: {h.get('chroma')}\n\n"
            f"servicenow: {h.get('servicenow_mode')} · newrelic: {h.get('newrelic_mode')}\n\n"
            f"historical docs: {h.get('historical_documents')} · runbooks: {h.get('runbook_documents')}"
        )
    except Exception as exc:  # noqa: BLE001
        st.markdown("🔴 **Backend:** unreachable")
        st.caption(str(exc))


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _get(path: str):
    try:
        return httpx.get(f"{api_base}{path}", timeout=60.0)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not reach the API at {api_base}: {exc}")
        return None


def _post(path: str, json: dict | None = None):
    try:
        return httpx.post(f"{api_base}{path}", json=json, timeout=60.0)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not reach the API at {api_base}: {exc}")
        return None


def _sev_color(sev: str) -> str:
    return {"SEV1": "🔴", "SEV2": "🟠", "SEV3": "🟡", "SEV4": "🔵"}.get(sev, "⚪")


def _health_color(h: str) -> str:
    return {"healthy": "🟢", "degraded": "🟡", "critical": "🔴"}.get(h, "⚪")


def _corr_color(level: str) -> str:
    return {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡", "NONE": "⚪"}.get(level, "⚪")


# --------------------------------------------------------------------------- #
# Header / input
# --------------------------------------------------------------------------- #
st.title("OpsPilot Console")
col_a, col_b, col_c, col_d = st.columns([3, 1, 1, 1])
with col_a:
    incident_id = st.text_input("Incident ID", value="DEMO-001", label_visibility="collapsed")
with col_b:
    do_context = st.button("Build Context", use_container_width=True)
with col_c:
    do_investigate = st.button("Run Investigation", use_container_width=True)
with col_d:
    do_remediate = st.button("Plan Remediation", type="primary", use_container_width=True)

state = st.session_state


def _decision_badge(decision: str) -> str:
    return {
        "auto_execute": "🟢 AUTO-EXECUTE ELIGIBLE",
        "ask_human": "🟠 APPROVAL REQUIRED",
        "escalate": "🔴 ESCALATE",
        "reject": "⛔ REJECTED BY POLICY",
    }.get(decision, decision.upper())


def _risk_color(risk: str) -> str:
    return {"low": "🟢", "medium": "🟠", "high": "🔴", "critical": "🟣"}.get((risk or "").lower(), "⚪")


def _status_color(status: str) -> str:
    good = {"verified"}
    warn = {"awaiting_approval", "auto_approved", "approved", "executing", "executed", "verifying"}
    bad = {"execution_failed", "verification_failed", "rejected", "rejected_by_policy", "rollback_failed"}
    if status in good:
        return "🟢"
    if status == "rolled_back":
        return "🟡"
    if status == "escalated":
        return "🔴"
    if status in bad:
        return "🔴"
    if status in warn:
        return "🟠"
    return "⚪"


# --------------------------------------------------------------------------- #
# Phase 1 rendering
# --------------------------------------------------------------------------- #
def render_context(ctx: dict) -> None:
    inc = ctx["incident"]
    st.subheader("1 · Incident")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Incident", inc["id"])
    c2.metric("Service", inc["service"])
    c3.metric("Severity", f"{_sev_color(inc['severity'])} {inc['severity']}")
    c4.metric("Status", inc["status"])
    st.markdown(f"**{inc['title']}**")
    st.write(inc.get("description", ""))
    if inc.get("symptoms"):
        st.caption("Symptoms: " + ", ".join(inc["symptoms"]))

    st.subheader("2 · Affected Service")
    svc = ctx.get("affected_service")
    if svc:
        s1, s2, s3 = st.columns(3)
        s1.metric("Name", svc["name"])
        s2.metric("Owner", svc["owner"])
        s3.metric("Criticality", svc["criticality"])
    else:
        st.info("Affected service not found in the topology store.")

    st.subheader("3 · Dependency Topology")
    topo = ctx["dependency_path"]
    if topo["direct_dependencies"]:
        st.dataframe(
            [
                {"target": d["target_service"], "relationship": d["relationship"],
                 "protocol": d["protocol"], "criticality": d["criticality"]}
                for d in topo["direct_dependencies"]
            ],
            use_container_width=True, hide_index=True,
        )
    t1, t2 = st.columns(2)
    t1.markdown("**Direct upstream** (services that directly depend on this one)")
    t1.write(", ".join(topo.get("direct_upstream", [])) or "—")
    t2.markdown("**Direct downstream** (services this one directly depends on)")
    t2.write(", ".join(topo.get("direct_downstream", [])) or "—")
    st.caption("Dependency chain (transitive/recursive): " +
               (" → ".join([topo["service"], *topo.get("dependency_chain", [])])
                if topo.get("dependency_chain") else "—"))

    st.subheader("4 · Telemetry")
    if ctx["telemetry"]:
        st.dataframe(
            [
                {"service": t["service"], "health": f"{_health_color(t['health_status'])} {t['health_status']}",
                 "error_rate_%": t["error_rate"], "latency_ms": t["latency_ms"],
                 "throughput": t["throughput"], "timestamp": t.get("timestamp")}
                for t in ctx["telemetry"]
            ],
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("No telemetry available.")

    st.subheader("5 · Recent Deployments")
    st.caption("Within the window before the incident. Phase 1 makes no causation inference.")
    if ctx["recent_deployments"]:
        st.dataframe(
            [
                {"deployment": d["id"], "version": d["version"], "status": d["status"],
                 "deployed_at": d.get("deployed_at"), "changed": ", ".join(d.get("changed_components", []))}
                for d in ctx["recent_deployments"]
            ],
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("No deployments in the window.")

    st.subheader("6 · Similar Historical Incidents")
    for ev in ctx["similar_incidents"]:
        m = ev["metadata"]
        with st.expander(f"{m.get('incident_id')} · {ev['title']} (relevance {m.get('score')})"):
            st.caption(f"Service: {m.get('service')} · Fingerprint: {m.get('incident_fingerprint')}")
            st.markdown(f"**Recorded root cause (historical):** {m.get('root_cause')}")
            st.markdown(f"**Recorded resolution (historical):** {m.get('resolution')}")

    st.subheader("7 · Relevant Runbooks")
    for ev in ctx["relevant_runbooks"]:
        m = ev["metadata"]
        with st.expander(f"{m.get('runbook_id')} · {ev['title']} (relevance {m.get('score')})"):
            st.markdown(ev.get("content", ""))

    st.subheader("8 · Missing Information")
    if ctx["missing_information"]:
        for mi in ctx["missing_information"]:
            st.warning(f"**{mi['source']}** — {mi['detail']}")
    else:
        st.success("No gaps detected — all context sources returned data.")


# --------------------------------------------------------------------------- #
# Phase 2 rendering
# --------------------------------------------------------------------------- #
def render_investigation(inv: dict) -> None:
    status = inv["status"]
    if status == "diagnosis_ready":
        st.success("Status: DIAGNOSIS READY")
    else:
        st.warning("Status: INSUFFICIENT EVIDENCE — more information needed")

    # Diagnosis
    st.subheader("Diagnosis")
    d1, d2 = st.columns([3, 1])
    d1.markdown(f"**Primary hypothesis:** {inv.get('primary_hypothesis') or '—'}")
    d2.metric("Confidence", f"{round((inv.get('confidence_score') or 0) * 100)}%")

    sup = inv.get("supporting_evidence", [])
    con = inv.get("contradicting_evidence", [])
    if sup:
        st.markdown("**Supporting evidence**")
        for e in sup:
            tag = "fact" if e["kind"] == "fact" else "inference"
            st.markdown(f"- ✓ `{e['id']}` [{tag}] {e['finding']}")
    if con:
        st.markdown("**Contradicting evidence**")
        for e in con:
            st.markdown(f"- ✗ `{e['id']}` {e['finding']}")
    if not con:
        st.caption("Contradicting evidence: none found for the primary hypothesis.")

    # Deployment correlation
    st.subheader("Deployment Correlation")
    dc = inv.get("deployment_correlation") or {}
    lvl = dc.get("level", "NONE")
    st.markdown(f"{_corr_color(lvl)} **{lvl}** — {dc.get('rationale', 'n/a')}")

    # Alternatives
    st.subheader("Alternative Hypotheses")
    for h in inv.get("alternative_hypotheses", []):
        st.markdown(
            f"- **{h['statement']}** — {round(h['confidence_score'] * 100)}% "
            f"(implicates `{h.get('implicated_service')}`)"
        )

    # Topology + historical
    with st.expander("Topology findings"):
        for tf in inv.get("topology_findings", []):
            st.markdown(f"- {tf['description']}")
    with st.expander("Historical findings (evidence, not proof)"):
        for e in inv.get("historical_findings", []):
            st.markdown(f"- `{e['id']}` {e['finding']}")

    # Missing information + human-in-the-loop
    if inv.get("missing_information") or inv.get("human_questions"):
        st.subheader("Missing Information")
        for mi in inv.get("missing_information", []):
            st.warning(f"**{mi['topic']}** — {mi['detail']}")
        if inv.get("human_questions"):
            st.markdown("**Questions for the operator:**")
            for q in inv["human_questions"]:
                st.markdown(f"- {q['question']}")
        with st.form("additional_info_form", clear_on_submit=True):
            info = st.text_area("Provide additional information")
            submitted = st.form_submit_button("Submit additional information")
        if submitted and info.strip():
            resp = _post(
                f"/api/investigations/{inv['investigation_id']}/additional-info",
                json={"information": info.strip()},
            )
            if resp is not None and resp.status_code == 200:
                state["inv"] = resp.json()
                st.rerun()

    # Recommendation (advisory only)
    st.subheader("Recommendation")
    rec = inv.get("recommendation") or {}
    st.markdown(f"**Recommended next action:** {inv.get('recommended_next_action') or '—'}")
    st.caption(rec.get("rationale", ""))
    r1, r2 = st.columns(2)
    r1.metric("Risk", (rec.get("risk_level") or "—").upper())
    r2.metric("Human approval", "REQUIRED" if inv.get("human_approval_required") else "no")
    for c in rec.get("caveats", []):
        st.caption(f"• {c}")
    st.info("Execution: **NOT AVAILABLE IN PHASE 2** — remediation requires Phase 3 risk/approval controls.")

    with st.expander("All ranked hypotheses"):
        for h in inv.get("hypotheses", []):
            st.markdown(
                f"- `{h['id']}` **{h['kind']}** → {h.get('implicated_service')} · "
                f"support={h['support_score']} · confidence={h['confidence_score']} · "
                f"for={h['evidence_for']} against={h['evidence_against']}"
            )
    with st.expander("Investigation audit trail"):
        for line in inv.get("audit", []):
            st.markdown(f"- {line}")


# --------------------------------------------------------------------------- #
# Phase 3 rendering — the remediation control plane
# --------------------------------------------------------------------------- #
def _refresh_remediation(inc_id: str) -> None:
    r = _get(f"/api/incidents/{inc_id}/remediation")
    if r is not None and r.status_code == 200:
        state["rem"] = r.json()


def render_remediation(view: dict) -> None:
    rec = view.get("record")
    if not rec:
        st.caption(
            "Plan a remediation to see the proposed action, the deterministic policy "
            "decision, the approval gate, controlled execution and verification."
        )
        return

    status = rec["status"]
    policy = rec.get("policy") or {}
    proposal = rec.get("proposal")
    decision = (policy.get("decision") or "").lower()

    # 1 · Decision header
    st.subheader("Remediation Decision")
    h1, h2, h3 = st.columns([2, 1, 1])
    h1.markdown(f"**Diagnosis:** {view.get('diagnosis') or '—'}")
    h2.metric("Confidence", f"{round((view.get('confidence') or 0) * 100)}%")
    h3.metric("Lifecycle", f"{_status_color(status)} {status.replace('_', ' ').upper()}")
    st.markdown(f"### {_decision_badge(decision)}")
    with st.expander("Why this decision (deterministic policy reasons)", expanded=True):
        for reason in policy.get("reasons", []):
            st.markdown(f"- {reason}")

    # 2 · Proposed action
    st.subheader("Proposed Action")
    if not proposal:
        st.error(
            "No safe, allow-listed automated remediation exists for this diagnosis — "
            "the incident is escalated to a human incident commander."
        )
    else:
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Action", proposal["action_id"])
        p2.metric("Target", proposal["target_service"])
        p3.metric("Risk", f"{_risk_color(proposal['risk_level'])} {proposal['risk_level'].upper()}")
        p4.metric("Blast radius", proposal["blast_radius"].replace("_", " "))
        st.markdown(f"**Rationale:** {proposal.get('rationale', '')}")
        st.caption(f"Expected effect: {proposal.get('expected_effect', '')}")
        e1, e2, e3 = st.columns(3)
        total = proposal.get("historical_total", 0)
        succ = proposal.get("historical_success", 0)
        e1.metric("Recurrence", proposal.get("recurrence_count", 0))
        e2.metric("Historical success", f"{succ}/{total}" if total else "no precedent")
        e3.metric("Rollback", "supported" if proposal.get("rollback_supported") else "n/a")
        if proposal.get("evidence_ids"):
            st.caption("Grounded in evidence: " + ", ".join(f"`{e}`" for e in proposal["evidence_ids"]))
        vp = proposal.get("verification_plan") or {}
        if vp.get("metrics"):
            with st.expander("Verification plan (recovery criteria)"):
                st.dataframe(
                    [
                        {"metric": m["metric"], "direction": m["direction"],
                         "threshold": m.get("threshold"), "source": m["source"]}
                        for m in vp["metrics"]
                    ],
                    use_container_width=True, hide_index=True,
                )

    # 3 · Approval gate
    ref = rec["action_ref"]
    if status == "awaiting_approval":
        st.subheader("Approval Required")
        st.warning(
            f"Production impact + {proposal['risk_level'].upper()}-risk action — "
            "human approval required before execution."
        )
        a1, a2, a3 = st.columns(3)
        if a1.button("✅ APPROVE", type="primary", use_container_width=True, key="approve"):
            resp = _post(f"/api/actions/{ref}/approve", json={"approver": "operator", "note": "approved via console"})
            if resp is not None and resp.status_code == 200:
                state["rem"] = resp.json(); st.rerun()
        if a2.button("❌ REJECT", use_container_width=True, key="reject"):
            resp = _post(f"/api/actions/{ref}/reject", json={"approver": "operator", "note": "rejected via console"})
            if resp is not None and resp.status_code == 200:
                state["rem"] = resp.json(); st.rerun()
        with a3.popover("🔎 INVESTIGATE FURTHER", use_container_width=True):
            with st.form("investigate_further", clear_on_submit=True):
                info = st.text_area("Additional information for the investigation")
                if st.form_submit_button("Submit") and info.strip():
                    resp = _post(f"/api/actions/{ref}/investigate", json={"information": info.strip()})
                    if resp is not None and resp.status_code == 200:
                        state["rem"] = resp.json(); st.rerun()
    elif status == "escalated":
        st.info("⏫ Escalated to a human incident commander — no automated action will run.")
    elif status == "rejected":
        st.error("Action rejected by the operator. No execution performed.")

    # 4 · Execution
    execution = rec.get("execution")
    if execution:
        st.subheader("Execution")
        st.markdown(
            f"**Executor:** `{execution['mode']}` · **Status:** "
            f"{execution['status'].upper()} — {execution.get('message', '')}"
        )
        st.caption("Lifecycle:")
        for step in execution.get("lifecycle", []):
            st.markdown(f"- {step}")
        if execution.get("rolled_back") or execution.get("rollback_lifecycle"):
            st.markdown("**Rollback:**")
            for step in execution.get("rollback_lifecycle", []):
                st.markdown(f"- ↩︎ {step}")

    # 5 · Verification
    verification = rec.get("verification")
    if verification:
        st.subheader("Verification")
        vstatus = verification["status"]
        icon = {"passed": "🟢", "failed": "🔴", "inconclusive": "🟡", "skipped": "⚪"}.get(vstatus, "⚪")
        st.markdown(f"{icon} **{vstatus.upper()}** — {verification.get('reason', '')}")
        v1, v2 = st.columns(2)
        v1.metric("Health before", verification.get("before_health") or "—")
        v2.metric("Health after", verification.get("after_health") or "—")
        if verification.get("observations"):
            st.dataframe(
                [
                    {"metric": o["metric"], "before": o["before"], "after": o["after"],
                     "met": "✓" if o["met"] else "✗", "criterion": o["expectation"]}
                    for o in verification["observations"]
                ],
                use_container_width=True, hide_index=True,
            )

    # 6 · Outcome + ServiceNow
    st.subheader("Outcome")
    o1, o2 = st.columns(2)
    if status == "verified":
        o1.success("INCIDENT RECOVERED — verified from telemetry.")
    elif status == "rolled_back":
        o1.warning("Action did not recover the incident — rolled back to the pre-action state.")
    elif status in ("execution_failed", "verification_failed", "rollback_failed"):
        o1.error(f"Remediation unsuccessful ({status.replace('_', ' ')}).")
    else:
        o1.info(f"Current state: {status.replace('_', ' ')}.")
    o2.metric("ServiceNow", "updated ✅" if rec.get("servicenow_updated") else "not updated")

    # 7 · Audit trail
    st.subheader("Audit Trail")
    for e in view.get("audit", []):
        ts = (e.get("at") or "")[:19].replace("T", " ")
        st.markdown(f"- `{ts}` **{e['event_type']}** — {e['summary']}  _(by {e.get('actor', 'system')})_")


# --------------------------------------------------------------------------- #
# Actions -> session state
# --------------------------------------------------------------------------- #
if do_context and incident_id.strip():
    with st.spinner(f"Building operational context for {incident_id}…"):
        r = _get(f"/api/incidents/{incident_id.strip()}/context")
    if r is not None and r.status_code == 200:
        state["ctx"] = r.json()
        state["active"] = incident_id.strip()
    elif r is not None and r.status_code == 404:
        st.warning(f"Incident '{incident_id}' not found.")
    elif r is not None:
        st.error(f"Error {r.status_code}: {r.text}")

if do_investigate and incident_id.strip():
    with st.spinner(f"Investigating {incident_id}…"):
        r = _post(f"/api/incidents/{incident_id.strip()}/investigate")
    if r is not None and r.status_code == 200:
        state["inv"] = r.json()
        state["active"] = incident_id.strip()
        # fetch context too for the context tab, if not already present
        cr = _get(f"/api/incidents/{incident_id.strip()}/context")
        if cr is not None and cr.status_code == 200:
            state["ctx"] = cr.json()
    elif r is not None and r.status_code == 404:
        st.warning(f"Incident '{incident_id}' not found.")
    elif r is not None:
        st.error(f"Error {r.status_code}: {r.text}")

if do_remediate and incident_id.strip():
    with st.spinner(f"Planning remediation for {incident_id} (investigate → policy → safe action)…"):
        r = _post(f"/api/incidents/{incident_id.strip()}/remediation/plan", json={})
    if r is not None and r.status_code == 200:
        state["rem"] = r.json()
        state["active"] = incident_id.strip()
        # fetch context + investigation for the other tabs too
        cr = _get(f"/api/incidents/{incident_id.strip()}/context")
        if cr is not None and cr.status_code == 200:
            state["ctx"] = cr.json()
        ir = _post(f"/api/incidents/{incident_id.strip()}/investigate")
        if ir is not None and ir.status_code == 200:
            state["inv"] = ir.json()
    elif r is not None and r.status_code == 404:
        st.warning(f"Incident '{incident_id}' not found.")
    elif r is not None:
        st.error(f"Error {r.status_code}: {r.text}")

tab_ctx, tab_inv, tab_rem = st.tabs(
    [
        "🗂 Operational Context (Phase 1)",
        "🔎 Investigation (Phase 2)",
        "🛡 Remediation (Phase 3)",
    ]
)
with tab_ctx:
    if state.get("ctx"):
        render_context(state["ctx"])
    else:
        st.caption("Build the operational context to see incident, topology, telemetry, "
                   "deployments, similar incidents and runbooks.")
with tab_inv:
    if state.get("inv"):
        render_investigation(state["inv"])
    else:
        st.caption("Run an investigation to see ranked hypotheses, evidence, deployment "
                   "correlation, a sufficiency check, and an advisory recommendation.")
with tab_rem:
    if state.get("rem"):
        render_remediation(state["rem"])
    else:
        st.caption("Plan a remediation to see the proposed action, the deterministic "
                   "policy decision, the approval gate, controlled (mock) execution, "
                   "telemetry verification, rollback and the audit trail.")
