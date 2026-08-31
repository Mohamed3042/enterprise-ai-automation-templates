"""Regenerate committed placeholder schemas from their Pydantic source models."""

from __future__ import annotations

import json

from atmpl.catalog import PLACEHOLDER_MODELS, TEMPLATE_ROOT


def main() -> None:
    for name, model in PLACEHOLDER_MODELS.items():
        target = TEMPLATE_ROOT / f"{name}.placeholders.schema.json"
        target.write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"generated {target.relative_to(TEMPLATE_ROOT.parent.parent)}")


if __name__ == "__main__":
    main()

