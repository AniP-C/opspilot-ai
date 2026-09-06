# Data Dictionary

## services.json
* `id` (string, required): Unique service identifier (e.g., svc-oms). Maps to dependencies.source_service.
* `name` (string, required): Human-readable name.
* `description` (string, required): Purpose of service.
* `owner` (string, required): Team responsible.
* `criticality` (string, required): Business impact (critical, high, medium).
* `environment` (string, required): Deployment environment.

## dependencies.json
* `source_service` (string, required): Calling service ID.
* `target_service` (string, required): Receiving service ID.
* `relationship` (string, required): Nature of link (calls, publishes_to).
* `protocol` (string, required): Network protocol (REST, TCP, gRPC).
* `criticality` (string, required): Impact if target fails.

## deployments.json
* `deployment_id` (string, required): Unique ID (e.g., dep-oms-v4.2.1). Referenced by ground_truth.
* `service` (string, required): Updated service ID.
* `version` (string, required): Semantic version.
* `environment` (string, required): Environment.
* `commit_id` (string, required): Git SHA.
* `deployed_at` (string, required): ISO8601 timestamp.
* `changed_components` (array, required): Affected modules.
* `deployment_status` (string, required): success, failed, rolled_back.

## historical_incidents.json
* `incident_id` (string, required): Unique ID.
* `title` (string, required): Brief operational summary.
* `description` (string, required): Detailed symptom text.
* `service` (string, required): Affected service ID.
* `severity` (string, required): SEV1-SEV4.
* `environment` (string, required): Environment.
* `created_at` (string, required): ISO8601 timestamp.
* `resolved_at` (string, required): ISO8601 timestamp.
* `symptoms` (array, required): Observable symptoms.
* `root_cause` (string, required): Underlying issue.
* `resolution` (string, required): Action taken.
* `resolution_success` (boolean, required): Did it work.
* `affected_component` (string, required): Internal module.
* `incident_fingerprint` (string, required): Hash category.
* `duration_minutes` (integer, required): Resolution time.
* `ground_truth` (object, optional): Contains `related_deployment_id` and `correlation` ("true"/"false").

## seed_incidents.json
* `incident` (object, required): User-visible incident payload.
* `ground_truth` (object, required): Hidden validation payload containing `actual_root_cause`, `expected_service`, `expected_remediation`, `historical_pattern`, and optionally `related_deployment_id` and `correlation`.

## telemetry.json
* `service` (string, required): Service ID.
* `timestamp` (string, required): ISO8601 snapshot time.
* `error_rate_percent` (float, required): Error percentage.
* `latency_ms` (integer, required): P99 latency.
* `throughput` (integer, required): Requests per second.
* `health_status` (string, required): healthy, degraded, critical.
* `active_connections` (integer, required): Open connections.
* `metadata` (object, required): Contextual fields (e.g., cpu_percent, partition_lag).
