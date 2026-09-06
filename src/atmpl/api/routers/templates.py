"""The template catalog: what a consultant can instantiate, and what it will ask for."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from atmpl.api.schemas import ActorOut, StageDefinitionOut, TemplateDetail, TemplateSummary
from atmpl.catalog import ALIASES, canonical_name, load_schema, load_template
from atmpl.security.dependencies import require_scope
from atmpl.security.principals import Scope

router = APIRouter(tags=["templates"], dependencies=[Depends(require_scope(Scope.TEMPLATES_READ))])


def _aliases_for(canonical: str) -> list[str]:
    return sorted(alias for alias, target in ALIASES.items() if target == canonical)


def _summary(canonical: str) -> TemplateSummary:
    document = load_template(canonical)
    return TemplateSummary(
        key=canonical,
        aliases=_aliases_for(canonical),
        name=document.metadata.name,
        sector=document.metadata.sector,
        version=document.metadata.version,
        description=document.metadata.description,
        stage_count=len(document.stages),
    )


@router.get(
    "/templates",
    response_model=list[TemplateSummary],
    summary="List base templates",
    description="Every base template this deployment can compile. Names are stable ids.",
)
async def list_templates() -> list[TemplateSummary]:
    return [_summary(canonical) for canonical in sorted(set(ALIASES.values()))]


@router.get(
    "/templates/{key}",
    response_model=TemplateDetail,
    summary="Read one template",
    description=(
        "Returns the stage graph, the actors, and the JSON Schema of the typed discovery "
        "answers the template needs before it can be compiled."
    ),
)
async def get_template(
    key: str = Path(description="Template id or alias, e.g. `bank` or `loan_triage`."),
) -> TemplateDetail:
    canonical = canonical_name(key)
    document = load_template(canonical)
    summary = _summary(canonical)
    return TemplateDetail(
        **summary.model_dump(),
        actors=[ActorOut(role=a.role, responsibility=a.responsibility) for a in document.actors],
        stages=[
            StageDefinitionOut(
                id=stage.id,
                name=stage.name,
                action_type=stage.action_type.value,
                risk_tier=stage.risk_tier.value,
                sla_hours=stage.sla_hours,
                approval_roles=stage.approval_roles,
                escalation_rules=stage.escalation_rules,
            )
            for stage in document.stages
        ],
        placeholder_schema=load_schema(canonical),
    )
