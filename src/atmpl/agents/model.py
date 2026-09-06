"""Bridge: PydanticAI drives the agent loop, the ATMPL router makes the model call.

PydanticAI is worth having for what it is good at — a typed result, a tool loop, and a
retry when the model answers in the wrong shape. It is not worth having a *second* path to
a provider: then half the model calls would miss the span, the cost estimate and the
LLMOps page. So the agent runs on a `FunctionModel` whose function is this router call.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from atmpl.providers.base import Message, ToolSpec
from atmpl.providers.mock import ORG_MARKER, TASK_MARKER, TEMPLATE_MARKER
from atmpl.providers.router import ProviderRouter


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_as_text(item) for item in content)
    return str(content)


def to_provider_messages(
    messages: list[ModelMessage],
    info: AgentInfo,
    *,
    header: list[str],
) -> list[Message]:
    """Flatten PydanticAI's message graph into the router's role/content pairs.

    Tool returns and retry prompts become user turns: every backend understands a user
    message, and the agent loop — not the wire format — is what makes them meaningful.
    """
    rendered: list[Message] = []
    if info.instructions:
        rendered.append(Message("system", info.instructions))
    rendered.append(Message("system", "\n".join(header)))
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    rendered.append(Message("system", _as_text(part.content)))
                elif isinstance(part, UserPromptPart):
                    rendered.append(Message("user", _as_text(part.content)))
                elif isinstance(part, ToolReturnPart):
                    rendered.append(
                        Message("user", f"Result of {part.tool_name}:\n{_as_text(part.content)}")
                    )
                elif isinstance(part, RetryPromptPart):
                    rendered.append(
                        Message(
                            "user",
                            "Your previous answer was rejected. Correct it and answer again.\n"
                            f"{_as_text(part.content)}",
                        )
                    )
        elif isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, TextPart):
                    rendered.append(Message("assistant", part.content))
                elif isinstance(part, ToolCallPart):
                    rendered.append(
                        Message("assistant", f"Calling {part.tool_name} with {part.args}")
                    )
    return rendered


def tool_specs(info: AgentInfo) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=tool.name,
            description=tool.description or tool.name,
            parameters=dict(tool.parameters_json_schema or {}),
        )
        for tool in info.function_tools
    ]


def router_model(
    router: ProviderRouter,
    *,
    task: str,
    template: str | None = None,
    organization: str | None = None,
) -> FunctionModel:
    """A PydanticAI model backed by :class:`ProviderRouter`."""
    header = [f"{TASK_MARKER} {task}"]
    if template:
        header.append(f"{TEMPLATE_MARKER} {template}")
    if organization:
        header.append(f"{ORG_MARKER} {organization}")

    def call(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        output_tool = info.output_tools[0] if info.output_tools else None
        result = router.complete(
            to_provider_messages(messages, info, header=header),
            tools=tool_specs(info) or None,
            schema=dict(output_tool.parameters_json_schema or {}) if output_tool else None,
        )
        parts: list[Any] = [
            ToolCallPart(tool_name=item.name, args=dict(item.arguments))
            for item in result.tool_calls
        ]
        if not parts:
            if output_tool is not None and result.json is not None:
                parts.append(ToolCallPart(tool_name=output_tool.name, args=result.json))
            else:
                parts.append(TextPart(content=result.text or ""))
        return ModelResponse(
            parts=parts,
            usage=RequestUsage(
                input_tokens=result.usage.input_tokens or 0,
                output_tokens=result.usage.output_tokens or 0,
            ),
            model_name=f"{result.provider}:{result.model}",
            provider_name=result.provider,
        )

    return FunctionModel(call, model_name=f"atmpl-router:{router.primary.name}")
