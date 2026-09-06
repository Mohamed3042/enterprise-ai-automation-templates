"""Compile consultant discovery answers into a validated workflow specification."""

from __future__ import annotations

import re
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ValidationError

from atmpl.catalog import canonical_name, load_template, placeholder_model
from atmpl.models import InstantiatedWorkflow

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
EXPECTED_RETAIL_PACKS = {
    "EU": "eu_gdpr_14_day",
    "US": "us_standard",
    "GCC": "gcc_consumer",
}


class DiscoveryError(ValueError):
    """A consultant-facing list of unresolved discovery questions."""


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    origin = get_origin(annotation)
    candidates = get_args(annotation) if origin in (Union, UnionType) else (annotation,)
    for candidate in candidates:
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _type_label(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is not None:
        args = ", ".join(getattr(item, "__name__", str(item)) for item in get_args(annotation))
        return f"{getattr(origin, '__name__', str(origin))}[{args}]"
    return getattr(annotation, "__name__", str(annotation))


def _question_map(model: type[BaseModel], prefix: str = "") -> dict[str, tuple[str, str]]:
    questions: dict[str, tuple[str, str]] = {}
    for name, field in model.model_fields.items():
        dotted = f"{prefix}.{name}" if prefix else name
        nested = _nested_model(field.annotation)
        if nested:
            questions.update(_question_map(nested, dotted))
            continue
        extra = field.json_schema_extra or {}
        questions[dotted] = (
            str(extra.get("question", f"Provide {dotted}.")),
            _type_label(field.annotation),
        )
    return questions


def _answer_skeleton(model: type[BaseModel], org: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        nested = _nested_model(field.annotation)
        if nested:
            result[name] = _answer_skeleton(nested, org)
        elif name == "name" and org:
            result[name] = org
        elif get_origin(field.annotation) is list:
            result[name] = []
        elif get_origin(field.annotation) is dict:
            result[name] = {}
        else:
            result[name] = None
    return result


def question_items(template_name: str) -> list[dict[str, str]]:
    """The discovery questions for a template, in asking order (API + CLI share this)."""
    model = placeholder_model(canonical_name(template_name))
    return [
        {"placeholder": path, "question": prompt, "type": type_label}
        for path, (prompt, type_label) in _question_map(model).items()
    ]


def answer_skeleton(template_name: str, organization: str | None = None) -> dict[str, Any]:
    """A blank, correctly shaped answers document for a template."""
    return _answer_skeleton(placeholder_model(canonical_name(template_name)), organization)


def validate_answers(template_name: str, answers: dict[str, Any]) -> list[dict[str, str]]:
    """Return the outstanding follow-ups; an empty list means the answers compile."""
    canonical = canonical_name(template_name)
    model = placeholder_model(canonical)
    questions = _question_map(model)
    try:
        validated = model.model_validate(answers)
    except ValidationError as exc:
        return [
            {
                "placeholder": ".".join(str(part) for part in error["loc"]),
                "question": questions.get(
                    ".".join(str(part) for part in error["loc"]),
                    (f"Correct {error['loc']}.", ""),
                )[0],
                "problem": error["msg"],
            }
            for error in exc.errors()
        ]
    if canonical == "retail_refund":
        regional = validated.model_dump(mode="json")["regional"]
        expected = EXPECTED_RETAIL_PACKS[regional["region"]]
        if expected != regional["policy_pack"]:
            return [
                {
                    "placeholder": "regional.policy_pack",
                    "question": questions["regional.policy_pack"][0],
                    "problem": (
                        f"'{regional['policy_pack']}' cannot cross the "
                        f"{regional['region']} boundary; expected '{expected}'."
                    ),
                }
            ]
    return []


def init_discovery(
    template_name: str,
    org: str,
    output_root: Path | None = None,
) -> tuple[Path, Path]:
    canonical = canonical_name(template_name)
    model = placeholder_model(canonical)
    output_root = output_root or Path.cwd() / ".atmpl"
    safe_org = re.sub(r"[^a-z0-9]+", "-", org.lower()).strip("-") or "organization"
    target = output_root / f"{safe_org}-{canonical}"
    target.mkdir(parents=True, exist_ok=True)

    questions = _question_map(model)
    markdown = [
        f"# Discovery questionnaire: {org}",
        "",
        f"Template: `{canonical}`",
        "",
        "Complete the paired `answers.yaml`. Unanswered or invalid items fail closed.",
        "",
    ]
    for index, (path, (prompt, type_label)) in enumerate(questions.items(), start=1):
        markdown.extend(
            [
                f"## {index}. {prompt}",
                "",
                f"- Placeholder: `{path}`",
                f"- Type: `{type_label}`",
                "",
            ]
        )

    questionnaire_path = target / "questionnaire.md"
    answers_path = target / "answers.yaml"
    questionnaire_path.write_text("\n".join(markdown), encoding="utf-8")
    answers_path.write_text(
        yaml.safe_dump(_answer_skeleton(model, org), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return questionnaire_path, answers_path


def _lookup(data: dict[str, Any], dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(dotted)
        value = value[part]
    if value is None or value == "" or value == [] or value == {}:
        raise KeyError(dotted)
    return value


def _resolve_value(value: Any, answers: dict[str, Any], missing: set[str]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_value(item, answers, missing) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, answers, missing) for item in value]
    if not isinstance(value, str):
        return value

    exact = PLACEHOLDER.fullmatch(value)
    if exact:
        try:
            return _lookup(answers, exact.group(1))
        except KeyError:
            missing.add(exact.group(1))
            return value

    def replace(match: re.Match[str]) -> str:
        dotted = match.group(1)
        try:
            return str(_lookup(answers, dotted))
        except KeyError:
            missing.add(dotted)
            return match.group(0)

    return PLACEHOLDER.sub(replace, value)


def _follow_up_error(items: list[tuple[str, str]]) -> DiscoveryError:
    lines = [f"Discovery follow-up required ({len(items)} item(s)):" ]
    for index, (path, prompt) in enumerate(items, start=1):
        lines.append(f"{index}. Ask: {prompt} [placeholder: {path}]")
    return DiscoveryError("\n".join(lines))


def compile_workflow(
    template_name: str,
    raw_answers: dict[str, Any],
) -> InstantiatedWorkflow:
    """Validate answers and resolve one base template into an instantiated workflow."""
    canonical = canonical_name(template_name)
    model = placeholder_model(canonical)
    questions = _question_map(model)

    try:
        validated = model.model_validate(raw_answers)
    except ValidationError as exc:
        follow_ups: list[tuple[str, str]] = []
        for error in exc.errors():
            path = ".".join(str(part) for part in error["loc"])
            prompt = questions.get(path, (f"Correct {path}: {error['msg']}", ""))[0]
            follow_ups.append((path, f"{prompt} Current answer is invalid: {error['msg']}"))
        raise _follow_up_error(follow_ups) from exc

    answers = validated.model_dump(mode="json")
    if canonical == "retail_refund":
        region = answers["regional"]["region"]
        pack = answers["regional"]["policy_pack"]
        if EXPECTED_RETAIL_PACKS[region] != pack:
            raise _follow_up_error(
                [
                    (
                        "regional.policy_pack",
                        (
                            f"Confirm the approved {region} policy pack; "
                            f"'{pack}' cannot cross that boundary."
                        ),
                    )
                ]
            )

    template_raw = load_template(canonical).model_dump(mode="json")
    missing: set[str] = set()
    compiled = _resolve_value(template_raw, answers, missing)
    if missing:
        raise _follow_up_error(
            [(path, questions.get(path, (f"Provide {path}.", ""))[0]) for path in sorted(missing)]
        )

    workflow = InstantiatedWorkflow.model_validate(
        {
            **compiled,
            "source_template": canonical,
            "org_profile": answers,
        }
    )
    return workflow


def resolve_discovery(
    template_name: str,
    answers_path: Path,
    output_path: Path | None = None,
) -> InstantiatedWorkflow:
    """File-based entry point used by the CLI and the seeded demos."""
    raw_answers = yaml.safe_load(answers_path.read_text(encoding="utf-8")) or {}
    workflow = compile_workflow(template_name, raw_answers)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            yaml.safe_dump(workflow.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return workflow
