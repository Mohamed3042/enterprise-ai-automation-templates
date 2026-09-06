"""The discovery agent: a paragraph about a process in, a typed questionnaire out.

The consulting step ATMPL already models is *discovery* — asking an organisation the
questions a governed workflow needs answered, then compiling the answers. This agent does
the first half from a free-text description, and it is held to exactly the boundary a
consultant is held to: its answer is validated by the template's own placeholder model
(`atmpl.models`) through :func:`atmpl.intake.resolver.validate_answers`. A field the
template does not declare is a refusal, not a merge — an agent that can widen the
questionnaire is an agent that can widen the contract the compiler checks.

The agent never decides anything. Its output is a *draft questionnaire* for a human to
review before `resolve` compiles it (invariants G1, G2).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from atmpl.catalog import canonical_name, placeholder_model
from atmpl.intake.resolver import answer_skeleton, question_items, validate_answers
from atmpl.providers.router import ProviderRouter
from atmpl.telemetry.context import call_context

TEMPLATES = ("retail_refund", "lesson_preparation", "loan_triage")

INSTRUCTIONS = """You run the discovery interview for a governed automation template.

You are given a free-text description of how an organisation runs one business process.
Turn it into that template's typed questionnaire.

Rules you must follow:
1. Answer every field the questionnaire declares, and only those fields. Call
   `list_required_fields` to see them with their questions and declared types. Inventing a
   field is a failure, not a helpful addition.
2. Where a field offers a fixed set of values, use one of those exact strings.
3. `locales` are BCP-47 codes such as `en-GB` or `ar-KW`, never language names.
4. Role names are lower_snake_case identifiers such as `store_manager`, not job titles.
5. Authority maps are lists of `{key, value}` rows: the decision or amount band, and the
   single named role allowed to approve it. Never return an empty list.
6. For every field, add one `rationales` entry: the field's dotted path, one line of
   reasoning, and `stated` if the description says it or `inferred` if you concluded it.
   Never label an inference as stated.
7. If the description does not settle something, choose the most conservative option and
   add the open question to `open_questions`.
8. You are drafting for a human reviewer. You never approve, decide, or state that anything
   has been actioned."""


class MapEntry(BaseModel):
    """One row of an authority map.

    A free-form `dict[str, str]` is expressible in JSON Schema but not in any provider's
    structured-output mode: measured on `gemini-3.6-flash`, an object with no declared
    properties comes back as `{}` every time, because the model is given no slot to fill.
    Rows have slots, so the map is carried as rows and rebuilt after validation.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, description="The decision, scenario or amount band.")
    value: str = Field(min_length=1, description="The single named role that may approve it.")


class Rationale(BaseModel):
    model_config = ConfigDict(extra="forbid")

    placeholder: str = Field(description="Dotted path, exactly as the questionnaire lists it.")
    rationale: str = Field(min_length=3, description="One line: why this value.")
    confidence: Literal["stated", "inferred"] = "inferred"


@dataclass
class DiscoveryDeps:
    """What the agent's tools may read. No engine, no database, no secrets."""

    template: str


