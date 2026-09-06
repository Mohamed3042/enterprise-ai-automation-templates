"""Agents built on PydanticAI, running on this deployment's provider router."""

from atmpl.agents.discovery import (
    TEMPLATES,
    AgentRefused,
    DiscoveryDeps,
    DiscoveryResult,
    MapEntry,
    Rationale,
    build_agent,
    output_model,
    pick_template,
    run_discovery,
    search_templates,
    to_answers,
)

__all__ = [
    "TEMPLATES",
    "AgentRefused",
    "DiscoveryDeps",
    "DiscoveryResult",
    "MapEntry",
    "Rationale",
    "build_agent",
    "output_model",
    "pick_template",
    "run_discovery",
    "search_templates",
    "to_answers",
]
