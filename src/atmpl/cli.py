"""One CLI for discovery, compilation, execution, demos, audit, and red-team proof."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

import uvicorn
import yaml

from atmpl.audit import verify_audit
from atmpl.demos import seed_demo_data
from atmpl.engine.database import Database
from atmpl.engine.service import AutomationEngine
from atmpl.intake.resolver import DiscoveryError, init_discovery, resolve_discovery
from atmpl.models import AI_ACTIONS, InstantiatedWorkflow
from atmpl.redteam.contracts import run_all
from atmpl.web.app import create_app


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

    subcommands.add_parser("redteam", help="Execute R1-R8 across all three demos.")

    audit = subcommands.add_parser("audit", help="Operate on the hash-chained audit ledger.")
    audit_subcommands = audit.add_subparsers(dest="audit_command", required=True)
    audit_verify = audit_subcommands.add_parser("verify", help="Recompute every hash-chain link.")
    audit_verify.add_argument("--path", type=Path, default=Path("var/audit.jsonl"))
    return parser


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
    print(f"stages: {len(workflow.stages)} · source: {workflow.source_template}")
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
    print(f"RUN: {run.id} · parked at the next human gate")
    return 0


def _command_demo_up(args: argparse.Namespace) -> int:
    root = Path.cwd() / "var"
    engine = seed_demo_data(root / "demo.db", root / "audit.jsonl")
    print("SEEDED: retail + ministry + bank (synthetic, deterministic, offline)")
    print(f"DASHBOARD: http://{args.host}:{args.port}")
    uvicorn.run(create_app(engine), host=args.host, port=args.port, log_level="info")
    return 0


def _command_redteam() -> int:
    outcomes = run_all()
    for outcome in outcomes:
        print(f"{outcome.case_id}/{outcome.demo}: {outcome.status} — {outcome.detail}")
    blocked = sum(outcome.status == "BLOCKED" for outcome in outcomes)
    print(f"REDTEAM: {blocked}/{len(outcomes)} BLOCKED")
    return 0 if blocked == len(outcomes) else 1


def _command_audit_verify(args: argparse.Namespace) -> int:
    result = verify_audit(args.path)
    if result.valid:
        print(f"AUDIT VERIFIED: {result.count} records · last hash {result.last_hash}")
        return 0
    print(f"AUDIT TAMPERING DETECTED: {result.error}")
    return 1


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
    if args.command == "audit" and args.audit_command == "verify":
        return _command_audit_verify(args)
    parser.error("Unsupported command")
    return 2
