"""Settings, and the shapes a deployment actually passes them in.

Every case here comes from something that broke a running container rather than a test.
"""

from __future__ import annotations

import pytest

from atmpl.settings import Settings


def build(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "variable",
    ["ATMPL_PROVIDER_FALLBACKS", "ATMPL_ALLOWED_ORIGINS", "ATMPL_CSP_SCRIPT_SRC"],
)
def test_an_empty_list_variable_means_empty_not_a_crash(monkeypatch, variable):
    """`ATMPL_PROVIDER_FALLBACKS: ""` is what a ConfigMap passes for "no fallbacks".

    pydantic-settings decodes a `list[str]` field as JSON *before* any `mode="before"`
    validator runs, so without `NoDecode` this raises SettingsError and the container never
    starts. The kind smoke job in CI is what found it.
    """
    settings = build(monkeypatch, **{variable: ""})

    assert getattr(settings, variable.removeprefix("ATMPL_").lower()) == []


def test_a_comma_separated_list_is_split_and_trimmed(monkeypatch):
    settings = build(
        monkeypatch,
        ATMPL_PROVIDER_FALLBACKS="gemini, anthropic ,openai",
        ATMPL_ALLOWED_ORIGINS="https://a.invalid, https://b.invalid",
    )

    assert settings.provider_fallbacks == ["gemini", "anthropic", "openai"]
    assert settings.allowed_origins == ["https://a.invalid", "https://b.invalid"]


def test_the_provider_chain_is_the_primary_then_the_fallbacks_deduplicated(monkeypatch):
    settings = build(monkeypatch, ATMPL_ADAPTER="gemini", ATMPL_PROVIDER_FALLBACKS="mock,gemini")

    assert settings.provider_chain == ["gemini", "mock"]


def test_an_unknown_trace_exporter_is_refused_by_name(monkeypatch):
    with pytest.raises(ValueError, match="Allowed: none, console, otlp"):
        build(monkeypatch, ATMPL_TRACE_EXPORTER="jaeger")


def test_every_list_setting_survives_an_empty_string(monkeypatch):
    """A new list-typed setting has to be `CsvList`, or it repeats this bug in production.

    Rather than trusting a reader to add a parametrize case, this enumerates the list-typed
    fields and passes each of them the empty string a ConfigMap would.
    """
    from typing import get_origin

    list_fields = [
        name
        for name, field in Settings.model_fields.items()
        if get_origin(field.annotation) is list
    ]
    assert list_fields, "the model has list-typed settings; this test must find them"

    for name in list_fields:
        monkeypatch.setenv(f"ATMPL_{name.upper()}", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert all(getattr(settings, name) == [] for name in list_fields)
