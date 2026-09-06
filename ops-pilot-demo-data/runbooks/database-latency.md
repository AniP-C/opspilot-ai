# Database Latency

## Purpose
Troubleshoot general database latency and high IOPS.

## Applicable Services
svc-pg-oms, svc-pg-wms

## Symptoms
Query latency > 500ms. CPU > 90%.

## Preconditions
Read access to pg_stat_statements.

## Diagnostic Checks
1. Find slow queries. 2. Check for missing indexes.

## Common Causes
Missing indexes, table bloat (autovacuum stuck).

## Recommended Actions
Run manual VACUUM ANALYZE. Add concurrent index.

## Risks
VACUUM FULL locks tables completely (do not run).

## Verification
Query latency drops back to baseline (< 50ms).

## Escalation Criteria
Page DBA if latency impacts checkout.

