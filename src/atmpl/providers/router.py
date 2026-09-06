"""The one place a model call happens.

Everything a caller wants — retries, a fallback chain, a circuit breaker, a span, a
Prometheus sample, a ledger row and a cost estimate — happens here, so no adapter has to
remember any of it and no caller can skip it. A provider is chosen by settings; the caller
passes messages, not a vendor (invariant **G7**).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from atmpl.providers.anthropic import AnthropicProvider
from atmpl.providers.base import (
    Message,
    Provider,
    ProviderError,
    ProviderResult,
    ToolSpec,
)
from atmpl.providers.gemini import GeminiProvider
from atmpl.providers.mock import MockProvider
from atmpl.providers.openai_compat import OpenAICompatibleProvider
from atmpl.providers.pricing import PriceTable, load_prices
from atmpl.settings import Settings
from atmpl.telemetry import Observability
from atmpl.telemetry.context import current_call_context
from atmpl.telemetry.ledger import LlmCallRecord
from atmpl.telemetry.tracing import current_trace_id, llm_call_span

KNOWN_PROVIDERS = ("mock", "gemini", "anthropic", "openai")
#: `claude` was v0.1's name for the Anthropic adapter; kept working, deprecated in ADR 0007.
PROVIDER_ALIASES = {"claude": "anthropic", "openai_compat": "openai", "azure": "openai"}


class AllProvidersFailed(RuntimeError):
    """Every provider in the chain refused or failed. Carries each one's last error."""

    def __init__(self, failures: dict[str, str]) -> None:
        detail = "; ".join(f"{name}: {reason}" for name, reason in failures.items())
        super().__init__(f"No provider completed the call ({detail})")
        self.failures = failures


