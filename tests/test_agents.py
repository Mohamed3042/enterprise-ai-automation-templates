"""The discovery agent, and the boundary it is held to.

The agent's value is that it turns a paragraph into the questionnaire the compiler already
validates. Its risk is that it answers something *near* the questionnaire. Every test here
is about the second half.
"""

from __future__ import annotations

import pytest

from atmpl.agents.discovery import (
    AgentRefused,
    MapEntry,
    Rationale,
    check_rationales,
    output_model,
    pick_template,
    run_discovery,
    search_templates,
    to_answers,
)
from atmpl.intake.resolver import question_items, validate_answers
from atmpl.providers import Message, MockProvider, ProviderResult, ProviderRouter, Usage
from atmpl.settings import Settings
from atmpl.telemetry import build_observability

RETAIL_TEXT = (
    "Northwind Retail is a European e-commerce chain handling about 4,000 refund requests a "
    "month. A store manager approves refunds up to 250 EUR and the regional director "
    "approves anything above and owns escalations. Order records are confidential; the team "
    "serves customers in en-GB and ar-KW under the EU 14-day policy pack."
)


def router(provider=None) -> ProviderRouter:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    return ProviderRouter(
        settings,
        observability=build_observability(settings, install=False),
        providers=[provider or MockProvider()],
    )


# --------------------------------------------------------------------------- typed output


def test_the_output_model_is_the_template_reshaped_not_a_new_contract():
    model = output_model("retail_refund")
    answers = model.model_fields["answers"].annotation

    assert set(answers.model_fields) == {"organization", "governance", "regional"}
    governance = answers.model_fields["governance"].annotation
    matrix = governance.model_fields["approval_authority_matrix"].annotation
    assert matrix == list[MapEntry], "a free-form map gives a provider no slot to fill"


def test_map_rows_are_rebuilt_into_the_dictionary_the_template_declares():
    rebuilt = to_answers(
        {
            "governance": {
                "approval_authority_matrix": [
                    {"key": "refund", "value": "store_manager"},
                    {"key": "escalation", "value": "regional_director"},
                ]
            },
            "organization": {"locales": ["en-GB", "ar-KW"]},
        }
    )

    assert rebuilt["governance"]["approval_authority_matrix"] == {
        "refund": "store_manager",
        "escalation": "regional_director",
    }
    assert rebuilt["organization"]["locales"] == ["en-GB", "ar-KW"], "a plain list stays a list"


# --------------------------------------------------------------------------- the mock path


@pytest.mark.parametrize("template", ["retail_refund", "lesson_preparation", "loan_triage"])
def test_the_mock_agent_answers_every_template_validly(template):
    result = run_discovery(
        router(),
        description="a synthetic process description",
        template=template,
        organization="Synthetic Co",
    )

    assert result.template == template
    assert validate_answers(template, result.answers) == []
    assert result.provider == "mock"


def test_the_mock_agent_is_deterministic():
    first = run_discovery(router(), description=RETAIL_TEXT, organization="Northwind Retail")
    second = run_discovery(router(), description=RETAIL_TEXT, organization="Northwind Retail")

    assert first.answers == second.answers
    assert first.rationales == second.rationales


def test_every_answered_field_carries_a_reason():
    result = run_discovery(router(), description=RETAIL_TEXT, organization="Northwind Retail")

    reasoned = {item["placeholder"] for item in result.rationales}
    declared = {item["placeholder"] for item in question_items("retail_refund")}

    assert reasoned <= declared
    assert len(reasoned) >= len(declared) - 1, "only the organisation name may go unexplained"


def test_the_agent_uses_its_tools_rather_than_answering_blind():
    """Two provider calls: one tool call, one answer. The loop is exercised offline too."""
    built = router()
    run_discovery(built, description=RETAIL_TEXT, organization="Northwind Retail")

    records = built.observability.calls.records()

    assert len(records) == 2
    assert {record.purpose for record in records} == {"discovery_agent"}


def test_the_template_is_chosen_from_the_description_when_not_given():
    assert pick_template("we triage loan applications at a bank") == "loan_triage"
    assert pick_template("teachers prepare lessons for the curriculum") == "lesson_preparation"
    assert pick_template("customers ask for refunds in our store") == "retail_refund"
    assert search_templates("something entirely unrelated")[0]["matched_terms"] == "0"