class AgentRefused(ValueError):
    """The draft could not be turned into a valid questionnaire. Carries the field list."""

    def __init__(self, message: str, follow_ups: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.follow_ups = follow_ups


@dataclass(frozen=True)
class DiscoveryResult:
    template: str
    organization: str
    answers: dict[str, Any]
    rationales: list[dict[str, Any]]
    open_questions: list[str]
    provider: str
    model: str


# --------------------------------------------------------------------------- typed output


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    origin = get_origin(annotation)
    candidates = get_args(annotation) if origin in (Union, UnionType) else (annotation,)
    for candidate in candidates:
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _wire_model(model: type[BaseModel], prefix: str) -> type[BaseModel]:
    """The placeholder model, shaped so a provider can actually fill it in.

    Same fields, same names, same enums; only free-form maps change shape. The result is
    what the model answers; the *real* placeholder model is what validates the answer.
    """
    fields: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        annotation = field.annotation
        nested = _nested_model(annotation)
        extra = field.json_schema_extra or {}
        description = str(extra.get("question", f"Provide {name}."))
        if nested is not None:
            annotation = _wire_model(nested, f"{prefix}_{name}")
        elif get_origin(annotation) is dict:
            annotation = list[MapEntry]
            description = f"{description} One row per entry."
        fields[name] = (annotation, Field(description=description))
    return create_model(
        f"Wire_{prefix}_{model.__name__}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


@lru_cache(maxsize=8)
def output_model(template: str) -> type[BaseModel]:
    """The agent's result type for one template: its answers, reasons and open questions."""
    canonical = canonical_name(template)
    answers = _wire_model(placeholder_model(canonical), canonical)
    return create_model(
        f"DiscoveryDraft_{canonical}",
        __config__=ConfigDict(extra="forbid"),
        answers=(answers, Field(description="Every questionnaire field, answered.")),
        rationales=(
            list[Rationale],
            Field(description="One entry per answered field, with its reasoning."),
        ),
        open_questions=(
            list[str],
            Field(default_factory=list, description="What a human still has to settle."),
        ),
    )


def to_answers(value: Any) -> Any:
    """Rebuild the real answers document: map rows become a dict again."""
    if isinstance(value, BaseModel):
        return to_answers(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: to_answers(item) for key, item in value.items()}
    if isinstance(value, list):
        rows = [item for item in value if isinstance(item, dict) and set(item) == {"key", "value"}]
        if value and len(rows) == len(value):
            return {str(row["key"]): str(row["value"]) for row in rows}
        return [to_answers(item) for item in value]
    return value


# --------------------------------------------------------------------------- the agent


CATALOG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "retail_refund": ("refund", "refunds", "return", "returns", "retail", "store", "shop"),
    "lesson_preparation": ("lesson", "lessons", "school", "curriculum", "teacher", "ministry"),
    "loan_triage": ("loan", "loans", "credit", "bank", "lending", "underwriting", "applicant"),
}


def search_templates(query: str) -> list[dict[str, str]]:
    """Deterministic keyword match over the catalog — the same answer for every provider."""
    words = {word.strip(".,;:!?()").lower() for word in query.split()}
    scored = [
        (sum(1 for keyword in keywords if keyword in words), name)
        for name, keywords in CATALOG_KEYWORDS.items()
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{"template": name, "matched_terms": str(score)} for score, name in scored]


def pick_template(description: str) -> str:
    return search_templates(description)[0]["template"]


def build_agent(router: ProviderRouter, *, template: str, organization: str) -> Agent:
    """An agent bound to one template, running on the deployment's provider router."""
    from atmpl.agents.model import router_model

    agent = Agent(
        router_model(
            router,
            task="discovery.questionnaire",
            template=template,
            organization=organization,
        ),
        deps_type=DiscoveryDeps,
        output_type=output_model(template),
        instructions=INSTRUCTIONS,
        retries=2,
    )

    @agent.tool
    def list_required_fields(ctx: RunContext[DiscoveryDeps], template: str = "") -> list[dict]:
        """Every placeholder this template needs, with its question and declared type."""
        return question_items(template or ctx.deps.template)

    @agent.tool
    def get_template(ctx: RunContext[DiscoveryDeps], template: str = "") -> dict[str, Any]:
        """The blank answers document for a template: the exact nesting expected."""
        name = canonical_name(template or ctx.deps.template)
        return {"template": name, "skeleton": answer_skeleton(name)}

    @agent.tool_plain
    def search_catalog(query: str) -> list[dict[str, str]]:
        """Find which base template fits a described process."""
        return search_templates(query)

    return agent


def check_rationales(template: str, rationales: list[Rationale]) -> None:
    """A reason may only be given for a field the template actually has."""
    known = {item["placeholder"] for item in question_items(template)}
    invented = sorted({item.placeholder for item in rationales} - known)
    if invented:
        raise AgentRefused(
            f"The agent gave reasons for {len(invented)} field(s) this template does not have.",
            [
                {
                    "placeholder": name,
                    "question": "This placeholder does not exist in the template.",
                    "problem": "invented_field",
                }
                for name in invented
            ],
        )


def run_discovery(
    router: ProviderRouter,
    *,
    description: str,
    template: str | None = None,
    organization: str | None = None,
) -> DiscoveryResult:
    """Draft a questionnaire from a description, then hold it to the typed boundary."""
    resolved_template = canonical_name(template) if template else pick_template(description)
    resolved_org = organization or "Synthetic Organization"
    agent = build_agent(router, template=resolved_template, organization=resolved_org)
    with call_context(template_key=resolved_template, purpose="discovery_agent"):
        try:
            run = agent.run_sync(
                f"Organisation: {resolved_org}\nTemplate: {resolved_template}\n\n"
                f"Process description:\n{description}",
                deps=DiscoveryDeps(template=resolved_template),
            )
        except UnexpectedModelBehavior as exc:
            raise AgentRefused(
                "The agent could not produce an answer in the questionnaire's shape.",
                [{"placeholder": "-", "question": "Typed output failed.", "problem": str(exc)}],
            ) from exc
    draft = run.output
    check_rationales(resolved_template, list(draft.rationales))
    answers = to_answers(draft.answers)
    answers.setdefault("organization", {})["name"] = resolved_org
    follow_ups = validate_answers(resolved_template, answers)
    if follow_ups:
        raise AgentRefused(
            f"The drafted questionnaire does not validate ({len(follow_ups)} field(s)).",
            follow_ups,
        )
    return DiscoveryResult(
        template=resolved_template,
        organization=resolved_org,
        answers=answers,
        rationales=[item.model_dump(mode="json") for item in draft.rationales],
        open_questions=list(draft.open_questions),
        provider=router.primary.name,
        model=router.primary.model,
    )
