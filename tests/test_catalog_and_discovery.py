import json
import re
from pathlib import Path

import pytest
import yaml

from atmpl.catalog import PROJECT_ROOT, load_schema, load_template, schema_for
from atmpl.intake.resolver import DiscoveryError, init_discovery, resolve_discovery

DEMOS = {
    "retail": PROJECT_ROOT / "demos" / "retail" / "eu.answers.yaml",
    "ministry": PROJECT_ROOT / "demos" / "ministry" / "answers.yaml",
    "bank": PROJECT_ROOT / "demos" / "bank" / "answers.yaml",
}


@pytest.mark.parametrize("template", DEMOS)
def test_committed_schema_is_generated_from_pydantic(template):
    assert load_schema(template) == schema_for(template)


@pytest.mark.parametrize("template", DEMOS)
def test_every_schema_leaf_has_a_human_question(template):
    schema = schema_for(template)
    nested_models = schema["$defs"]
    leaves = [
        field
        for definition in nested_models.values()
        for field in definition.get("properties", {}).values()
        if "$ref" not in field
    ]
    assert leaves
    assert all(field.get("question") for field in leaves)


@pytest.mark.parametrize(("template", "answers"), DEMOS.items())
def test_discovery_resolves_without_unresolved_placeholders(template, answers):
    workflow = resolve_discovery(template, answers)
    serialized = json.dumps(workflow.model_dump(mode="json"), ensure_ascii=False)

    assert not re.search(r"\{\{[^}]+\}\}", serialized)
    assert len(workflow.stages) == 5
    assert any(stage.risk_tier.value == "HIGH" for stage in workflow.stages)


def test_init_emits_questionnaire_and_yaml_inside_requested_root(tmp_path: Path):
    questionnaire, answers = init_discovery("bank", "Synthetic Example Org", tmp_path)

    assert questionnaire.exists()
    assert answers.exists()
    assert "Which amount bands" in questionnaire.read_text(encoding="utf-8")
    assert yaml.safe_load(answers.read_text(encoding="utf-8"))["organization"]["name"] == (
        "Synthetic Example Org"
    )


def test_invalid_answers_become_consultant_follow_up_list(tmp_path: Path):
    path = tmp_path / "answers.yaml"
    path.write_text("organization:\n  name: X\n", encoding="utf-8")

    with pytest.raises(DiscoveryError) as captured:
        resolve_discovery("retail", path)

    message = str(captured.value)
    assert "Discovery follow-up required" in message
    assert "Ask:" in message
    assert "organization.process_owner" in message


def test_eu_answers_cannot_select_us_policy_pack(tmp_path: Path):
    source = yaml.safe_load(DEMOS["retail"].read_text(encoding="utf-8"))
    source["regional"]["policy_pack"] = "us_standard"
    path = tmp_path / "wrong-region.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")

    with pytest.raises(DiscoveryError, match="cannot cross that boundary"):
        resolve_discovery("retail", path)


def test_base_templates_are_ordered_and_complete():
    for name in DEMOS:
        template = load_template(name)
        assert template.metadata.description
        assert [stage.id for stage in template.stages] == list(
            dict.fromkeys(stage.id for stage in template.stages)
        )
        assert all(stage.escalation_rules and stage.audit_tags for stage in template.stages)

