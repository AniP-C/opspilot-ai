# Oms Order Processing

## Purpose
Resolve general order processing and creation delays.

## Applicable Services
svc-oms, svc-frontend

## Symptoms
Elevated checkout latency in New Relic. Frontend timeouts.

## Preconditions
Service must be running in production.

## Diagnostic Checks
1. Check New Relic APM. 2. Verify recent OMS deployments.

## Common Causes
Traffic spikes, deployment regressions.

## Recommended Actions
Check payload validation logs. Scale pods if CPU bound.

## Risks
Scaling may exhaust downstream connections.

## Verification
Monitor latency returning to < 200ms.

## Escalation Criteria
Page Order Platform if unresolved > 15m.