class CircuitBreaker:
    """Open after N consecutive failures; half-open again after the cooldown."""

    def __init__(self, threshold: int, cooldown_seconds: float) -> None:
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_open(self, provider: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            opened = self._opened_at.get(provider)
            if opened is None:
                return False
            if now - opened >= self.cooldown_seconds:
                self._opened_at.pop(provider, None)
                self._failures[provider] = 0
                return False
            return True

    def record_failure(self, provider: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            count = self._failures.get(provider, 0) + 1
            self._failures[provider] = count
            if self.threshold > 0 and count >= self.threshold:
                self._opened_at[provider] = now

    def record_success(self, provider: str) -> None:
        with self._lock:
            self._failures[provider] = 0
            self._opened_at.pop(provider, None)

    def state(self, provider: str) -> str:
        return "open" if self.is_open(provider) else "closed"


def build_provider(name: str, settings: Settings) -> Provider:
    """Construct one provider from settings. Raises for an unknown name."""
    resolved = PROVIDER_ALIASES.get(name, name)
    timeout = settings.provider_timeout_seconds
    if resolved == "mock":
        return MockProvider()
    if resolved == "gemini":
        return GeminiProvider(
            model=settings.gemini_model,
            base_url=settings.gemini_base_url,
            timeout=timeout,
        )
    if resolved == "anthropic":
        return AnthropicProvider(
            model=settings.anthropic_model,
            base_url=settings.anthropic_base_url,
            timeout=timeout,
        )
    if resolved == "openai":
        return OpenAICompatibleProvider(
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            timeout=timeout,
        )
    raise ValueError(
        f"Unknown provider '{name}'. Allowed: {', '.join(KNOWN_PROVIDERS)} "
        f"(aliases: {', '.join(sorted(PROVIDER_ALIASES))})"
    )


@dataclass
class ProviderStatus:
    name: str
    model: str
    configured: bool
    role: str  # primary | fallback
    breaker: str


class ProviderRouter:
    """Primary provider, then each fallback, each with bounded retries."""

    def __init__(
        self,
        settings: Settings,
        *,
        observability: Observability | None = None,
        providers: list[Provider] | None = None,
        prices: PriceTable | None = None,
        sleep=time.sleep,
    ) -> None:
        self.settings = settings
        self.observability = observability
        self.prices = prices or load_prices()
        self.chain: list[Provider] = providers or [
            build_provider(name, settings) for name in settings.provider_chain
        ] or [MockProvider()]
        self.breaker = CircuitBreaker(
            settings.provider_breaker_threshold,
            settings.provider_breaker_cooldown_seconds,
        )
        self._sleep = sleep

    # ------------------------------------------------------------------ reporting

    @property
    def primary(self) -> Provider:
        return self.chain[0]

    def status(self) -> list[ProviderStatus]:
        return [
            ProviderStatus(
                name=provider.name,
                model=provider.model,
                configured=provider.configured(),
                role="primary" if index == 0 else "fallback",
                breaker=self.breaker.state(provider.name),
            )
            for index, provider in enumerate(self.chain)
        ]

    # ------------------------------------------------------------------ the call

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResult:
        failures: dict[str, str] = {}
        primary_name = self.primary.name
        for index, provider in enumerate(self.chain):
            if self.breaker.is_open(provider.name):
                failures[provider.name] = "circuit breaker open"
                continue
            if not provider.configured():
                failures[provider.name] = "not configured"
                continue
            try:
                result = self._attempt_provider(
                    provider,
                    messages,
                    tools=tools,
                    schema=schema,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except ProviderError as exc:
                failures[provider.name] = f"{exc.kind}: {exc.detail}"
                continue
            if index == 0:
                return result
            return ProviderResult(**{**result.__dict__, "fallback_from": primary_name})
        raise AllProvidersFailed(failures)

    def _attempt_provider(
        self,
        provider: Provider,
        messages: list[Message],
        **kwargs: Any,
    ) -> ProviderResult:
        attempts = max(1, self.settings.provider_max_attempts)
        last: ProviderError | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._one_call(provider, messages, attempt=attempt, **kwargs)
            except ProviderError as exc:
                last = exc
                self.breaker.record_failure(provider.name)
                if not exc.retryable or attempt == attempts:
                    raise
                self._sleep(self.settings.provider_backoff_seconds * (2 ** (attempt - 1)))
        raise last if last else RuntimeError("unreachable")

    def _one_call(
        self,
        provider: Provider,
        messages: list[Message],
        *,
        attempt: int,
        **kwargs: Any,
    ) -> ProviderResult:
        context = current_call_context()
        started = time.perf_counter()
        with llm_call_span(
            provider=provider.name,
            model=provider.model,
            **{
                "atmpl.attempt": attempt,
                "atmpl.purpose": context.purpose,
                "atmpl.run_id": context.run_id,
                "atmpl.stage_key": context.stage_key,
                "atmpl.template_key": context.template_key,
            },
        ) as span:
            try:
                result = provider.complete(messages, **kwargs)
            except ProviderError as exc:
                latency_ms = (time.perf_counter() - started) * 1000
                span.set_attribute("atmpl.outcome", "error")
                span.set_attribute("atmpl.error_kind", exc.kind)
                span.set_attribute("atmpl.latency_ms", round(latency_ms, 3))
                self._record(
                    provider,
                    latency_ms=latency_ms,
                    outcome="error",
                    attempt=attempt,
                    error_kind=exc.kind,
                    result=None,
                )
                raise
            cost = self.prices.estimate(provider.name, result.model, result.usage)
            span.set_attribute("atmpl.outcome", "ok")
            span.set_attribute("atmpl.latency_ms", round(result.latency_ms, 3))
            if result.usage.input_tokens is not None:
                span.set_attribute("atmpl.tokens_in", result.usage.input_tokens)
            if result.usage.output_tokens is not None:
                span.set_attribute("atmpl.tokens_out", result.usage.output_tokens)
            if cost is not None:
                span.set_attribute("atmpl.cost_estimate_usd", cost)
            self._record(
                provider,
                latency_ms=result.latency_ms,
                outcome="ok",
                attempt=attempt,
                error_kind=None,
                result=result,
                cost=cost,
            )
            return ProviderResult(**{**result.__dict__, "attempt": attempt})

    # ------------------------------------------------------------------ instruments

    def _record(
        self,
        provider: Provider,
        *,
        latency_ms: float,
        outcome: str,
        attempt: int,
        error_kind: str | None,
        result: ProviderResult | None,
        cost: float | None = None,
    ) -> None:
        if self.observability is None:
            return
        context = current_call_context()
        model = result.model if result else provider.model
        self.observability.calls.record(
            LlmCallRecord(
                started_at=datetime.now(UTC),
                provider=provider.name,
                model=model,
                latency_ms=latency_ms,
                outcome=outcome,
                tokens_in=result.usage.input_tokens if result else None,
                tokens_out=result.usage.output_tokens if result else None,
                cost_estimate_usd=cost,
                cost_basis=self.prices.basis(provider.name, model),
                error_kind=error_kind,
                attempt=attempt,
                run_id=context.run_id,
                stage_key=context.stage_key,
                template_key=context.template_key,
                purpose=context.purpose,
                trace_id=current_trace_id(),
            )
        )
        metrics = self.observability.metrics
        metrics.llm_calls.labels(provider.name, model, outcome).inc()
        metrics.llm_duration.labels(provider.name, model).observe(latency_ms / 1000)
        if result is not None:
            if result.usage.input_tokens:
                metrics.llm_tokens.labels(provider.name, model, "in").inc(result.usage.input_tokens)
            if result.usage.output_tokens:
                metrics.llm_tokens.labels(provider.name, model, "out").inc(
                    result.usage.output_tokens
                )
            if cost:
                metrics.llm_cost.labels(provider.name, model).inc(cost)
