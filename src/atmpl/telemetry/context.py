"""What a model call is *for*, carried beside it rather than threaded through signatures.

The engine knows the run, stage and template; the provider router knows the provider,
model, tokens and latency. Neither should have to learn the other's arguments, so the
engine opens a :func:`call_context` and the router reads it when it records the call.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class CallContext:
    run_id: str | None = None
    stage_key: str | None = None
    template_key: str | None = None
    organization_id: str | None = None
    purpose: str = "unknown"


_EMPTY = CallContext()
_CURRENT: ContextVar[CallContext] = ContextVar("atmpl_call_context", default=_EMPTY)


def current_call_context() -> CallContext:
    return _CURRENT.get()


@contextmanager
def call_context(
    *,
    run_id: str | None = None,
    stage_key: str | None = None,
    template_key: str | None = None,
    organization_id: str | None = None,
    purpose: str | None = None,
) -> Iterator[CallContext]:
    """Nest a call context; unset fields inherit from the enclosing one."""
    parent = _CURRENT.get()
    value = replace(
        parent,
        run_id=run_id if run_id is not None else parent.run_id,
        stage_key=stage_key if stage_key is not None else parent.stage_key,
        template_key=template_key if template_key is not None else parent.template_key,
        organization_id=(
            organization_id if organization_id is not None else parent.organization_id
        ),
        purpose=purpose if purpose is not None else parent.purpose,
    )
    token = _CURRENT.set(value)
    try:
        yield value
    finally:
        _CURRENT.reset(token)
