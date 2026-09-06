"""The one object every request handler needs: engine, settings, secrets, rate limiter.

This is deliberately the single seam for later cross-cutting work (tracing, metrics,
provider routing): add a field here rather than reaching for a module-level global.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from atmpl.engine.service import AutomationEngine
from atmpl.providers.adapter import RouterAdapter
from atmpl.providers.router import ProviderRouter
from atmpl.secrets import SecretResolver, build_provider
from atmpl.settings import Settings
from atmpl.telemetry import Observability, build_observability


class TokenBucket:
    """In-process token bucket, keyed by credential. One process, one bucket set.

    [INFERRED] A single-process limiter is the honest scope: it protects this container,
    not a fleet. A multi-replica deployment needs a shared store (documented in ADR 0002).
    """

    def __init__(self, capacity: int, window_seconds: float) -> None:
        self.capacity = capacity
        self.window_seconds = window_seconds
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.capacity > 0

    def take(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``."""
        if not self.enabled:
            return True, 0
        now = time.monotonic() if now is None else now
        rate = self.capacity / self.window_seconds
        with self._lock:
            tokens, last = self._state.get(key, (float(self.capacity), now))
            tokens = min(float(self.capacity), tokens + (now - last) * rate)
            if tokens < 1.0:
                self._state[key] = (tokens, now)
                return False, max(1, int((1.0 - tokens) / rate) + 1)
            self._state[key] = (tokens - 1.0, now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


@dataclass
class AppContext:
    engine: AutomationEngine
    settings: Settings
    secrets: SecretResolver
    observability: Observability
    router: ProviderRouter
    limiter: TokenBucket = field(init=False)

    def __post_init__(self) -> None:
        capacity, window = self.settings.rate_limit_parts()
        self.limiter = TokenBucket(capacity, window)

    @property
    def jwt_key(self) -> str:
        return self.secrets.get_or_ephemeral("jwt_signing_key")

    @property
    def session_key(self) -> str:
        return self.secrets.get_or_ephemeral("session_signing_key")


def build_context(
    engine: AutomationEngine,
    settings: Settings,
    *,
    observability: Observability | None = None,
) -> AppContext:
    """Build the one object handlers read. Also back-fills the engine's instruments.

    The engine is often constructed before the settings are known (the CLI seeds a demo,
    then serves it), so this is where its observability and provider router are attached
    if it does not have them yet.
    """
    observability = observability or build_observability(settings)
    router = ProviderRouter(settings, observability=observability)
    if engine.observability is None:
        engine.observability = observability
        engine.adapter = RouterAdapter(router)
    return AppContext(
        engine=engine,
        settings=settings,
        secrets=SecretResolver(build_provider(settings)),
        observability=observability,
        router=router,
    )
