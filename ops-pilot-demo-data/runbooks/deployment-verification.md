# Deployment Verification

## Purpose
Post-deployment health validation checklist.

## Applicable Services
All

## Symptoms
Deployment marked complete but anomalies exist.

## Preconditions
Recent rollout.

## Diagnostic Checks
1. Check commit SHA matches image. 2. Watch logs for 5 minutes.

## Common Causes
Config map mismatch.

## Recommended Actions
If error rate > 1%, immediately rollback.

## Risks
Delaying rollback increases impact.

## Verification
New Relic marker shows stable.

## Escalation Criteria
Page on-call if rollback fails.

