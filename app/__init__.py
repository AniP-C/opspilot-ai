"""OpsPilot AI — Phase 1: Ops Context Engine.

Given an incident ID, gather operational context (incident details, service
topology, telemetry, recent deployments, similar historical incidents, relevant
runbooks) and return a unified ``IncidentContext``.

Phase 1 explicitly does NOT diagnose, recommend, or execute remediation.
"""

__version__ = "0.1.0"
