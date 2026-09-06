"""Optional LLM-backed hypothesis generation (OFF by default).

When enabled, the LLM only *proposes* candidate hypotheses as **structured
output** (statement + kind + implicated service). The deterministic scorer in
``scoring.py`` still evaluates evidence and ranks them, so LLM output never
becomes truth on its own and confidence is never taken from the model.

Any failure (missing provider package, missing credentials, bad output) falls
back to the heuristic generator — the engine never breaks because of the LLM.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.investigation.analysis import (
    KIND_DATA,
    KIND_DB,
    KIND_DEPENDENCY,
    KIND_DEPLOYMENT,
    KIND_FRONTEND,
    KIND_KAFKA,
    KIND_NETWORK,
    KIND_NOVEL,
    KIND_PAYMENT,
    KIND_REDIS,
    Signals,
)
from app.investigation.hypotheses import HeuristicHypothesisGenerator
from app.investigation.models import Hypothesis
from app.logging_config import get_logger

logger = get_logger(__name__)

_ALLOWED_KINDS = [
    KIND_DEPLOYMENT, KIND_DB, KIND_REDIS, KIND_KAFKA, KIND_PAYMENT,
    KIND_DEPENDENCY, KIND_FRONTEND, KIND_DATA, KIND_NETWORK, KIND_NOVEL,
]


class _LLMHypothesis(BaseModel):
    statement: str = Field(description="one-sentence hypothesis about the root cause")
    kind: str = Field(description=f"one of: {', '.join(_ALLOWED_KINDS)}")
    implicated_service: str = Field(description="the service id most implicated")


class _LLMHypotheses(BaseModel):
    hypotheses: list[_LLMHypothesis]


def _build_chat_model(settings):
    provider = settings.investigation_llm_provider.strip().lower()
    model = settings.investigation_llm_model
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=0)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=0)
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, temperature=0)
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=model, temperature=0)
    raise ValueError(f"Unknown LLM provider: {provider}")


class LLMHypothesisGenerator:
    def __init__(self, settings, fallback: HeuristicHypothesisGenerator | None = None) -> None:
        self._settings = settings
        self._fallback = fallback or HeuristicHypothesisGenerator()
        # Build eagerly so wiring failures surface at factory time (-> heuristic).
        self._model = _build_chat_model(settings).with_structured_output(_LLMHypotheses)

    @property
    def name(self) -> str:
        return f"llm:{self._settings.investigation_llm_provider}:{self._settings.investigation_llm_model}"

    def _prompt(self, signals: Signals) -> str:
        inc = signals.context.incident
        tele = "; ".join(
            f"{s}={snap.health_status.value}(err={snap.error_rate}%,lat={snap.latency_ms}ms)"
            for s, snap in signals.telemetry_current.items()
        )
        deploys = "; ".join(f"{d.id} on {d.service}" for d in signals.deployments) or "none"
        hist = "; ".join(
            str(e.refs.get("fingerprint"))
            for e in signals.evidence
            if e.source.value == "historical_incident"
        )
        return (
            "You are an SRE assistant. Propose 3-6 DISTINCT root-cause hypotheses for the "
            "incident below. Do NOT recommend or execute actions. Return structured output.\n\n"
            f"Incident: {inc.title}\nDescription: {inc.description}\n"
            f"Affected service: {inc.service}\nSymptoms: {inc.symptoms}\n"
            f"Neighbourhood telemetry: {tele}\n"
            f"Recent deployments: {deploys}\n"
            f"Historical patterns seen: {hist}\n"
            f"Allowed 'kind' values: {', '.join(_ALLOWED_KINDS)}\n"
        )

    def generate(self, signals: Signals) -> list[Hypothesis]:
        try:
            out: _LLMHypotheses = self._model.invoke(self._prompt(signals))
            hyps: list[Hypothesis] = []
            for i, h in enumerate(out.hypotheses, start=1):
                kind = h.kind if h.kind in _ALLOWED_KINDS else KIND_NOVEL
                hyps.append(
                    Hypothesis(
                        id=f"H{i}",
                        kind=kind,
                        statement=h.statement,
                        implicated_service=h.implicated_service or signals.neighborhood.affected,
                        source_references=["llm"],
                    )
                )
            # Always keep a novel fallback present.
            if not any(h.kind == KIND_NOVEL for h in hyps):
                hyps.append(
                    Hypothesis(
                        id=f"H{len(hyps) + 1}",
                        kind=KIND_NOVEL,
                        statement=f"Novel / undetermined failure affecting {signals.neighborhood.affected}",
                        implicated_service=signals.neighborhood.affected,
                    )
                )
            return hyps
        except Exception as exc:  # noqa: BLE001 - never break the engine on LLM errors
            logger.warning("investigation: LLM generation failed (%s); using heuristic", exc)
            return self._fallback.generate(signals)
