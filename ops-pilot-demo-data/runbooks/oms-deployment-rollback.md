# Oms Deployment Rollback

## Purpose
Provide safely rollback procedures for OMS API regressions.

## Applicable Services
svc-oms

## Symptoms
Immediate error rate spike (>5%) post-deploy.

## Preconditions
kubectl access to production cluster.

## Diagnostic Checks
1. Compare current Git SHA with previous stable. 2. Check deployment history.

## Common Causes
Bad logic, unindexed queries in new code.

## Recommended Actions
Execute `kubectl rollout undo deployment/svc-oms`.

## Risks
Rollback may cause schema incompatibilities if DB was migrated.

## Verification
Verify New Relic version tag reverts to stable.

## Escalation Criteria
Page on-call if pods enter CrashLoop after rollback.

