# Service Health Check

## Purpose
Baseline health checks for any microservice.

## Applicable Services
All

## Symptoms
Degraded state in New Relic.

## Preconditions
None.

## Diagnostic Checks
1. Verify RED metrics. 2. Check /healthz endpoints.

## Common Causes
Pod eviction, node failure.

## Recommended Actions
Restart pods. Verify readiness probes.

## Risks
None.

## Verification
Service status green.

## Escalation Criteria
Page service owner if pods crashloop.

