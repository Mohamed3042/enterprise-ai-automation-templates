"""Deterministic, synthetic demo seeding for the dashboard and portfolio walkthroughs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from atmpl.catalog import PROJECT_ROOT
from atmpl.engine.database import (
    Database,
    Organization,
    RedTeamResult,
    Run,
    WebhookSubscription,
)
from atmpl.engine.service import AutomationEngine
from atmpl.intake.resolver import resolve_discovery
from atmpl.redteam.contracts import EXPECTATIONS, run_all
from atmpl.webhooks.inbound import mapping_block

SYNTHETIC_SIGNATURE = "sig_synthetic_demo_attestation"

#: Inbound sources the demos accept, mapped onto the seeded workflows.
DEMO_INBOUND_SOURCES = {
    "helpdesk": {
        "organization_id": "org_omnimart",
        "workflow_id": "wf_retail_eu",
        "title": "Helpdesk ticket {ticket_id} - refund review",
        "region": "EU",
        "field_map": {
            "ticket_id": "ticket.id",
            "amount": "ticket.refund_amount",
            "reason": "ticket.subject",
            "customer_email": "ticket.requester.email",
        },
    },
    "case_intake": {
        "organization_id": "org_ministry_lab",
        "workflow_id": "wf_ministry_lesson",
        "title": "Case intake {case_id} - lesson review",
        "region": "GLOBAL",
        "field_map": {
            "case_id": "case.reference",
            "subject": "case.subject",
            "submitted_by": "case.submitted_by",
        },
    },
}


def seed_inbound_sources(engine: AutomationEngine) -> dict[str, str]:
    """Declare the demo inbound mappings on their organizations; return their secrets.

    Secrets are generated per database, never committed. ``ATMPL_SECRET_WEBHOOK_INBOUND_<SOURCE>``
    overrides the stored value at request time.
    """
    secrets_by_source: dict[str, str] = {}
    with engine.database.session() as session:
        for source, spec in DEMO_INBOUND_SOURCES.items():
            organization = session.get(Organization, spec["organization_id"])
            if organization is None:
                continue
            profile = dict(organization.profile or {})
            webhooks = dict(profile.get("webhooks") or {})
            inbound = dict(webhooks.get("inbound") or {})
            existing = inbound.get(source)
            block = existing or mapping_block(
                workflow_id=spec["workflow_id"],
                title=spec["title"],
                region=spec["region"],
                field_map=spec["field_map"],
            )
            inbound[source] = block
            webhooks["inbound"] = inbound
            profile["webhooks"] = webhooks
            organization.profile = profile
            secrets_by_source[source] = str(block["secret"])
        session.commit()
    return secrets_by_source


def _draft(task: str, recommendation: str, summary: str, **extra: Any) -> dict[str, Any]:
    return {
        "task": task,
        "input_hash": f"synthetic_{task}_hash",
        "adapter": "mock",
        "content": {
            "summary": summary,
            "recommendation": recommendation,
            **extra,
        },
    }


def _decision(actor: str, role: str, decision: str, reason: str) -> dict[str, Any]:
    return {
        "actor": actor,
        "role": role,
        "decision": decision,
        "reason": reason,
        "signature": SYNTHETIC_SIGNATURE,
        "timestamp": "2026-08-31T09:00:00+00:00",
    }


def _set_run_status(database: Database, run_id: str, status: str) -> None:
    with database.session() as session:
        run = session.get(Run, run_id)
        if run:
            run.status = status
            session.commit()


def _audit_seed_stages(engine: AutomationEngine, run_id: str) -> None:
    with engine.database.session() as session:
        run = session.scalar(select(Run).where(Run.id == run_id))
        if not run:
            return
        snapshots = [
            {
                "stage_id": stage.id,
                "stage_key": stage.stage_key,
                "status": stage.status,
                "risk_tier": stage.risk_tier,
            }
            for stage in run.stages
        ]
        organization_id = run.organization_id
    engine.audit.append(
        "synthetic_seed_snapshot",
        actor="demo-seeder",
        organization_id=organization_id,
        run_id=run_id,
        payload={"synthetic": True, "stages": snapshots},
    )


def _seed_retail(engine: AutomationEngine) -> None:
    answer_paths = {
        "EU": PROJECT_ROOT / "demos" / "retail" / "eu.answers.yaml",
        "US": PROJECT_ROOT / "demos" / "retail" / "us.answers.yaml",
        "GCC": PROJECT_ROOT / "demos" / "retail" / "gcc.answers.yaml",
    }
    workflows = {region: resolve_discovery("retail", path) for region, path in answer_paths.items()}
    engine.create_organization(
        organization_id="org_omnimart",
        name="OmniMart Global",
        sector="Retail",
        profile={"synthetic": True, "regions": ["EU", "US", "GCC"], "locales": ["ar", "en"]},
    )
    for region, workflow in workflows.items():
        engine.create_workflow(
            workflow_id=f"wf_retail_{region.lower()}",
            organization_id="org_omnimart",
            workflow=workflow,
        )

    identical_input = {
        "synthetic_case": "SYN-REFUND-1042",
        "amount": 125,
        "reason": "Unopened synthetic item returned on day 12",
    }
    engine.start_run(
        workflow_id="wf_retail_eu",
        run_id="run_retail_eu_1042",
        title="SYN-REFUND-1042 — EU privacy review route",
        region="EU",
        stage_overrides={
            "receive_request": {"status": "completed", "input_data": identical_input},
            "classify_refund": {
                "status": "draft_ready",
                "input_data": {**identical_input, "region": "EU", "policy_region": "EU"},
                "draft": _draft(
                    "classify_refund",
                    "ROUTE_PRIVACY_REVIEW",
                    "EU pack requires privacy review before manager approval.",
                    confidence=0.88,
                ),
            },
        },
    )
    engine.start_run(
        workflow_id="wf_retail_us",
        run_id="run_retail_us_1042",
        title="SYN-REFUND-1042 — US standard manager route",
        region="US",
        stage_overrides={
            "receive_request": {"status": "completed", "input_data": identical_input},
            "classify_refund": {
                "status": "draft_ready",
                "input_data": {**identical_input, "region": "US", "policy_region": "US"},
                "draft": _draft(
                    "classify_refund",
                    "ROUTE_STANDARD_MANAGER",
                    "US pack routes the identical facts directly to manager review.",
                    confidence=0.88,
                ),
            },
        },
    )
    engine.start_run(
        workflow_id="wf_retail_gcc",
        run_id="run_retail_gcc_blocked",
        title="SYN-REFUND-9001 — blocked instruction injection",
        region="GCC",
        stage_overrides={
            "receive_request": {"status": "completed"},
            "classify_refund": {
                "status": "blocked",
                "escalation_reason": "G3 PROMPT_INJECTION quarantined before adapter access.",
            },
        },
    )
    _set_run_status(engine.database, "run_retail_gcc_blocked", "escalated")
    for run_id in ("run_retail_eu_1042", "run_retail_us_1042", "run_retail_gcc_blocked"):
        _audit_seed_stages(engine, run_id)


def _seed_ministry(engine: AutomationEngine) -> None:
    workflow = resolve_discovery("ministry", PROJECT_ROOT / "demos" / "ministry" / "answers.yaml")
    engine.create_organization(
        organization_id="org_ministry_lab",
        name="Ministry of Education — Synthetic Learning Lab",
        sector="Public education",
        profile={"synthetic": True, "locales": ["ar", "en"], "rtl": True},
    )
    engine.create_workflow(
        workflow_id="wf_ministry_lesson",
        organization_id="org_ministry_lab",
        workflow=workflow,
    )
    engine.start_run(
        workflow_id="wf_ministry_lesson",
        run_id="run_ministry_ar_math",
        title="خطة درس الرياضيات — الصف الخامس",
        region="GLOBAL",
        stage_overrides={
            "submit_lesson": {
                "status": "completed",
                "input_data": {
                    "lesson_title_ar": "خطة درس الرياضيات — الصف الخامس",
                    "lesson_title_en": "Mathematics lesson plan — Grade 5",
                    "subject_ar": "الرياضيات",
                    "subject_en": "Mathematics",
                    "teacher": "المعلمة التجريبية أمل",
                },
            },
            "enrich_lesson": {
                "status": "draft_ready",
                "draft": _draft(
                    "enrich_lesson",
                    "DEPARTMENT_REVIEW",
                    "مسودة إثراء تجريبية متوافقة مع أهداف الدرس — Synthetic aligned draft.",
                    alignment_score=92,
                    bilingual=True,
                ),
            },
        },
    )
    engine.start_run(
        workflow_id="wf_ministry_lesson",
        run_id="run_ministry_en_science",
        title="Synthetic science lesson — ecosystems",
        region="GLOBAL",
        stage_overrides={
            "submit_lesson": {"status": "completed"},
            "enrich_lesson": {
                "status": "completed",
                "draft": _draft(
                    "enrich_lesson",
                    "DEPARTMENT_REVIEW",
                    "Synthetic enrichment aligns objectives and activities.",
                    alignment_score=94,
                    bilingual=True,
                ),
                "decision": _decision(
                    "Synthetic Department Head",
                    "department_head",
                    "approve",
                    "Synthetic objectives were reviewed against the demo framework.",
                ),
            },
            "department_approval": {
                "status": "completed",
                "decision": _decision(
                    "رئيس القسم التجريبي يوسف",
                    "department_head",
                    "approve",
                    "تمت مراجعة الخطة التجريبية والموافقة عليها.",
                ),
            },
            "qa_gate": {
                "status": "completed",
                "decision": _decision(
                    "Synthetic QA Lead",
                    "qa_lead",
                    "approve",
                    "Bilingual synthetic content passed the quality checklist.",
                ),
            },
            "publish_lesson": {"status": "completed"},
        },
    )
    _set_run_status(engine.database, "run_ministry_en_science", "completed")
    for run_id in ("run_ministry_ar_math", "run_ministry_en_science"):
        _audit_seed_stages(engine, run_id)


def _seed_bank(engine: AutomationEngine) -> None:
    workflow = resolve_discovery("bank", PROJECT_ROOT / "demos" / "bank" / "answers.yaml")
    engine.create_organization(
        organization_id="org_gulf_horizon",
        name="Gulf Horizon Bank",
        sector="Banking",
        profile={"synthetic": True, "locales": ["ar", "en"], "data": "fake-only"},
    )
    engine.create_workflow(
        workflow_id="wf_bank_triage",
        organization_id="org_gulf_horizon",
        workflow=workflow,
    )
    ai_route = _draft(
        "credit_summary",
        "ROUTE_TO_TIER_2",
        "Synthetic completeness is 86; route to Tier 2 for a human terminal decision.",
        completeness_score=86,
        risk_flags=["synthetic_income_variance"],
    )
    human_rejection = _decision(
        "Mariam Synthetic",
        "credit_officer_tier_2",
        "reject",
        "OVERRIDE: verified synthetic evidence conflicts; human rejects and documents rationale.",
    )
    engine.start_run(
        workflow_id="wf_bank_triage",
        run_id="run_bank_override",
        title="SYN-LOAN-2048 — human overrides AI routing recommendation",
        region="GCC",
        stage_overrides={
            "receive_application": {
                "status": "completed",
                "input_data": {
                    "synthetic_application": "SYN-LOAN-2048",
                    "amount": 62_000,
                    "currency": "KWD",
                },
            },
            "loan_extract": {
                "status": "completed",
                "draft": _draft(
                    "loan_extract",
                    "VALIDATE_WITH_OFFICER",
                    "Synthetic fields extracted for analyst validation.",
                    completeness_score=86,
                    risk_flags=["synthetic_income_variance"],
                ),
                "decision": _decision(
                    "Omar Synthetic",
                    "credit_analyst",
                    "approve",
                    "Synthetic extraction was checked against the submitted fixture.",
                ),
            },
            "credit_summary": {
                "status": "completed",
                "draft": ai_route,
                "decision": _decision(
                    "Mariam Synthetic",
                    "credit_officer_tier_2",
                    "approve",
                    "Draft accepted as evidence only; no credit outcome delegated to AI.",
                ),
            },
            "terminal_decision": {"status": "rejected", "decision": human_rejection},
            "notify_applicant": {"status": "completed"},
        },
    )
    _set_run_status(engine.database, "run_bank_override", "completed")
    engine.audit.append(
        "human_override_recorded",
        actor="Mariam Synthetic",
        organization_id="org_gulf_horizon",
        run_id="run_bank_override",
        payload={
            "synthetic": True,
            "ai_recommendation": "ROUTE_TO_TIER_2",
            "human_terminal_decision": "reject",
            "reason": human_rejection["reason"],
            "signature": SYNTHETIC_SIGNATURE,
        },
    )
    engine.start_run(
        workflow_id="wf_bank_triage",
        run_id="run_bank_pending",
        title="SYN-LOAN-3021 — awaiting Tier 2 human decision",
        region="GCC",
        stage_overrides={
            "receive_application": {
                "status": "completed",
                "input_data": {
                    "synthetic_application": "SYN-LOAN-3021",
                    "amount": 48_000,
                    "currency": "KWD",
                },
            },
            "loan_extract": {
                "status": "completed",
                "draft": _draft(
                    "loan_extract",
                    "VALIDATE_WITH_OFFICER",
                    "Synthetic application extraction ready for human validation.",
                    completeness_score=91,
                    risk_flags=[],
                ),
                "decision": _decision(
                    "Omar Synthetic",
                    "credit_analyst",
                    "approve",
                    "Synthetic extraction fixture validated.",
                ),
            },
            "credit_summary": {
                "status": "draft_ready",
                "draft": _draft(
                    "credit_summary",
                    "ROUTE_TO_TIER_2",
                    "AI draft only: a Tier 2 human must decide.",
                    completeness_score=91,
                    risk_flags=[],
                ),
            },
        },
    )
    engine.start_run(
        workflow_id="wf_bank_triage",
        run_id="run_bank_blocked",
        title="SYN-LOAN-9999 — blocked cross-customer content",
        region="GCC",
        stage_overrides={
            "receive_application": {"status": "completed"},
            "loan_extract": {
                "status": "blocked",
                "escalation_reason": "G3 FORBIDDEN_CONTENT: synthetic cross-customer PII.",
            },
        },
    )
    _set_run_status(engine.database, "run_bank_blocked", "escalated")
    for run_id in ("run_bank_override", "run_bank_pending", "run_bank_blocked"):
        _audit_seed_stages(engine, run_id)


def _seed_redteam_results(database: Database) -> None:
    outcomes = run_all()
    with database.session() as session:
        if session.scalar(select(func.count(RedTeamResult.id))):
            return
        session.add_all(
            [
                RedTeamResult(
                    case_id=outcome.case_id,
                    demo=outcome.demo,
                    status=outcome.status,
                    expectation=EXPECTATIONS[outcome.case_id],
                    detail=outcome.detail,
                )
                for outcome in outcomes
            ]
        )
        session.commit()


def seed_demo_subscription(engine: AutomationEngine, url: str, secret: str) -> str | None:
    """Point the demos at a receiver (Compose's `receiver` profile). Idempotent by URL."""
    with engine.database.session() as session:
        existing = session.scalar(
            select(WebhookSubscription).where(WebhookSubscription.url == url)
        )
        if existing:
            return existing.id
        subscription = WebhookSubscription(
            url=url,
            description="Synthetic demo receiver",
            secret=secret,
            event_types=[],
            active=True,
        )
        session.add(subscription)
        session.commit()
        return subscription.id


def seed_demo_data(
    db_path: Path,
    audit_path: Path,
    database: Database | None = None,
) -> AutomationEngine:
    database = database or Database(path=db_path)
    engine = AutomationEngine(database, audit_path)
    with database.session() as session:
        already_seeded = bool(session.scalar(select(func.count(Organization.id))))
    if not already_seeded:
        _seed_retail(engine)
        _seed_ministry(engine)
        _seed_bank(engine)
    _seed_redteam_results(database)
    seed_inbound_sources(engine)
    return engine
