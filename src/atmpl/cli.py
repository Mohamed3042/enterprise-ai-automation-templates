"""One CLI for discovery, compilation, execution, demos, audit, credentials, and webhooks."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn
import yaml
from sqlalchemy import inspect, select

from atmpl.agents.discovery import AgentRefused, run_discovery
from atmpl.audit import verify_audit
from atmpl.demos import (
    exercise_demos,
    seed_demo_data,
    seed_demo_subscription,
    seed_inbound_sources,
)
from atmpl.engine.database import ApiKey, Database
from atmpl.engine.service import AutomationEngine
from atmpl.evals.runner import NoCasesFound, run_suite, write_report
from atmpl.intake.resolver import DiscoveryError, init_discovery, resolve_discovery
from atmpl.migrate import current_revision, upgrade_database
from atmpl.models import AI_ACTIONS, InstantiatedWorkflow
from atmpl.providers.base import Message, ProviderError
from atmpl.providers.router import ProviderRouter
from atmpl.redteam.contracts import run_all
from atmpl.runtime import build_context
from atmpl.security.credentials import (
    CredentialError,
    create_api_key,
    create_oauth_client,
    hash_password,
    revoke_api_key,
)
from atmpl.settings import Settings, settings_from_env
from atmpl.telemetry import build_observability
from atmpl.web.app import create_app
from atmpl.webhooks.outbox import deliver_pending

DEMO_SECRETS_FILE = Path("var") / "demo-webhook-secrets.json"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "organization"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m atmpl",
        description="Discovery-led, human-authority enterprise automation templates.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init", help="Emit a typed discovery questionnaire.")
    init.add_argument("template", choices=["retail", "ministry", "bank"])
    init.add_argument("--org", required=True, help="Synthetic or client organization name.")
    init.add_argument("--output", type=Path, default=Path(".atmpl"))

    resolve = subcommands.add_parser("resolve", help="Compile validated answers into a workflow.")
    resolve.add_argument("template", choices=["retail", "ministry", "bank"])
    resolve.add_argument("answers", type=Path)
    resolve.add_argument("--output", type=Path, default=Path(".atmpl/workflow.yaml"))

    run = subcommands.add_parser("run", help="Start one compiled workflow using the mock adapter.")
    run.add_argument("workflow", type=Path)
    run.add_argument("--db", type=Path, default=Path(".atmpl/run.db"))
    run.add_argument("--audit", type=Path, default=Path(".atmpl/audit.jsonl"))

    demo = subcommands.add_parser("demo", help="Seed and serve the deterministic portfolio demos.")
    demo_subcommands = demo.add_subparsers(dest="demo_command", required=True)
    demo_up = demo_subcommands.add_parser("up", help="Seed all demos and serve the dashboard.")
    demo_up.add_argument("--host", default="127.0.0.1")
    demo_up.add_argument("--port", type=int, default=8000)
    demo_up.add_argument(
        "--no-open-api",
        action="store_true",
        help="Keep /api/v1 credential-only even in the keyless demo.",
    )
    demo_up.add_argument(
        "--exercise",
        action="store_true",
        help=(
            "Draft one AI stage per demo at start-up, so /llmops and /metrics show real "
            "calls rather than an empty deployment. Used by the hosted read-only demo."
        ),
    )

    subcommands.add_parser("redteam", help="Execute R1-R8 across all three demos.")

    evals = subcommands.add_parser("evals", help="Scored eval suite over the guardrails.")
    evals_subcommands = evals.add_subparsers(dest="evals_command", required=True)
    evals_run = evals_subcommands.add_parser("run", help="Run every case and write a report.")
    evals_run.add_argument(
        "--provider",
        default=None,
        help="Override the configured provider for this run (e.g. mock, gemini).",
    )
    evals_run.add_argument("--out", type=Path, default=Path("var") / "evals")
    evals_run.add_argument("--only", nargs="*", default=None, help="Run only these case ids.")
    evals_gate = evals_subcommands.add_parser(
        "gate",
        help="Fail the build when the gated categories drop below a pass rate.",
    )
    evals_gate.add_argument("--min-pass", type=float, default=1.0)
    evals_gate.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Score an existing report instead of running the suite again.",
    )

    discover = subcommands.add_parser(
        "discover",
        help="Draft a typed questionnaire from a free-text process description.",
    )
    discover.add_argument("--text", required=True, help="The process description.")
    discover.add_argument("--template", default=None, choices=["retail", "ministry", "bank"])
    discover.add_argument("--org", default=None, help="Organization name for the draft.")
    discover.add_argument("--output", type=Path, default=None, help="Write answers.yaml here.")

    providers = subcommands.add_parser("providers", help="Which model providers are wired up.")
    providers_subcommands = providers.add_subparsers(dest="providers_command", required=True)
    providers_check = providers_subcommands.add_parser(
        "check",
        help="Print the provider chain, its configuration state and an optional live ping.",
    )
    providers_check.add_argument(
        "--ping",
        action="store_true",
        help="Make one real, minimal call to each configured provider.",
    )

    audit = subcommands.add_parser("audit", help="Operate on the hash-chained audit ledger.")
    audit_subcommands = audit.add_subparsers(dest="audit_command", required=True)
    audit_verify = audit_subcommands.add_parser("verify", help="Recompute every hash-chain link.")
    audit_verify.add_argument("--path", type=Path, default=Path("var/audit.jsonl"))

    database = subcommands.add_parser("db", help="Schema migrations for ATMPL_DATABASE_URL.")
    database_subcommands = database.add_subparsers(dest="db_command", required=True)
    database_subcommands.add_parser("upgrade", help="Apply every pending Alembic migration.")
    database_subcommands.add_parser("current", help="Print the applied revision.")

    keys = subcommands.add_parser("keys", help="Scoped API keys for machine callers.")
    keys_subcommands = keys.add_subparsers(dest="keys_command", required=True)
    keys_create = keys_subcommands.add_parser("create", help="Mint a key and print it once.")
    keys_create.add_argument("--name", required=True)
    keys_create.add_argument(
        "--scopes",
        required=True,
        help="Comma or space separated, e.g. runs:read,runs:write,decisions:write",
    )
    keys_subcommands.add_parser("list", help="List key names, scopes, and last use.")
    keys_revoke = keys_subcommands.add_parser("revoke", help="Revoke a key by name.")
    keys_revoke.add_argument("--name", required=True)

    clients = subcommands.add_parser("clients", help="OAuth2 client-credentials principals.")
    clients_subcommands = clients.add_subparsers(dest="clients_command", required=True)
    clients_create = clients_subcommands.add_parser("create", help="Create a client and secret.")
    clients_create.add_argument("--name", required=True)
    clients_create.add_argument("--scopes", required=True)
    clients_create.add_argument("--client-id", default=None)

    users = subcommands.add_parser("users", help="Dashboard sign-in helpers.")
    users_subcommands = users.add_subparsers(dest="users_command", required=True)
    users_subcommands.add_parser(
        "hash-password",
        help="Read a password (prompt or ATMPL_NEW_PASSWORD) and print its PBKDF2 hash.",
    )

    webhooks = subcommands.add_parser("webhooks", help="Outbound delivery worker and sources.")
    webhooks_subcommands = webhooks.add_subparsers(dest="webhooks_command", required=True)
    deliver = webhooks_subcommands.add_parser("deliver", help="Run one delivery pass and exit.")
    deliver.add_argument(
        "--ignore-backoff",
        action="store_true",
        help="Attempt every pending delivery now, not only the due ones.",
    )
    webhooks_subcommands.add_parser(
        "sources",
        help="Print the configured inbound sources and their shared secrets.",
    )

    subcommands.add_parser("doctor", help="Report what this deployment is actually configured as.")
    return parser


# --------------------------------------------------------------------------- helpers


def _open_engine(settings: Settings) -> AutomationEngine:
    database = Database(settings.database_url, create_all=settings.create_schema_on_start)
    audit_path = Path("var") / "audit.jsonl"
    return AutomationEngine(database, audit_path)


# --------------------------------------------------------------------------- commands


def _command_init(args: argparse.Namespace) -> int:
    questionnaire, answers = init_discovery(args.template, args.org, args.output)
    print("Discovery pack created (fail-closed until every typed answer validates):")
    print(f"  questionnaire: {questionnaire}")
    print(f"  answers:       {answers}")
    return 0


def _command_resolve(args: argparse.Namespace) -> int:
    try:
        workflow = resolve_discovery(args.template, args.answers, args.output)
    except DiscoveryError as exc:
        print(str(exc))
        return 2
    print(f"COMPILED: {workflow.metadata.name}")
    print(f"workflow: {args.output}")
    print(f"stages: {len(workflow.stages)} - source: {workflow.source_template}")
    return 0


def _command_run(args: argparse.Namespace) -> int:
    workflow = InstantiatedWorkflow.model_validate(
        yaml.safe_load(args.workflow.read_text(encoding="utf-8"))
    )
    database = Database(path=args.db)
    engine = AutomationEngine(database, args.audit)
    org_name = workflow.org_profile["organization"]["name"]
    org_id = f"org_{_slug(org_name)}"
    workflow_id = f"wf_{_slug(workflow.metadata.id)}"
    region = workflow.org_profile.get("regional", {}).get("region", "GLOBAL")
    engine.create_organization(
        organization_id=org_id,
        name=org_name,
        sector=workflow.metadata.sector,
        profile={**workflow.org_profile, "synthetic": True},
    )
    engine.create_workflow(
        workflow_id=workflow_id,
        organization_id=org_id,
        workflow=workflow,
    )
    run = engine.start_run(
        workflow_id=workflow_id,
        title=f"Synthetic run · {workflow.metadata.name}",
        region=region,
    )
    for stage in workflow.stages:
        if stage.risk_tier.value == "LOW" and stage.action_type.value == "system_action":
            engine.complete_low_stage(run.id, stage.id)
            continue
        if stage.action_type in AI_ACTIONS:
            actions = stage.inputs.get("allowed_actions", [])
            payload = {"region": region, "synthetic": True}
            if actions:
                payload["requested_action"] = actions[0]
            result = engine.process_ai_stage(run.id, stage.id, payload)
            print(f"{stage.id}: {result.status.value} — {result.detail}")
            break
        break
    print(f"RUN: {run.id} - parked at the next human gate")
    return 0


def _command_demo_up(args: argparse.Namespace) -> int:
    if not args.no_open_api:
        os.environ.setdefault("ATMPL_DEMO_OPEN_API", "1")
    settings = settings_from_env()
    root = Path.cwd() / "var"
    root.mkdir(parents=True, exist_ok=True)
    revision = upgrade_database(settings.database_url)
    database = Database(settings.database_url, create_all=settings.create_schema_on_start)
    engine = seed_demo_data(root / "demo.db", root / "audit.jsonl", database=database)
    inbound = seed_inbound_sources(engine)
    # One set of instruments for the whole process: the calls made below have to be the
    # same ones the served /llmops page reads, or the page reports an idle deployment.
    observability = build_observability(settings)
    context = build_context(engine, settings, observability=observability)
    resolver = context.secrets
    # A provider-configured secret wins at verification time, so print what a caller must use.
    inbound = {
        source: resolver.get(f"webhook_inbound_{source}") or secret
        for source, secret in inbound.items()
    }
    DEMO_SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEMO_SECRETS_FILE.write_text(json.dumps(inbound, indent=2), encoding="utf-8")

    receiver_url = os.getenv("ATMPL_DEMO_SUBSCRIBE_URL")
    receiver_secret = os.getenv("ATMPL_DEMO_SUBSCRIBE_SECRET")
    subscription_id = (
        seed_demo_subscription(engine, receiver_url, receiver_secret)
        if receiver_url and receiver_secret
        else None
    )

    print("SEEDED: retail + ministry + bank (synthetic, deterministic, offline)")
    if args.exercise:
        for outcome in exercise_demos(engine):
            if "error" in outcome:
                print(f"EXERCISED: {outcome['stage']} FAILED - {outcome['error']}")
            else:
                print(f"EXERCISED: {outcome['stage']} -> {outcome['status']} ({outcome['run_id']})")
        totals = observability.calls.totals()
        print(f"MODEL CALLS AT START-UP: {totals['calls']} on the {settings.adapter} provider")
    print(f"DATABASE: {settings.database_url} (migrations at {revision})")
    print(f"DASHBOARD: http://{args.host}:{args.port}")
    print(f"API DOCS:  http://{args.host}:{args.port}/api/v1/docs")
    if settings.demo_mode:
        print("MODE: demo mode - no login; decisions are recorded as human:demo")
    for source, secret in inbound.items():
        print(f"WEBHOOK IN: POST /api/v1/webhooks/{source} - secret {secret}")
    if subscription_id:
        print(f"WEBHOOK OUT: every event -> {receiver_url} (subscription {subscription_id})")
    print(f"(inbound secrets also written to {DEMO_SECRETS_FILE})")
    if settings.demo_readonly:
        print("MODE: READ-ONLY public demo - every mutation is refused (ATMPL_DEMO_READONLY=1)")
    uvicorn.run(
        create_app(engine, settings, observability=observability),
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


def _command_redteam() -> int:
    outcomes = run_all()
    for outcome in outcomes:
        print(f"{outcome.case_id}/{outcome.demo}: {outcome.status} - {outcome.detail}")
    blocked = sum(outcome.status == "BLOCKED" for outcome in outcomes)
    print(f"REDTEAM: {blocked}/{len(outcomes)} BLOCKED")
    return 0 if blocked == len(outcomes) else 1



def _command_evals(args: argparse.Namespace) -> int:
    if args.evals_command == "run":
        try:
            report = run_suite(provider=args.provider, only=args.only)
        except NoCasesFound as exc:
            print(f"EVALS REFUSED: {exc}")
            return 2
        json_path, md_path = write_report(report, args.out)
        totals = report.as_dict()["totals"]
        print(f"EVALS: {report.provider} ({report.model}) - {len(report.outcomes)} case(s)")
        for name, entry in report.by_category().items():
            print(
                f"  {name:<18} {entry['pass']}/{entry['total']} pass"
                f"  fail={entry['fail']} error={entry['error']} skipped={entry['skipped']}"
            )
        for outcome in report.outcomes:
            if outcome.status in {"fail", "error"}:
                print(f"  FAILED {outcome.id}: {'; '.join(outcome.reasons) or outcome.detail}")
        print(
            f"PASS RATE: {totals['pass_rate']:.3f} "
            f"({totals['passed']}/{totals['scored']} scored, {totals['skipped']} skipped)"
        )
        print(f"GATED PASS RATE: {totals['gated_pass_rate']:.3f}")
        print(f"REPORT: {json_path}")
        print(f"SUMMARY: {md_path}")
        return 0 if totals["failed"] == 0 and totals["errored"] == 0 else 1

    if args.report:
        payload = json.loads(args.report.read_text(encoding="utf-8"))
        rate = float(payload["totals"]["gated_pass_rate"])
        failures = [
            case
            for case in payload["cases"]
            if case["gated"] and case["status"] in {"fail", "error"}
        ]
        source = str(args.report)
    else:
        try:
            report = run_suite()
        except NoCasesFound as exc:
            print(f"GATE FAIL: {exc}")
            return 1
        rate = report.gated_pass_rate
        failures = [
            {"id": o.id, "reasons": o.reasons}
            for o in report.gated_outcomes
            if o.status in {"fail", "error"}
        ]
        source = "a fresh run"
    if rate < args.min_pass:
        print(f"GATE FAIL: gated pass rate {rate:.3f} < required {args.min_pass:.3f} ({source})")
        for case in failures:
            print(f"  {case['id']}: {'; '.join(case.get('reasons') or []) or 'failed'}")
        return 1
    print(f"GATE PASS: gated pass rate {rate:.3f} >= required {args.min_pass:.3f} ({source})")
    return 0


def _command_discover(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    observability = build_observability(settings, install=False)
    router = ProviderRouter(settings, observability=observability)
    print(f"PROVIDER: {router.primary.name} ({router.primary.model})")
    try:
        result = run_discovery(
            router,
            description=args.text,
            template=args.template,
            organization=args.org,
        )
    except AgentRefused as exc:
        print(f"REFUSED: {exc}")
        for index, item in enumerate(exc.follow_ups, start=1):
            print(f"  {index}. {item.get('placeholder')}: {item.get('problem')}")
        return 2
    print(f"TEMPLATE: {result.template}")
    print(f"ORGANIZATION: {result.organization}")
    print(yaml.safe_dump(result.answers, sort_keys=False, allow_unicode=True).rstrip())
    if result.open_questions:
        print("OPEN QUESTIONS (a human still has to settle these):")
        for question in result.open_questions:
            print(f"  - {question}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            yaml.safe_dump(result.answers, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        print(f"ANSWERS: {args.output}")
    totals = observability.calls.totals()
    print(
        f"MODEL CALLS: {totals['calls']} "
        f"({totals['tokens_in']} in / {totals['tokens_out']} out tokens, "
        f"estimated ${totals['cost_estimate_usd']:.6f})"
    )
    print("This is a DRAFT questionnaire. A human reviews it before `resolve` compiles it.")
    return 0


def _command_providers(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    router = ProviderRouter(settings, observability=build_observability(settings, install=False))
    prices = router.prices
    print(f"PRICE TABLE: as of {prices.as_of} (estimates only, not billing)")
    for status in router.status():
        state = "configured" if status.configured else "NOT CONFIGURED (no key)"
        priced = "priced" if prices.knows(status.name, status.model) else "not priced"
        print(
            f"{status.role:<8} {status.name:<10} {status.model:<24} "
            f"{state:<24} breaker={status.breaker} {priced}"
        )
    if not args.ping:
        print("(add --ping to make one real call to each configured provider)")
        return 0
    for provider in router.chain:
        if not provider.configured():
            print(f"PING {provider.name}: skipped, not configured")
            continue
        try:
            result = provider.complete([Message("user", "Reply with the single word: ready")])
        except ProviderError as exc:
            print(f"PING {provider.name}: FAILED ({exc.kind}) {exc.detail[:120]}")
            continue
        print(
            f"PING {provider.name}: ok in {result.latency_ms:.0f} ms, "
            f"{result.usage.input_tokens} in / {result.usage.output_tokens} out tokens"
        )
    return 0


def _command_audit_verify(args: argparse.Namespace) -> int:
    result = verify_audit(args.path)
    if result.valid:
        print(f"AUDIT VERIFIED: {result.count} records - last hash {result.last_hash}")
        return 0
    print(f"AUDIT TAMPERING DETECTED: {result.error}")
    return 1


def _command_db(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    if args.db_command == "upgrade":
        revision = upgrade_database(settings.database_url)
        print(f"DB UPGRADED: {settings.database_url} is at revision {revision}")
        return 0
    print(f"DB REVISION: {current_revision(settings.database_url) or 'none'}")
    return 0


def _command_keys(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    engine = _open_engine(settings)
    with engine.database.session() as session:
        if args.keys_command == "create":
            try:
                issued = create_api_key(session, name=args.name, scopes=args.scopes)
            except (CredentialError, ValueError) as exc:
                print(f"REFUSED: {exc}")
                return 2
            print("API KEY CREATED (shown once - store it now):")
            print(f"  name:   {issued.name}")
            print(f"  scopes: {', '.join(issued.scopes)}")
            print(f"  key:    {issued.token}")
            return 0
        if args.keys_command == "revoke":
            if revoke_api_key(session, args.name):
                print(f"REVOKED: {args.name}")
                return 0
            print(f"NOT FOUND (or already revoked): {args.name}")
            return 2
        rows = session.scalars(select(ApiKey).order_by(ApiKey.created_at)).all()
        if not rows:
            print("No API keys exist yet. Create one: atmpl keys create --name <client> --scopes …")
            return 0
        for row in rows:
            state = "revoked" if row.revoked_at else "active"
            used = row.last_used_at.isoformat() if row.last_used_at else "never used"
            print(f"{row.name}  [{state}]  scopes={','.join(row.scopes)}  last_used={used}")
        return 0


def _command_clients(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    engine = _open_engine(settings)
    with engine.database.session() as session:
        try:
            issued = create_oauth_client(
                session,
                name=args.name,
                scopes=args.scopes,
                client_id=args.client_id,
            )
        except (CredentialError, ValueError) as exc:
            print(f"REFUSED: {exc}")
            return 2
    print("OAUTH CLIENT CREATED (secret shown once):")
    print(f"  client_id:     {issued.client_id}")
    print(f"  client_secret: {issued.client_secret}")
    print(f"  scopes:        {', '.join(issued.scopes)}")
    return 0


def _command_users(args: argparse.Namespace) -> int:
    password = os.getenv("ATMPL_NEW_PASSWORD")
    if not password:
        password = getpass.getpass("New dashboard password: ") if sys.stdin.isatty() else ""
    if not password:
        print("REFUSED: no password supplied (prompt or ATMPL_NEW_PASSWORD).")
        return 2
    print("Put this in the environment; the plaintext is never stored:")
    print(f'ATMPL_ADMIN_PASSWORD_HASH="{hash_password(password)}"')
    return 0


def _command_webhooks(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    engine = _open_engine(settings)
    context = build_context(engine, settings)
    if args.webhooks_command == "sources":
        secrets_by_source = seed_inbound_sources(engine)
        if not secrets_by_source:
            print("No inbound sources are declared. Run `python -m atmpl demo up` first.")
            return 0
        for source, secret in secrets_by_source.items():
            print(f"{source}: POST /api/v1/webhooks/{source}  secret={secret}")
        return 0
    outcomes = deliver_pending(context, ignore_backoff=args.ignore_backoff)
    if not outcomes:
        print("WEBHOOKS: nothing due")
        return 0
    summary = ", ".join(f"{count} {status}" for status, count in sorted(outcomes.items()))
    print(f"WEBHOOKS: {summary}")
    return 0


def _command_doctor() -> int:
    settings = settings_from_env()
    engine = _open_engine(settings)
    context = build_context(engine, settings)
    inspector = inspect(engine.database.engine)
    jwt_state = "configured" if not context.secrets.is_ephemeral("jwt_signing_key") else (
        "EPHEMERAL (restarts invalidate tokens)"
    )
    session_state = "configured" if not context.secrets.is_ephemeral("session_signing_key") else (
        "EPHEMERAL (restarts invalidate sessions)"
    )
    api_state = (
        "open (ATMPL_DEMO_OPEN_API=1)" if settings.demo_open_api else "credential required"
    )
    worker_state = "on" if settings.webhook_worker else "off (run: atmpl webhooks deliver)"
    lines = [
        f"database        {settings.database_url}",
        f"tables          {len(inspector.get_table_names())}",
        f"migrations      {current_revision(settings.database_url) or 'not stamped'}",
        f"adapter         {settings.adapter}",
        f"secrets backend {settings.secrets_backend}",
        f"jwt key         {jwt_state}",
        f"session key     {session_state}",
        f"dashboard       {'DEMO MODE (no login)' if settings.demo_mode else 'login required'}",
        f"api             {api_state}",
        f"rate limit      {settings.rate_limit}",
        f"cors origins    {', '.join(settings.allowed_origins) or 'none (same-origin only)'}",
        f"webhook worker  {worker_state}",
    ]
    print("\n".join(lines))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return _command_init(args)
    if args.command == "resolve":
        return _command_resolve(args)
    if args.command == "run":
        return _command_run(args)
    if args.command == "demo" and args.demo_command == "up":
        return _command_demo_up(args)
    if args.command == "redteam":
        return _command_redteam()
    if args.command == "evals":
        return _command_evals(args)
    if args.command == "discover":
        return _command_discover(args)
    if args.command == "providers":
        return _command_providers(args)
    if args.command == "audit" and args.audit_command == "verify":
        return _command_audit_verify(args)
    if args.command == "db":
        return _command_db(args)
    if args.command == "keys":
        return _command_keys(args)
    if args.command == "clients":
        return _command_clients(args)
    if args.command == "users":
        return _command_users(args)
    if args.command == "webhooks":
        return _command_webhooks(args)
    if args.command == "doctor":
        return _command_doctor()
    parser.error("Unsupported command")
    return 2


def run() -> None:
    """Console-script entry point (`atmpl ...`)."""
    raise SystemExit(main())
