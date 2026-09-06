# Wms Inventory Sync

## Purpose
Resolve stock synchronization failures between WMS and Inventory.

## Applicable Services
svc-wms, svc-inventory

## Symptoms
Inventory API returns 429s, or WMS queue backlog grows.

## Preconditions
Access to WMS worker logs.

## Diagnostic Checks
1. Check Inventory rate limits. 2. Verify WMS worker thread count.

## Common Causes
Supplier API outage causing Inventory to hang, cascading to WMS.

## Recommended Actions
Increase WMS thread pool. Request rate limit bump from Supply Chain.

## Risks
Overwhelming Inventory API can crash the service globally.

## Verification
Queue backlog reaches 0.

## Escalation Criteria
Page Supply Chain if Inventory API remains unresponsive.