# --------------------------------------------------------------------------- refusals


def test_a_reason_for_a_field_the_template_lacks_is_refused():
    with pytest.raises(AgentRefused) as caught:
        check_rationales(
            "retail_refund",
            [
                Rationale(
                    placeholder="governance.override_authority",
                    rationale="The description mentioned an override path.",
                )
            ],
        )

    assert caught.value.follow_ups[0]["placeholder"] == "governance.override_authority"
    assert caught.value.follow_ups[0]["problem"] == "invented_field"


class WrongShapeProvider:
    """A provider that answers with a field the template does not declare."""

    name = "mock"
    model = "wrong-shape-1"

    def configured(self) -> bool:
        return True

    def complete(self, messages: list[Message], **kwargs) -> ProviderResult:
        turn_taken = any(message.role in {"assistant", "tool"} for message in messages)
        payload = MockProvider().complete(messages, **kwargs)
        if not turn_taken:
            return payload
        draft = dict(payload.json or {})
        draft["rationales"] = [
            *draft.get("rationales", []),
            {
                "placeholder": "governance.override_authority",
                "rationale": "Invented by this test provider.",
                "confidence": "inferred",
            },
        ]
        return ProviderResult(
            provider=self.name,
            model=self.model,
            latency_ms=0.0,
            json=draft,
            usage=Usage(1, 1),
        )


def test_an_invented_field_from_the_provider_reaches_the_caller_as_a_named_field():
    with pytest.raises(AgentRefused) as caught:
        run_discovery(
            router(WrongShapeProvider()),
            description=RETAIL_TEXT,
            template="retail_refund",
            organization="Northwind Retail",
        )

    assert "governance.override_authority" in str(caught.value.follow_ups)


# --------------------------------------------------------------------------- over HTTP


def test_the_api_returns_a_draft_and_opens_a_session(client):
    response = client.post(
        "/api/v1/discovery/agent",
        json={"description": RETAIL_TEXT, "template": "retail", "organization": "Northwind"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["template"] == "retail_refund"
    assert body["provider"] == "mock"
    assert body["answers"]["regional"]["policy_pack"] == "eu_gdpr_14_day"
    assert body["rationales"]
    assert "DRAFT" in body["boundary"]

    session = client.get(f"/api/v1/discovery/sessions/{body['session_id']}")
    assert session.status_code == 200
    assert session.json()["answers"] == body["answers"]


def test_the_api_creates_nothing_when_asked_not_to(client):
    response = client.post(
        "/api/v1/discovery/agent",
        json={"description": RETAIL_TEXT, "template": "retail", "open_session": False},
    )

    assert response.status_code == 201
    assert response.json()["session_id"] is None


def test_the_api_refuses_a_description_too_short_to_answer_from(client):
    response = client.post("/api/v1/discovery/agent", json={"description": "refunds"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_a_draft_that_does_not_validate_is_a_422_naming_the_field(client, monkeypatch):
    """Fail-first: make the provider answer with an undeclared field, and the caller is
    told which field rather than receiving a questionnaire that quietly grew."""
    from atmpl.agents import discovery

    def refuse(*args, **kwargs):
        raise AgentRefused(
            "The agent answered a field this template does not have.",
            [
                {
                    "placeholder": "governance.override_authority",
                    "question": "This placeholder does not exist in the template.",
                    "problem": "invented_field",
                }
            ],
        )

    monkeypatch.setattr(discovery, "run_discovery", refuse)
    monkeypatch.setattr("atmpl.api.routers.discovery.run_discovery", refuse)

    response = client.post(
        "/api/v1/discovery/agent",
        json={"description": RETAIL_TEXT, "template": "retail"},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "discovery_incomplete"
    assert error["details"]["follow_ups"][0]["placeholder"] == "governance.override_authority"


def test_the_agent_route_requires_the_discovery_scope(locked_client, make_key):
    unscoped = make_key("agent-reader", "runs:read")

    response = locked_client.post(
        "/api/v1/discovery/agent",
        json={"description": RETAIL_TEXT},
        headers={"Authorization": f"Bearer {unscoped}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"
