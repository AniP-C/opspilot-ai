# Redis Recovery

## Purpose
Recover from cache evictions and Redis OOM events.

## Applicable Services
svc-redis

## Symptoms
Users logged out. OOM kills in New Relic. 'Connection reset by peer'.

## Preconditions
AWS ElastiCache console access.

## Diagnostic Checks
1. Check memory_utilization metrics. 2. Verify maxmemory-policy.

## Common Causes
Policy accidentally set to noeviction. Huge cart payloads.

## Recommended Actions
Update eviction policy to volatile-lru. Scale instance size.

## Risks
Flushing cache deletes all active user sessions.

## Verification
Memory utilization stabilizes below 80%.

## Escalation Criteria
Page Infrastructure if node fails to recover.

