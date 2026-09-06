"""Phase 2 — Investigation Engine.

Consumes a Phase 1 ``IncidentContext`` and produces an ``InvestigationResult``:
multiple ranked hypotheses, each grounded in traceable evidence, a deliberate
deployment-correlation judgement, topology-aware reasoning, a sufficiency check
with human-in-the-loop follow-ups, and an advisory recommendation.

It performs NO remediation and mutates NO production systems — execution and
approval belong to Phase 3.
"""
