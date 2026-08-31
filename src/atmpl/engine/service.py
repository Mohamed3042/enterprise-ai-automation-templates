"""Workflow execution with policy gates, human decisions, and complete audit emission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from atmpl.adapters.base import LLMAdapter, create_adapter
from atmpl.audit import AuditLog
from atmpl.engine.database import Database, Organization, Run, Stage, Workflow, new_id
from atmpl.guardrails.constants import G1_RISK_TIERS, G6_KILL_SWITCH
from atmpl.guardrails.decisions import (
    GuardrailRejection,
    approve_medium,
    complete_low,
    decide_high,
)
from atmpl.guardrails.policy import DeterministicPolicyEngine, check_ai_enabled
from atmpl.models import (
    AI_ACTIONS,
    EngineResult,
    InstantiatedWorkflow,
    RiskTier,
    SignedHumanDecision,
    StageStatus,
)


class AutomationEngine:
    def __init__(
        self,
        database: Database,
        audit_path: Path,
        adapter: LLMAdapter | None = None,
    ) -> None:
        self.database = database
        self.audit = AuditLog(audit_path, database.session)
        self.adapter = adapter or create_adapter()
        self.policy = DeterministicPolicyEngine()

    def create_organization(
        self,
        *,
        organization_id: str,
        name: str,
        sector: str,
        profile: dict[str, Any],
    ) -> Organization:
        with self.database.session() as session:
            existing = session.get(Organization, organization_id)
            if existing:
                return existing
            org = Organization(
                id=organization_id,
                name=name,
                sector=sector,
                profile=profile,
                kill_switch=False,
            )
            session.add(org)
            session.commit()
        self.audit.append(
            "organization_created",
            actor="system",
            organization_id=organization_id,
            payload={"name": name, "synthetic": True},
        )
        return org

    def create_workflow(
        self,
        *,
        workflow_id: str,
        organization_id: str,
        workflow: InstantiatedWorkflow,
    ) -> Workflow:
        with self.database.session() as session:
            existing = session.get(Workflow, workflow_id)
            if existing:
                return existing
            db_workflow = Workflow(
                id=workflow_id,
                organization_id=organization_id,
                name=workflow.metadata.name,
                template_id=workflow.metadata.id,
                spec=workflow.model_dump(mode="json"),
            )
            session.add(db_workflow)
            session.commit()
        self.audit.append(
            "workflow_instantiated",
            actor="system",
            organization_id=organization_id,
            payload={"workflow_id": workflow_id, "template_id": workflow.metadata.id},
        )
        return db_workflow

    def start_run(
        self,
        *,
        workflow_id: str,
        title: str,
        region: str,
        run_id: str | None = None,
        stage_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> Run:
        stage_overrides = stage_overrides or {}
        with self.database.session() as session:
            workflow = session.get(Workflow, workflow_id)
            if not workflow:
                raise KeyError(f"Workflow not found: {workflow_id}")
            selected_id = run_id or new_id("run")
            existing = session.get(Run, selected_id)
            if existing:
                return existing
            spec = InstantiatedWorkflow.model_validate(workflow.spec)
            run = Run(
                id=selected_id,
                workflow_id=workflow_id,
                organization_id=workflow.organization_id,
                title=title,
                region=region,
                status="active",
                synthetic=True,
            )
            for position, definition in enumerate(spec.stages, start=1):
                override = stage_overrides.get(definition.id, {})
                run.stages.append(
                    Stage(
                        stage_key=definition.id,
                        name=definition.name,
                        position=position,
                        action_type=definition.action_type.value,
                        risk_tier=definition.risk_tier.value,
                        status=override.get("status", StageStatus.PENDING.value),
                        input_data=override.get("input_data", definition.inputs),
                        output_schema=definition.outputs,
                        draft=override.get("draft"),
                        decision=override.get("decision"),
                        escalation_reason=override.get("escalation_reason"),
                        allowed_roles=override.get("allowed_roles", definition.approval_roles),
                    )
                )
            session.add(run)
            session.commit()
        self.audit.append(
            "run_started",
            actor="system",
            organization_id=run.organization_id,
            run_id=run.id,
            payload={"title": title, "region": region, "synthetic": True},
        )
        return run

    def _stage(self, session, run_id: str, stage_key: str) -> Stage:
        stage = session.scalar(
            select(Stage).where(Stage.run_id == run_id, Stage.stage_key == stage_key)
        )
        if not stage:
            raise KeyError(f"Stage '{stage_key}' was not found in run '{run_id}'.")
        return stage

    def _block(
        self,
        *,
        run_id: str,
        stage_key: str,
        guardrail: str,
        detail: str,
        code: str,
    ) -> EngineResult:
        with self.database.session() as session:
            stage = self._stage(session, run_id, stage_key)
            stage.status = StageStatus.BLOCKED.value
            stage.escalation_reason = detail
            stage.run.status = "escalated"
            org_id, stage_id = stage.run.organization_id, stage.id
            session.commit()
        self.audit.append(
            "stage_blocked",
            actor="policy-engine",
            organization_id=org_id,
            run_id=run_id,
            stage_id=stage_id,
            payload={"guardrail": guardrail, "code": code, "detail": detail},
        )
        return EngineResult(
            accepted=False,
            status=StageStatus.BLOCKED,
            guardrail=guardrail,
            detail=detail,
        )

    def process_ai_stage(
        self,
        run_id: str,
        stage_key: str,
        payload: dict[str, Any],
    ) -> EngineResult:
        with self.database.session() as session:
            stage = self._stage(session, run_id, stage_key)
            run = stage.run
            org = run.organization
            if stage.action_type not in {action.value for action in AI_ACTIONS}:
                raise GuardrailRejection(
                    G1_RISK_TIERS,
                    f"Stage '{stage_key}' is not an AI drafting stage.",
                )
            if stage.status not in {StageStatus.PENDING.value, StageStatus.PARKED.value}:
                raise GuardrailRejection(
                    G1_RISK_TIERS,
                    f"Stage '{stage_key}' cannot be drafted from status '{stage.status}'.",
                )
            enabled = check_ai_enabled(org.kill_switch)
            if not enabled.allowed:
                stage.status = StageStatus.PARKED.value
                run.status = "parked"
                stage_id, org_id = stage.id, org.id
                session.commit()
                violation = enabled.violations[0]
                self.audit.append(
                    "ai_stage_parked",
                    actor="kill-switch",
                    organization_id=org_id,
                    run_id=run_id,
                    stage_id=stage_id,
                    payload={"guardrail": G6_KILL_SWITCH, "detail": violation.message},
                )
                return EngineResult(
                    accepted=False,
                    status=StageStatus.PARKED,
                    guardrail=G6_KILL_SWITCH,
                    detail=violation.message,
                )
            allowed_region = run.region
            allowed_scope = set(stage.input_data.get("allowed_actions", [])) or None
            output_schema = dict(stage.output_schema)

        preflight = self.policy.preflight(
            payload,
            allowed_region=allowed_region,
            allowed_scope=allowed_scope,
        )
        if not preflight.allowed:
            violation = preflight.violations[0]
            return self._block(
                run_id=run_id,
                stage_key=stage_key,
                guardrail=violation.guardrail,
                code=violation.code,
                detail=violation.message,
            )

        output = self.adapter.generate(stage_key, preflight.sanitized)
        required_fields = set(output_schema.get("required", []))
        bounds = {
            name: (float(values[0]), float(values[1]))
            for name, values in output_schema.get("numeric_bounds", {}).items()
        }
        postflight = self.policy.postflight(
            output.content,
            required_fields=required_fields,
            numeric_bounds=bounds,
        )
        if not postflight.allowed:
            violation = postflight.violations[0]
            return self._block(
                run_id=run_id,
                stage_key=stage_key,
                guardrail=violation.guardrail,
                code=violation.code,
                detail=violation.message,
            )

        with self.database.session() as session:
            stage = self._stage(session, run_id, stage_key)
            stage.draft = output.model_dump(mode="json")
            stage.status = StageStatus.DRAFT_READY.value
            org_id, stage_id = stage.run.organization_id, stage.id
            session.commit()
        self.audit.append(
            "ai_draft_created",
            actor=f"adapter:{output.adapter}",
            organization_id=org_id,
            run_id=run_id,
            stage_id=stage_id,
            payload={"input_hash": output.input_hash, "business_transition": False},
        )
        return EngineResult(
            accepted=True,
            status=StageStatus.DRAFT_READY,
            detail="AI output is attached as a draft; no business transition occurred.",
            output=output.content,
        )

    def complete_low_stage(self, run_id: str, stage_key: str) -> EngineResult:
        outcome = complete_low()
        with self.database.session() as session:
            stage = self._stage(session, run_id, stage_key)
            if stage.risk_tier != RiskTier.LOW.value:
                raise GuardrailRejection(G1_RISK_TIERS, "Only LOW stages may auto-complete.")
            if stage.status != StageStatus.PENDING.value:
                raise GuardrailRejection(
                    G1_RISK_TIERS,
                    f"LOW stage cannot complete from status '{stage.status}'.",
                )
            stage.status = outcome.status.value
            org_id, stage_id = stage.run.organization_id, stage.id
            session.commit()
        self.audit.append(
            "low_stage_completed",
            actor="system",
            organization_id=org_id,
            run_id=run_id,
            stage_id=stage_id,
            payload={"guardrail": G1_RISK_TIERS},
        )
        return EngineResult(
            accepted=True,
            status=outcome.status,
            guardrail=G1_RISK_TIERS,
            detail="LOW stage completed by deterministic system action.",
        )

    def submit_human_decision(
        self,
        run_id: str,
        stage_key: str,
        decision: SignedHumanDecision,
    ) -> EngineResult:
        with self.database.session() as session:
            stage = self._stage(session, run_id, stage_key)
            try:
                if stage.status not in {
                    StageStatus.PENDING.value,
                    StageStatus.DRAFT_READY.value,
                }:
                    raise GuardrailRejection(
                        G1_RISK_TIERS,
                        f"Stage cannot be decided from status '{stage.status}'.",
                    )
                if (
                    stage.action_type in {action.value for action in AI_ACTIONS}
                    and stage.status != StageStatus.DRAFT_READY.value
                ):
                    raise GuardrailRejection(
                        G1_RISK_TIERS,
                        "An AI stage cannot receive human approval "
                        "before its validated draft exists.",
                    )
                if stage.risk_tier == RiskTier.HIGH.value:
                    outcome = decide_high(decision, stage.allowed_roles)
                elif stage.risk_tier == RiskTier.MEDIUM.value:
                    outcome = approve_medium(decision, stage.allowed_roles)
                else:
                    raise GuardrailRejection(
                        G1_RISK_TIERS,
                        "LOW stages use the deterministic LOW completion path.",
                    )
            except GuardrailRejection as exc:
                org_id, stage_id = stage.run.organization_id, stage.id
                session.rollback()
                self.audit.append(
                    "human_decision_rejected",
                    actor=decision.actor,
                    organization_id=org_id,
                    run_id=run_id,
                    stage_id=stage_id,
                    payload={
                        "guardrail": exc.guardrail,
                        "role": decision.role,
                        "decision": decision.decision.value,
                        "reason": str(exc),
                    },
                )
                raise

            stage.status = outcome.status.value
            stage.decision = decision.model_dump(mode="json")
            org_id, stage_id = stage.run.organization_id, stage.id
            if outcome.status == StageStatus.REJECTED:
                stage.run.status = "rejected"
            session.commit()

        self.audit.append(
            "human_decision_recorded",
            actor=decision.actor,
            organization_id=org_id,
            run_id=run_id,
            stage_id=stage_id,
            payload={
                "guardrail": outcome.guardrail,
                "role": decision.role,
                "timestamp": decision.timestamp.isoformat(),
                "decision": decision.decision.value,
                "reason": decision.reason,
                "signature": decision.signature,
            },
        )
        return EngineResult(
            accepted=True,
            status=outcome.status,
            guardrail=outcome.guardrail,
            detail="Signed human decision recorded and audited.",
        )

    def set_kill_switch(
        self,
        organization_id: str,
        enabled: bool,
        decision: SignedHumanDecision,
    ) -> EngineResult:
        decide_high(decision, ["risk_owner", "chief_risk_officer"])
        with self.database.session() as session:
            org = session.get(Organization, organization_id)
            if not org:
                raise KeyError(f"Organization not found: {organization_id}")
            org.kill_switch = enabled
            runs = session.scalars(select(Run).where(Run.organization_id == organization_id)).all()
            for run in runs:
                for stage in run.stages:
                    if stage.action_type in {action.value for action in AI_ACTIONS}:
                        if enabled and stage.status in {
                            StageStatus.PENDING.value,
                            StageStatus.DRAFT_READY.value,
                        }:
                            stage.status = StageStatus.PARKED.value
                            run.status = "parked"
                        elif not enabled and stage.status == StageStatus.PARKED.value:
                            stage.status = StageStatus.PENDING.value
                            if run.status == "parked":
                                run.status = "active"
            session.commit()
        self.audit.append(
            "kill_switch_changed",
            actor=decision.actor,
            organization_id=organization_id,
            payload={
                "guardrail": G6_KILL_SWITCH,
                "enabled": enabled,
                "role": decision.role,
                "reason": decision.reason,
                "signature": decision.signature,
            },
        )
        return EngineResult(
            accepted=True,
            status=StageStatus.PARKED if enabled else StageStatus.PENDING,
            guardrail=G6_KILL_SWITCH,
            detail="Kill switch change was human-signed and audited as HIGH risk.",
        )
