# Oms Db Connection Exhaustion

## Purpose
Mitigate database connection pool exhaustion for OMS.

## Applicable Services
svc-pg-oms, svc-oms

## Symptoms
New Relic alert: 'FATAL: sorry, too many clients already'.

## Preconditions
DB credentials available.

## Diagnostic Checks
1. Check pg_stat_activity. 2. Review PgBouncer metrics.

## Common Causes
Rogue analytical queries, traffic spikes without pool limits.

## Recommended Actions
Kill long-running queries via PID. Temporarily increase max_connections.

## Risks
Restarting OMS pods abruptly drops active checkout sessions.

## Verification
Connection usage percent drops below 80%.

## Escalation Criteria
Page Data Platform if DB remains locked.

