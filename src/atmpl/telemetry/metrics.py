"""Prometheus collectors. One registry per process, built explicitly rather than using
the global default, so a test can build a second one without duplicate-name errors.
"""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import REGISTRY as GLOBAL_REGISTRY

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Model calls are seconds-scale; HTTP requests are milliseconds-scale. Two ladders.
_HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_LLM_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0)


@dataclass
class Metrics:
    """The collectors this process exposes at ``/metrics``."""

    registry: CollectorRegistry

    def __post_init__(self) -> None:
        registry = self.registry
        self.http_requests = Counter(
            "atmpl_http_requests_total",
            "HTTP requests handled, by route template and status class.",
            ["method", "route", "status"],
            registry=registry,
        )
        self.http_duration = Histogram(
            "atmpl_http_request_duration_seconds",
            "Wall-clock time to produce an HTTP response.",
            ["method", "route"],
            buckets=_HTTP_BUCKETS,
            registry=registry,
        )
        self.llm_calls = Counter(
            "atmpl_llm_calls_total",
            "Model calls attempted, by provider, model and outcome.",
            ["provider", "model", "outcome"],
            registry=registry,
        )
        self.llm_duration = Histogram(
            "atmpl_llm_call_duration_seconds",
            "Wall-clock time of one model call attempt.",
            ["provider", "model"],
            buckets=_LLM_BUCKETS,
            registry=registry,
        )
        self.llm_tokens = Counter(
            "atmpl_llm_tokens_total",
            "Tokens reported by the provider, by direction.",
            ["provider", "model", "direction"],
            registry=registry,
        )
        self.llm_cost = Counter(
            "atmpl_llm_cost_estimate_usd_total",
            "ESTIMATED spend from the committed price table; not a billing figure.",
            ["provider", "model"],
            registry=registry,
        )
        self.guardrail_decisions = Counter(
            "atmpl_guardrail_decisions_total",
            "Guardrail evaluations, by phase, guardrail id and decision.",
            ["phase", "guardrail", "decision"],
            registry=registry,
        )
        self.stage_decisions = Counter(
            "atmpl_stage_decisions_total",
            "Signed human decisions recorded, by risk tier and decision.",
            ["risk_tier", "decision"],
            registry=registry,
        )
        self.runs_started = Counter(
            "atmpl_runs_started_total",
            "Governed runs started, by source template.",
            ["template"],
            registry=registry,
        )
        self.webhook_deliveries = Counter(
            "atmpl_webhook_deliveries_total",
            "Outbound webhook delivery attempts, by resulting status.",
            ["status"],
            registry=registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)


def build_metrics(*, use_global: bool = False) -> Metrics:
    """A fresh registry by default; the global one only where a process needs one."""
    return Metrics(registry=GLOBAL_REGISTRY if use_global else CollectorRegistry())
