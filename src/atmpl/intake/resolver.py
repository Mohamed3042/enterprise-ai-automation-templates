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


def resolve_discovery(
    template_name: str,
    answers_path: Path,
    output_path: Path | None = None,
) -> InstantiatedWorkflow:
    canonical = canonical_name(template_name)
    model = placeholder_model(canonical)
    questions = _question_map(model)
    raw_answers = yaml.safe_load(answers_path.read_text(encoding="utf-8")) or {}

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
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            yaml.safe_dump(workflow.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return workflow
