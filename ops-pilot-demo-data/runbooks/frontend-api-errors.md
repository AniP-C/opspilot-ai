# Frontend Api Errors

## Purpose
Resolve UI crashes and gateway connection issues.

## Applicable Services
svc-frontend

## Symptoms
Blank white screens (React crash). 502 Bad Gateway. CORS errors.

## Preconditions
Access to CDN and Ingress config.

## Diagnostic Checks
1. Check browser console for JS errors. 2. Verify Nginx ingress logs.

## Common Causes
Bad JS bundle, missing env vars, invalid CORS origins.

## Recommended Actions
Rollback frontend container. Fix ingress config.

## Risks
Ingress changes can misroute all traffic.

## Verification
JS error rate drops in New Relic Browser.

## Escalation Criteria
Page Customer Experience team if site remains blank.

