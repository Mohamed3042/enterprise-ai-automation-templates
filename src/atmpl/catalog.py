"""Template catalog and generated placeholder-schema access."""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml
from pydantic import BaseModel

from atmpl.models import (
    BankPlaceholders,
    MinistryPlaceholders,
    RetailPlaceholders,
    TemplateDocument,
)


def _discover_project_root() -> Path:
    """Find the directory that holds `templates/base`.

    Editable installs put it two levels above this file; a wheel in a container does not, so
    the image sets ATMPL_PROJECT_ROOT and the search below is the fallback for both.
    """
    override = os.getenv("ATMPL_PROJECT_ROOT")
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "templates" / "base").is_dir():
            return candidate
    return here.parents[2]


PROJECT_ROOT = _discover_project_root()
TEMPLATE_ROOT = PROJECT_ROOT / "templates" / "base"

ALIASES = {
    "retail": "retail_refund",
    "retail_refund": "retail_refund",
    "ministry": "lesson_preparation",
    "lesson_preparation": "lesson_preparation",
    "bank": "loan_triage",
    "loan_triage": "loan_triage",
}

type PlaceholderModel = type[BaseModel]
PLACEHOLDER_MODELS: dict[str, PlaceholderModel] = {
    "retail_refund": RetailPlaceholders,
    "lesson_preparation": MinistryPlaceholders,
    "loan_triage": BankPlaceholders,
}


class CatalogError(ValueError):
    """Raised when a requested template does not exist."""


def canonical_name(name: str) -> str:
    try:
        return ALIASES[name]
    except KeyError as exc:
        choices = ", ".join(sorted({"retail", "ministry", "bank"}))
        raise CatalogError(f"Unknown template '{name}'. Available templates: {choices}") from exc


def placeholder_model(name: str) -> PlaceholderModel:
    return PLACEHOLDER_MODELS[canonical_name(name)]


def schema_for(name: str) -> dict:
    return placeholder_model(name).model_json_schema()


def load_schema(name: str) -> dict:
    canonical = canonical_name(name)
    path = TEMPLATE_ROOT / f"{canonical}.placeholders.schema.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return schema_for(canonical)


def load_template(name: str) -> TemplateDocument:
    canonical = canonical_name(name)
    path = TEMPLATE_ROOT / f"{canonical}.yaml"
    if not path.exists():
        raise CatalogError(f"Template artifact is missing: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return TemplateDocument.model_validate(raw)
