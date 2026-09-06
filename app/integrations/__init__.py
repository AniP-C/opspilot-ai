"""External integrations, isolated behind adapters.

Every vendor-specific concern (ServiceNow, New Relic response formats, HTTP,
credentials) lives under this package. The core application only ever sees the
internal domain models produced by the adapters.
"""
