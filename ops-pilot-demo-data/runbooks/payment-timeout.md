# Payment Timeout

## Purpose
Handle downstream payment gateway latency or outages.

## Applicable Services
svc-payment, svc-oms

## Symptoms
Context deadline exceeded logs. Checkout abandonment high.

## Preconditions
Access to Stripe/Adyen status pages.

## Diagnostic Checks
1. Check vendor status page. 2. Review outbound packet drops on NAT.

## Common Causes
Third-party outage, NAT gateway IP exhaustion.

## Recommended Actions
Manually trip circuit breaker to fallback processor. Scale NAT gateways if network bound.

## Risks
Fallback processor may have higher transaction fees.

## Verification
Payment success rate returns to > 99%.

## Escalation Criteria
Page FinTech team if both processors are down.

