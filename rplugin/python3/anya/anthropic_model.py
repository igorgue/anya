"""Anthropic API model provider for OpenAI Agents SDK.

Supports Anthropic's native API format, including tool calling and streaming.
Also works with Anthropic-compatible APIs like GLM.
"""

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from .protocol import AgentSettings

logger = logging.getLogger("anya.anthropic_model")


class AnthropicModel:
    """Model implementation wrapping the Anthropic API for the OpenAI Agents SDK."""

    def __init__(self, model: str, client: Any):
        self.model = model
        self.client = client

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list,
        model_settings: Any,
        tools: list,
        output_schema: Any | None,
        handoffs: list,
        tracing: Any,
        *,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: Any | None = None,
    ) -> Any:
        from agents import ModelResponse
        from agents.usage import Usage

        messages = self._convert_input(input)
        anthropic_tools = self._convert_tools(tools, handoffs)
        params = self._build_params(
            model_settings, messages, system_instructions, anthropic_tools
        )

        response = await self.client.messages.create(**params)
        output = self._convert_output(response)
        usage = Usage(
            requests=1,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )
        return ModelResponse(output=output, usage=usage, response_id=response.id)

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list,
        model_settings: Any,
        tools: list,
        output_schema: Any | None,
        handoffs: list,
        tracing: Any,
        *,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: Any | None = None,
    ) -> AsyncIterator[Any]:
        from openai.types.responses import (
            Response,
            ResponseCompletedEvent,
            ResponseContentPartAddedEvent,
            ResponseContentPartDoneEvent,
            ResponseCreatedEvent,
            ResponseFunctionCallArgumentsDeltaEvent,
            ResponseFunctionToolCall,
            ResponseOutputItemAddedEvent,
            ResponseOutputItemDoneEvent,
            ResponseOutputMessage,
            ResponseOutputText,
            ResponseTextDeltaEvent,
        )
        from openai.types.responses.response_usage import (
            InputTokensDetails,
            OutputTokensDetails,
            ResponseUsage,
        )

        messages = self._convert_input(input)
        anthropic_tools = self._convert_tools(tools, handoffs)
        params = self._build_params(
            model_settings, messages, system_instructions, anthropic_tools
        )

        seq = [0]

        def next_seq():
            n = seq[0]
            seq[0] += 1
            return n

        response_id = "anthropic_response"
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_creation_tokens = 0

        # Track streaming state
        current_text = ""
        current_tool_calls: dict[int, dict] = {}
        # Map content block index -> output index (text=0, tools=1,2,...)
        text_output_index = 0
        tool_output_indices: dict[int, int] = {}
        next_output_index = [1]  # text is always 0

        response = Response(
            id=response_id,
            created_at=0,
            model=self.model,
            object="response",
            status="in_progress",
            output=[],
            parallel_tool_calls=True,
            tool_choice="auto",
            tools=[],
        )

        yield ResponseCreatedEvent(
            response=response,
            type="response.created",
            sequence_number=next_seq(),
        )

        # Track if we've emitted the text output item yet
        text_item_added = False

        async with self.client.messages.stream(**params) as stream:
            async for event in stream:
                etype = event.type

                if etype == "message_start":
                    response_id = event.message.id
                    usage = event.message.usage
                    input_tokens = usage.input_tokens
                    cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
                    cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0

                elif etype == "content_block_start":
                    block = event.content_block
                    idx = event.index

                    if block.type == "text":
                        if not text_item_added:
                            text_item_added = True
                            yield ResponseOutputItemAddedEvent(
                                item=ResponseOutputMessage(
                                    id=response_id,
                                    content=[],
                                    role="assistant",
                                    type="message",
                                    status="in_progress",
                                ),
                                output_index=text_output_index,
                                type="response.output_item.added",
                                sequence_number=next_seq(),
                            )
                        yield ResponseContentPartAddedEvent(
                            content_index=idx,
                            item_id=response_id,
                            output_index=text_output_index,
                            part=ResponseOutputText(
                                text="",
                                type="output_text",
                                annotations=[],
                            ),
                            type="response.content_part.added",
                            sequence_number=next_seq(),
                        )

                    elif block.type == "tool_use":
                        out_idx = next_output_index[0]
                        next_output_index[0] += 1
                        tool_output_indices[idx] = out_idx
                        current_tool_calls[idx] = {
                            "id": block.id,
                            "name": block.name,
                            "input": "",
                        }
                        yield ResponseOutputItemAddedEvent(
                            item=ResponseFunctionToolCall(
                                id=response_id,
                                call_id=block.id,
                                arguments="",
                                name=block.name,
                                type="function_call",
                            ),
                            output_index=out_idx,
                            type="response.output_item.added",
                            sequence_number=next_seq(),
                        )

                elif etype == "content_block_delta":
                    idx = event.index
                    delta = event.delta

                    if delta.type == "text_delta":
                        current_text += delta.text
                        yield ResponseTextDeltaEvent(
                            content_index=idx,
                            delta=delta.text,
                            item_id=response_id,
                            output_index=text_output_index,
                            type="response.output_text.delta",
                            sequence_number=next_seq(),
                            logprobs=[],
                        )

                    elif delta.type == "input_json_delta":
                        if idx in current_tool_calls:
                            current_tool_calls[idx]["input"] += delta.partial_json
                            yield ResponseFunctionCallArgumentsDeltaEvent(
                                delta=delta.partial_json,
                                item_id=response_id,
                                output_index=tool_output_indices[idx],
                                type="response.function_call_arguments.delta",
                                sequence_number=next_seq(),
                            )

                elif etype == "content_block_stop":
                    idx = event.index

                    if idx in current_tool_calls:
                        tc = current_tool_calls[idx]
                        out_idx = tool_output_indices[idx]
                        yield ResponseOutputItemDoneEvent(
                            item=ResponseFunctionToolCall(
                                id=response_id,
                                call_id=tc["id"],
                                arguments=tc["input"],
                                name=tc["name"],
                                type="function_call",
                            ),
                            output_index=out_idx,
                            type="response.output_item.done",
                            sequence_number=next_seq(),
                        )
                    else:
                        # Text block done
                        yield ResponseContentPartDoneEvent(
                            content_index=idx,
                            item_id=response_id,
                            output_index=text_output_index,
                            part=ResponseOutputText(
                                text=current_text,
                                type="output_text",
                                annotations=[],
                            ),
                            type="response.content_part.done",
                            sequence_number=next_seq(),
                        )

                elif etype == "message_delta":
                    if hasattr(event, "usage") and event.usage:
                        usage = event.usage
                        output_tokens = usage.output_tokens
                        # Update cache tokens if present in delta
                        cache_read_tokens = getattr(usage, "cache_read_input_tokens", cache_read_tokens) or cache_read_tokens
                        cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", cache_creation_tokens) or cache_creation_tokens

                elif etype == "message_stop":
                    pass

        # Emit text output item done if we had text
        if text_item_added:
            yield ResponseOutputItemDoneEvent(
                item=ResponseOutputMessage(
                    id=response_id,
                    content=[
                        ResponseOutputText(
                            text=current_text,
                            type="output_text",
                            annotations=[],
                        )
                    ],
                    role="assistant",
                    type="message",
                    status="completed",
                ),
                output_index=text_output_index,
                type="response.output_item.done",
                sequence_number=next_seq(),
            )

        # Build final output list
        output_items = []
        if text_item_added:
            output_items.append(
                ResponseOutputMessage(
                    id=response_id,
                    content=[
                        ResponseOutputText(
                            text=current_text,
                            type="output_text",
                            annotations=[],
                        )
                    ],
                    role="assistant",
                    type="message",
                    status="completed",
                )
            )
        for idx, tc in current_tool_calls.items():
            output_items.append(
                ResponseFunctionToolCall(
                    id=response_id,
                    call_id=tc["id"],
                    arguments=tc["input"],
                    name=tc["name"],
                    type="function_call",
                )
            )

        # Total cache tokens (read + creation)
        total_cache_tokens = cache_read_tokens + cache_creation_tokens
        
        logger.debug(
            f"Anthropic usage: input={input_tokens}, output={output_tokens}, "
            f"cache_read={cache_read_tokens}, cache_creation={cache_creation_tokens}"
        )
        
        usage = ResponseUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            input_tokens_details=InputTokensDetails(cached_tokens=total_cache_tokens),
            output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        )

        final_response = Response(
            id=response_id,
            created_at=0,
            model=self.model,
            object="response",
            status="completed",
            output=output_items,
            usage=usage,
            parallel_tool_calls=True,
            tool_choice="auto",
            tools=[],
        )

        yield ResponseCompletedEvent(
            response=final_response,
            type="response.completed",
            sequence_number=next_seq(),
        )

    # -------------------------------------------------------------------------
    # Conversion helpers
    # -------------------------------------------------------------------------

    def _build_params(
        self,
        model_settings: Any,
        messages: list,
        system_instructions: str | None,
        anthropic_tools: list,
    ) -> dict:
        import json

        logger.debug(
            "_build_params messages:\n%s", json.dumps(messages, indent=2, default=str)
        )
        params: dict = {
            "model": self.model,
            "max_tokens": (getattr(model_settings, "max_tokens", None) or 4096),
            "messages": messages,
        }
        if system_instructions:
            params["system"] = system_instructions
        if anthropic_tools:
            params["tools"] = anthropic_tools
            if model_settings and hasattr(model_settings, "tool_choice"):
                tc = self._convert_tool_choice(model_settings.tool_choice)
                if tc:
                    params["tool_choice"] = tc
        if model_settings and getattr(model_settings, "temperature", None) is not None:
            params["temperature"] = model_settings.temperature
        if model_settings and getattr(model_settings, "top_p", None) is not None:
            params["top_p"] = model_settings.top_p
        if model_settings and getattr(model_settings, "stop", None):
            stop = model_settings.stop
            params["stop_sequences"] = stop if isinstance(stop, list) else [stop]
        return params

    def _convert_input(self, input: str | list) -> list:
        """Convert SDK input items to Anthropic messages format.

        The SDK passes items as plain dicts (from model_dump) on second and later
        turns, and as typed objects on the first turn.  We handle both uniformly
        by normalising to (type, attrs) early.

        Anthropic requires strictly alternating user/assistant roles, so
        consecutive messages with the same role are merged into one.
        """
        if isinstance(input, str):
            return [{"role": "user", "content": input}]

        raw: list[dict] = []
        for item in input:
            # Normalise: dicts and typed objects are treated the same way.
            if isinstance(item, dict):
                item_type = item.get("type")
            else:
                item_type = getattr(item, "type", None)

            def _get(key: str, default=None):
                if isinstance(item, dict):
                    return item.get(key, default)
                return getattr(item, key, default)

            # Handle items with type="message" OR simple {"role": ..., "content": ...}
            # dicts (the agents SDK passes history items without a type field)
            if item_type == "message" or (
                item_type is None and _get("role") is not None
            ):
                role = _get("role", "user")
                content = _get("content", "")
                if isinstance(content, str):
                    raw.append({"role": role, "content": content})
                elif isinstance(content, list):
                    blocks = self._convert_content_blocks(content)
                    raw.append({"role": role, "content": blocks})

            elif item_type == "function_call":
                # Assistant tool call -> tool_use block in assistant message
                call_id = _get("call_id") or _get("id") or ""
                raw.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": call_id,
                                "name": _get("name", ""),
                                "input": self._parse_json(_get("arguments", "{}")),
                            }
                        ],
                    }
                )

            elif item_type == "function_call_output":
                # Tool result -> tool_result block in user message
                raw.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": _get("call_id", ""),
                                "content": str(_get("output", "")),
                            }
                        ],
                    }
                )

            # Ignore unknown item types (reasoning, mcp, etc.)

        # Merge consecutive messages with the same role (Anthropic requirement)
        messages: list[dict] = []
        for msg in raw:
            if messages and messages[-1]["role"] == msg["role"]:
                prev = messages[-1]
                if isinstance(prev["content"], str):
                    prev["content"] = [{"type": "text", "text": prev["content"]}]
                new_content = msg["content"]
                if isinstance(new_content, str):
                    new_content = [{"type": "text", "text": new_content}]
                prev["content"].extend(new_content)
            else:
                messages.append({"role": msg["role"], "content": msg["content"]})

        return messages

    def _convert_content_blocks(self, blocks: list) -> list:
        result = []
        for block in blocks:
            btype = (
                block.get("type")
                if isinstance(block, dict)
                else getattr(block, "type", None)
            )
            text = (
                block.get("text")
                if isinstance(block, dict)
                else getattr(block, "text", "")
            )

            if btype in ("text", "output_text"):
                result.append({"type": "text", "text": text or ""})
            elif btype == "tool_use":
                # Already in Anthropic format (shouldn't normally appear here)
                result.append(block if isinstance(block, dict) else block.__dict__)
            elif btype == "tool_result":
                result.append(block if isinstance(block, dict) else block.__dict__)
            # Skip unknown/unsupported block types (refusal, image, etc.)
        return result

    def _convert_tools(self, tools: list, handoffs: list) -> list:
        result = []
        for tool in tools:
            try:
                schema = (
                    tool.params_json_schema
                    if hasattr(tool, "params_json_schema")
                    else {}
                )
                result.append(
                    {
                        "name": tool.name,
                        "description": getattr(tool, "description", "") or "",
                        "input_schema": schema or {"type": "object", "properties": {}},
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to convert tool {tool}: {e}")
        for handoff in handoffs:
            try:
                result.append(
                    {
                        "name": handoff.tool_name,
                        "description": getattr(handoff, "tool_description", "") or "",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to convert handoff {handoff}: {e}")
        return result

    def _convert_tool_choice(self, tool_choice: Any) -> dict | None:
        if tool_choice is None:
            return None
        if isinstance(tool_choice, str):
            if tool_choice == "auto":
                return {"type": "auto"}
            if tool_choice == "none":
                return {"type": "auto"}  # Anthropic has no "none"; closest is auto
            if tool_choice == "required":
                return {"type": "any"}
        return {"type": "auto"}

    def _convert_output(self, response: Any) -> list:
        """Convert Anthropic response content to SDK output items."""
        from openai.types.responses import (
            ResponseOutputMessage,
            ResponseOutputText,
            ResponseFunctionToolCall,
        )

        output = []
        text_blocks = []
        tool_blocks = []

        for block in response.content:
            if block.type == "text":
                text_blocks.append(
                    ResponseOutputText(
                        text=block.text, type="output_text", annotations=[]
                    )
                )
            elif block.type == "tool_use":
                import json

                tool_blocks.append(
                    ResponseFunctionToolCall(
                        id=response.id,
                        call_id=block.id,
                        arguments=json.dumps(block.input),
                        name=block.name,
                        type="function_call",
                    )
                )

        if text_blocks:
            output.append(
                ResponseOutputMessage(
                    id=response.id,
                    content=text_blocks,
                    role="assistant",
                    type="message",
                    status="completed",
                )
            )
        output.extend(tool_blocks)
        return output

    @staticmethod
    def _parse_json(s: str) -> dict:
        import json

        try:
            return json.loads(s)
        except Exception:
            return {}


def get_anthropic_model_provider(settings: "AgentSettings | None" = None) -> Any:
    """Create a ModelProvider that uses the Anthropic API."""
    import os
    from agents import Model, ModelProvider

    if settings:
        model = settings.model or "claude-sonnet-4-20250514"
        api_key = settings.api_key
        base_url = settings.api_base
    else:
        model = os.environ.get("ANYA_MODEL", "claude-sonnet-4-20250514")
        api_key = os.environ.get("ANYA_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        base_url = os.environ.get("ANYA_API_BASE")

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.error("anthropic package not installed; run: pip install anthropic")
        raise

    client_kwargs: dict = {}
    if api_key:
        client_kwargs["api_key"] = api_key
    if base_url:
        client_kwargs["base_url"] = base_url

    client = AsyncAnthropic(**client_kwargs)
    logger.info(f"Created Anthropic client for model={model}, base_url={base_url}")

    _model = model

    class AnthropicModelProvider(ModelProvider):
        def get_model(self, model_name: str | None) -> Model:
            return AnthropicModel(model=model_name or _model, client=client)

    return AnthropicModelProvider()
