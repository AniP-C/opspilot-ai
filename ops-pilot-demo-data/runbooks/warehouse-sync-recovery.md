# Warehouse Sync Recovery

## Purpose
Fix database replication lag in fulfillment centers.

## Applicable Services
svc-pg-wms

## Symptoms
replication_lag_seconds > 600. Read replicas stale.

## Preconditions
VPN access to warehouse subnet.

## Diagnostic Checks
1. Check network bandwidth between AZs. 2. Verify WAL shipping logs.

## Common Causes
Heavy batch inserts, inter-AZ packet loss.

## Recommended Actions
Throttle batch jobs. Failover read traffic to healthy AZ.

## Risks
Promoting a stale replica causes data split-brain.

## Verification
Replication lag returns to < 10 seconds.

## Escalation Criteria
Page Platform Infrastructure if replication breaks permanently.

