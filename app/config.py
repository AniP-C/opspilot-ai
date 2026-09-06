"""Application configuration.

All settings are read from environment variables / a local ``.env`` file via
pydantic-settings. The defaults are chosen so the entire application runs
locally in MOCK mode with **zero external credentials**.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = parent of the ``app`` package directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Typed application settings.

    Field names map to environment variables case-insensitively
    (e.g. ``database_url`` <- ``DATABASE_URL``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database ---------------------------------------------------------
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'opspilot.db').as_posix()}"

    # --- ServiceNow (incident source) ------------------------------------
    servicenow_mode: str = "mock"  # mock | real
    servicenow_url: str | None = None
    servicenow_user: str | None = None
    servicenow_token: str | None = None

    # --- New Relic (telemetry source) ------------------------------------
    newrelic_mode: str = "mock"  # mock | real
    newrelic_api_key: str | None = None
    newrelic_account_id: str | None = None

    # --- Chroma (semantic knowledge store) -------------------------------
    chroma_path: str = (PROJECT_ROOT / "chroma_data").as_posix()

    # --- Embeddings ------------------------------------------------------
    embedding_backend: str = "hash"  # hash | sentence_transformers
    embedding_dim: int = 1024

    # --- Data ------------------------------------------------------------
    data_dir: str = (PROJECT_ROOT / "ops-pilot-demo-data").as_posix()

    # --- Context builder tuning ------------------------------------------
    deployment_window_minutes: int = 60
    historical_top_k: int = 5
    runbook_top_k: int = 3
    # Which services' recent deployments count as operational context:
    #   service       -> the affected service only
    #   dependencies  -> affected service + its DIRECT dependencies (default)
    #   neighborhood  -> affected + direct dependencies + direct dependents
    # This is OBSERVED CONTEXT ONLY — no causation is inferred.
    deployment_scope: str = "dependencies"

    # --- Phase 2: investigation ------------------------------------------
    # LLM-backed hypothesis generation is OPTIONAL and OFF by default so the
    # investigation engine runs fully offline with zero credentials. When on,
    # the LLM only *proposes* candidate hypotheses; scoring/ranking stays
    # deterministic.
    investigation_llm_mode: str = "off"  # off | on
    investigation_llm_provider: str = "anthropic"  # anthropic | openai | ollama
    investigation_llm_model: str = "claude-3-5-sonnet-latest"
    max_hypotheses: int = 6
    sufficiency_confidence: float = 0.65
    sufficiency_margin: float = 1.0
    # Telemetry older than this (relative to the incident) is treated as stale /
    # unknown rather than a current fact, so morning readings don't contaminate
    # an afternoon incident.
    telemetry_relevance_minutes: int = 90

    # --- Phase 3: remediation OS -----------------------------------------
    # Execution mode. ``mock`` (default) NEVER mutates any real infrastructure;
    # it drives the deterministic mock executor. ``real`` is only honoured when
    # ``allow_real_execution`` is ALSO true (two independent safety flags).
    execution_mode: str = "mock"  # mock | real
    # A hard, separate kill-switch. Real infrastructure mutation is impossible
    # unless this is explicitly true AND execution_mode == real. Default false.
    allow_real_execution: bool = False
    # ServiceNow write-back (work notes / state). Read stays available in Phase 1;
    # Phase 3 adds write, disabled by default and only ever real when BOTH this is
    # true AND servicenow_mode == real. The mock writer is always safe.
    servicenow_write_enabled: bool = False

    # Policy thresholds (deterministic risk/policy engine). These are the ONLY
    # knobs — the decision logic itself is fixed application code, never the LLM.
    auto_execute_min_confidence: float = 0.90
    auto_execute_min_recurrence: int = 3
    auto_execute_min_success_rate: float = 0.80
    # Verification observation window (simulated seconds of stabilisation).
    verification_window_seconds: int = 300

    # --- Misc ------------------------------------------------------------
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"

    # --- Derived helpers -------------------------------------------------
    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def servicenow_is_mock(self) -> bool:
        return self.servicenow_mode.strip().lower() != "real"

    @property
    def newrelic_is_mock(self) -> bool:
        return self.newrelic_mode.strip().lower() != "real"

    @property
    def investigation_llm_enabled(self) -> bool:
        return self.investigation_llm_mode.strip().lower() in {"on", "true", "1", "real"}

    @property
    def execution_is_mock(self) -> bool:
        """True unless BOTH real mode is selected AND the real-execution kill-switch
        is enabled. Any ambiguity resolves to mock — real mutation is opt-in twice."""
        return not (
            self.execution_mode.strip().lower() == "real" and self.allow_real_execution
        )

    @property
    def servicenow_write_is_mock(self) -> bool:
        """True unless real write-back is explicitly enabled AND ServiceNow is in
        real mode. Defaults to the safe mock writer."""
        return not (self.servicenow_write_enabled and not self.servicenow_is_mock)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
