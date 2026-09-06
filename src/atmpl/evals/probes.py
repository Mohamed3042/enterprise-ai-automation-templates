"""Probes: the small number of ways an eval case can interrogate the system.

Each probe returns an :class:`Observation` — the decision the system actually made, the
codes it cited, and a text rendering the case's `must_not_contain` clause is checked
against. Probes never assert; the runner compares an observation to an expectation. That
separation is what lets the same case run on the mock and on a hosted provider.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import ValidationError

from atmpl.audit import AuditLog, verify_audit
from atmpl.guardrails.decisions import GuardrailRejection, approve_medium, decide_high
from atmpl.guardrails.policy import DeterministicPolicyEngine, check_ai_enabled
from atmpl.models import SignedHumanDecision
from atmpl.providers.router import ProviderRouter
from atmpl.redteam.contracts import CASES as REDTEAM_CASES


@dataclass
class Observation:
    decision: str
    codes: tuple[str, ...] = ()
    guardrail: str | None = None
    detail: str = ""
    rendered: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProbeContext:
    """Everything a probe may reach for. A probe gets no database and no secrets."""

    router: ProviderRouter
    policy: DeterministicPolicyEngine = field(default_factory=DeterministicPolicyEngine)


ProbeFn = Callable[[ProbeContext, dict[str, Any]], Observation]
REGISTRY: dict[str, ProbeFn] = {}


def probe(name: str) -> Callable[[ProbeFn], ProbeFn]:
    def register(function: ProbeFn) -> ProbeFn:
        REGISTRY[name] = function
        return function

    return register


def _policy_observation(result, sanitized: Any = None) -> Observation:
    codes = tuple(violation.code for violation in result.violations)
    return Observation(
        decision="allowed" if result.allowed else "blocked",
        codes=codes,
        guardrail=result.violations[0].guardrail if result.violations else None,
        detail=result.violations[0].message if result.violations else "no violations",
        rendered=json.dumps(sanitized if sanitized is not None else result.sanitized, default=str),
    )


@probe("preflight")
def preflight(context: ProbeContext, args: dict[str, Any]) -> Observation:
    """Does the deterministic input gate let this payload reach a model?"""
    scope = args.get("allowed_scope")
    result = context.policy.preflight(
        args.get("payload") or {},
        allowed_region=args.get("allowed_region"),
        allowed_scope=set(scope) if scope else None,
    )
    return _policy_observation(result)


@probe("postflight")
def postflight(context: ProbeContext, args: dict[str, Any]) -> Observation:
    """Does the deterministic output gate let this model output become a draft?"""
    bounds = {
        name: (float(low), float(high))
        for name, (low, high) in (args.get("numeric_bounds") or {}).items()
    }
    output = args.get("output")
    result = context.policy.postflight(
        output,
        required_fields=set(args.get("required_fields") or ()),
        numeric_bounds=bounds,
        max_bytes=int(args.get("max_bytes", 4096)),
    )
    return _policy_observation(result, sanitized=output)


@probe("authority")
def authority(context: ProbeContext, args: dict[str, Any]) -> Observation:
    """Can this role sign this transition?"""
    tier = str(args.get("tier", "HIGH")).upper()
    allowed_roles = list(args.get("allowed_roles") or [])
    try:
        decision = SignedHumanDecision.model_validate(args.get("decision") or {})
    except ValidationError as exc:
        return Observation(
            decision="blocked",
            codes=("UNSIGNED_DECISION",),
            guardrail="G2",
            detail=f"The decision object did not validate ({exc.error_count()} field(s)).",
            rendered=str(exc),
        )
    try:
        outcome = (decide_high if tier == "HIGH" else approve_medium)(decision, allowed_roles)
    except GuardrailRejection as exc:
        return Observation(
            decision="blocked",
            codes=("AUTHORITY_MATRIX",),
            guardrail=exc.guardrail,
            detail=str(exc),
            rendered=str(exc),
        )
    return Observation(
        decision="allowed",
        guardrail=outcome.guardrail,
        detail=f"Recorded as {outcome.status.value}.",
        rendered=outcome.status.value,
    )


@probe("kill_switch")
def kill_switch(context: ProbeContext, args: dict[str, Any]) -> Observation:
    result = check_ai_enabled(bool(args.get("enabled", True)))
    return _policy_observation(result)


@probe("audit_chain")
def audit_chain(context: ProbeContext, args: dict[str, Any]) -> Observation:
    """Write a record, optionally mutate it, and ask the chain whether it still verifies."""
    with TemporaryDirectory(prefix="atmpl-evals-") as directory:
        path = Path(directory) / "audit.jsonl"
        AuditLog(path).append("eval_probe", actor="system", payload={"synthetic": True})
        if args.get("tamper", True):
            record = json.loads(path.read_text(encoding="utf-8"))
            record["payload"]["synthetic"] = False
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        verification = verify_audit(path)
    return Observation(
        decision="allowed" if verification.valid else "blocked",
        codes=() if verification.valid else ("AUDIT_CHAIN_BROKEN",),
        guardrail="G5",
        detail=verification.error or f"{verification.count} record(s) verified",
        rendered=str(verification.error or verification.count),
    )


@probe("redteam")
def redteam(context: ProbeContext, args: dict[str, Any]) -> Observation:
    """Run one of the original R1-R8 adversarial contracts, unchanged."""
    wanted = str(args["case"])
    demo = str(args.get("demo", "retail"))
    function = dict(REDTEAM_CASES).get(wanted)
    if function is None:
        raise KeyError(f"Unknown red-team case '{wanted}'")
    outcome = function(demo)
    return Observation(
        decision="blocked" if outcome.status == "BLOCKED" else "allowed",
        codes=(outcome.status,),
        detail=outcome.detail,
        rendered=outcome.detail,
        extra={"demo": demo},
    )


@probe("discovery_agent")
def discovery_agent(context: ProbeContext, args: dict[str, Any]) -> Observation:
    """Run the discovery agent end to end and see whether its answer holds up."""
    from atmpl.agents.discovery import AgentRefused, run_discovery

    try:
        result = run_discovery(
            context.router,
            description=str(args.get("description", "")),
            template=args.get("template"),
            organization=args.get("organization"),
        )
    except AgentRefused as exc:
        return Observation(
            decision="refused",
            codes=tuple(sorted({str(item.get("problem", "invalid")) for item in exc.follow_ups})),
            detail=str(exc),
            rendered=json.dumps(exc.follow_ups, default=str),
        )
    return Observation(
        decision="valid",
        detail=f"{result.template}: {len(result.rationales)} answered field(s)",
        rendered=json.dumps(result.answers, default=str),
        extra={
            "template": result.template,
            "provider": result.provider,
            "model": result.model,
            "open_questions": result.open_questions,
        },
    )


@probe("discovery_assembly")
def discovery_assembly(context: ProbeContext, args: dict[str, Any]) -> Observation:
    """Hand the assembler a draft directly, to probe what the agent is *allowed* to say."""
    from atmpl.agents.discovery import AgentRefused, Rationale, check_rationales, to_answers
    from atmpl.intake.resolver import validate_answers

    template = str(args["template"])
    rationales = [Rationale.model_validate(item) for item in args.get("rationales") or []]
    try:
        check_rationales(template, rationales)
    except AgentRefused as exc:
        return Observation(
            decision="refused",
            codes=tuple(sorted({str(item.get("problem", "invalid")) for item in exc.follow_ups})),
            detail=str(exc),
            rendered=json.dumps(exc.follow_ups, default=str),
        )
    answers = to_answers(args.get("answers") or {})
    follow_ups = validate_answers(template, answers)
    if follow_ups:
        return Observation(
            decision="refused",
            codes=("validation_error",),
            detail=f"{len(follow_ups)} field(s) did not validate.",
            rendered=json.dumps(follow_ups, default=str),
        )
    return Observation(
        decision="valid",
        detail="The assembled questionnaire validates.",
        rendered=json.dumps(answers, default=str),
    )


@probe("provider_response")
def provider_response(context: ProbeContext, args: dict[str, Any]) -> Observation:
    """Feed a recorded provider body through an adapter's parser, then the output gate.

    This is how "the provider returned malformed JSON" is tested without a live provider:
    the body is real wire shape, the parsing is the shipped code, and the verdict is the
    same deterministic postflight a live answer would meet.
    """
    from atmpl.evals.cassettes import parse_recorded

    body = args.get("body")
    result = parse_recorded(str(args.get("provider", "openai")), body or {})
    content = result.json if result.json is not None else {"raw_text": result.text or ""}
    verdict = context.policy.postflight(
        content,
        required_fields=set(args.get("required_fields") or ()),
    )
    observation = _policy_observation(verdict, sanitized=content)
    observation.extra["parsed_as"] = "json" if result.json is not None else "text"
    return observation
