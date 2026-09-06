# Kafka Consumer Recovery

## Purpose
Recover stalled Kafka consumers (e.g. WMS).

## Applicable Services
svc-kafka, svc-wms

## Symptoms
partition_lag > 10000. consumer_state offline.

## Preconditions
Kafka CLI access.

## Diagnostic Checks
1. Check consumer group offsets. 2. Look for serialization errors in logs.

## Common Causes
Poison pill message, memory limit hit on broker.

## Recommended Actions
Restart consumer pods. If poison pill, advance offset past bad message.

## Risks
Advancing offsets drops messages (requires manual reconciliation).

## Verification
Lag decreases steadily.

## Escalation Criteria
Page Platform Infrastructure if brokers are offline.

