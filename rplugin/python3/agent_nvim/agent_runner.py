"""Agent execution and streaming for agent.nvim plugin."""

import os
import sys
import uuid
import asyncio
from . import tool_events


async def run_agent(
    prompt,
    request_id,
    nvim,
    buffer_manager,
    logger,
    conversation_history,
    cancel_flag_getter,
    current_request_id_ref,
    mcp_manager,
    tool_wrappers,
    cached_cwd,
    emit_event_fn
):
    """Run the agent with streaming output.
    
    Args:
        prompt: User prompt text (not used directly - in conversation_history)
        request_id: Unique request identifier
        nvim: Neovim instance
        buffer_manager: BufferManager instance
        logger: Logger instance
        conversation_history: List of conversation messages
        cancel_flag_getter: Function that returns True if cancel was requested
        current_request_id_ref: Dict with 'value' key for tracking current request
        mcp_manager: MCPManager instance
        tool_wrappers: Dict of wrapped tool functions
        cached_cwd: Current working directory
        emit_event_fn: Function to emit user events
    """
    model = os.environ.get("AGENT_MODEL", "gpt-5.1")

    # Emit fidget start event
    emit_event_fn("AgentRequestStarted", {"id": request_id, "model": model})

    status = "error"  # Default to error, will be set to success if completion succeeds

    try:
        # Import from current Python environment
        try:
            from agents import (
                Agent,
                Runner,
                set_default_openai_client,
                set_default_openai_api,
                set_tracing_disabled,
            )
            from openai import AsyncOpenAI
        except ImportError as e:
            # Debug: show sys.path and error
            debug_msg = (
                f"ImportError: {e}\nPython: {sys.executable}\nsys.path: {sys.path}"
            )
            nvim.async_call(
                buffer_manager.append_content,
                ["Error: agents not installed. Run :AgentInstall.", debug_msg],
            )
            emit_event_fn(
                "AgentRequestFinished", {"id": request_id, "status": "error"}
            )
            return

        # Configure custom OpenAI client if base URL or API key provided
        base_url = os.environ.get("AGENT_BASE_URL")
        api_key = os.environ.get("AGENT_API_KEY") or os.environ.get(
            "OPENAI_API_KEY"
        )

        if base_url or api_key:
            client_kwargs = {}
            if base_url:
                client_kwargs["base_url"] = base_url
            if api_key:
                client_kwargs["api_key"] = api_key

            # Create custom client and set it as default
            custom_client = AsyncOpenAI(**client_kwargs)
            set_default_openai_client(custom_client, use_for_tracing=False)

            # Allow choosing API type via environment variable
            # Options: 'responses' (default) or 'chat_completions'
            api_type = os.environ.get("AGENT_API_TYPE", "responses")
            set_default_openai_api(api_type)

            # Disable tracing for custom providers by default
            if os.environ.get("AGENT_DISABLE_TRACING", "1") == "1":
                set_tracing_disabled(True)

        # Load instructions
        from .utils import load_project_instructions
        
        base_instructions = "You are a helpful AI assistant embedded in Neovim. You can read files, list files, search the repository, and propose patches."
        project_instructions = load_project_instructions(cached_cwd)
        full_instructions = base_instructions
        if project_instructions:
            full_instructions += (
                "\n\nProject Instructions:\n" + project_instructions
            )

        # Load MCP servers
        mcp_servers, hosted_tools = mcp_manager.load_servers()

        # Initialize and connect MCP servers if available
        if mcp_servers:
            mcp_servers = await mcp_manager.connect_servers(mcp_servers)
            
            if mcp_servers:
                full_instructions += (
                    f"\n\nAdditional MCP tools are available for enhanced capabilities "
                    f"(loaded {len(mcp_servers)} MCP servers: {[s.name for s in mcp_servers]})."
                )

        # Build tools list
        tools = list(tool_wrappers.values())

        # Add MCP hosted tools if available
        if hosted_tools:
            tools.extend(hosted_tools)
            logger.info(f"Added {len(hosted_tools)} MCP hosted tools")

        # Initialize Agent with optional model
        agent_kwargs = {
            "name": "Neovim Agent",
            "instructions": full_instructions,
            "tools": tools,
        }

        # Add MCP servers if available
        if mcp_servers:
            agent_kwargs["mcp_servers"] = mcp_servers

        if model:
            agent_kwargs["model"] = model

        agent = Agent(**agent_kwargs)

        # Display agent header with model name
        display_model = model if model else "gpt-4o"

        # Reset the agent response started flag for intelligent spacing
        buffer_manager.reset_agent_response_flag()

        # Add agent header with proper spacing
        header_lines = ["", f"## Agent ({display_model})", "", ""]
        nvim.async_call(buffer_manager.append_content, header_lines)

        # Build input from conversation history
        input_messages = conversation_history.copy()

        # Run the agent with streaming and conversation history
        result_stream = Runner.run_streamed(agent, input=input_messages)

        # Cache buffer number before async loop
        content_bufnr = (
            buffer_manager.content_buf.handle
            if hasattr(buffer_manager.content_buf, "handle")
            else buffer_manager.content_buf.number
        )

        async for event in result_stream.stream_events():
            # Check for cancellation
            if cancel_flag_getter():
                logger.info(f"Agent request {request_id} cancelled by user")
                status = "cancelled"
                break

            event_type = type(event).__name__

            # Debug: Log all event types
            logger.info(f"Event type: {event_type}")

            if event_type == "RawResponsesStreamEvent":
                data = event.data
                data_type = type(data).__name__

                # Debug: Log all data types in responses
                logger.info(f"Responses data type: {data_type}")
                if hasattr(data, "__dict__"):
                    logger.info(f"Data attributes: {data.__dict__.keys()}")

                # Only process actual output text, not reasoning/thinking
                if data_type == "ResponseTextDeltaEvent":
                    delta = data.delta
                    if delta:
                        buffer_manager.append_stream_lua_direct(delta, content_bufnr)

                # Check for tool-related events in responses API
                elif "Tool" in data_type or "tool" in data_type.lower():
                    logger.info(f"Tool event in responses: {data_type}")
                    tool_events.handle_tool_event(
                        event, content_bufnr, nvim, logger, buffer_manager.append_content
                    )

                # Look for other potential tool-related events
                elif data_type in [
                    "ResponseToolCallEvent",
                    "ResponseToolCallDeltaEvent",
                    "ResponseToolCallOutputEvent",
                ]:
                    logger.info(f"Found tool-related event: {data_type}")
                    tool_events.handle_tool_event(
                        event, content_bufnr, nvim, logger, buffer_manager.append_content
                    )
                else:
                    # Log unknown types for debugging
                    logger.debug(f"Other responses event: {data_type}")

            elif event_type == "RawChatCompletionsStreamEvent":
                # Handle chat completions API events
                data = event.data
                data_type = type(data).__name__

                logger.info(f"Chat completions data type: {data_type}")

                if data_type == "ChatCompletionsTextDeltaEvent":
                    delta = data.delta
                    if delta:
                        buffer_manager.append_stream_lua_direct(delta, content_bufnr)

                # Handle tool call events in chat completions
                elif data_type == "ChatCompletionsToolCallDeltaEvent":
                    logger.info(f"Tool call delta: {data}")
                    tool_events.handle_tool_call_delta(
                        data, content_bufnr, nvim, logger, buffer_manager.append_content
                    )
                elif data_type == "ChatCompletionsToolCallEndEvent":
                    logger.info(f"Tool call end: {data}")
                    tool_events.handle_tool_call_end(data, content_bufnr, logger)
                elif data_type == "ChatCompletionsToolCallOutputEvent":
                    logger.info(f"Tool call output: {data}")
                    tool_events.handle_tool_call_output(
                        data, content_bufnr, nvim, logger, buffer_manager.append_content
                    )
                else:
                    # Log unknown event types for debugging
                    logger.info(
                        f"Unhandled chat completion event: {data_type}"
                    )
                    if hasattr(data, "__dict__"):
                        logger.info(f"Data attributes: {data.__dict__.keys()}")

            # Handle RunItemStreamEvent - this is where tool calls appear during streaming
            elif event_type == "RunItemStreamEvent":
                if hasattr(event, "item"):
                    item_type = type(event.item).__name__
                    logger.info(f"RunItemStreamEvent with item: {item_type}")

                    # Process tool call items as they stream
                    if "ToolCall" in item_type or "Tool" in item_type:
                        tool_events.handle_tool_item(
                            event.item,
                            content_bufnr,
                            nvim,
                            logger,
                            buffer_manager.append_content
                        )

            # Handle other tool-related events
            elif "ToolCall" in event_type:
                logger.info(f"ToolCall event: {event_type}")
                tool_events.handle_tool_call_event(
                    event, content_bufnr, nvim, logger, buffer_manager.append_content
                )
            elif "Tool" in event_type:
                logger.info(f"Tool event: {event_type}")
                tool_events.handle_tool_event(
                    event, content_bufnr, nvim, logger, buffer_manager.append_content
                )
            else:
                # Log other event types for debugging
                logger.debug(f"Other event type: {event_type}")

            if result_stream.is_complete:
                break

        # Get the final output and add it to conversation history
        if hasattr(result_stream, "final_output") and result_stream.final_output:
            conversation_history.append(
                {"role": "assistant", "content": str(result_stream.final_output)}
            )

        # Agent completed successfully (unless cancelled)
        if not cancel_flag_getter():
            status = "success"

    except Exception as e:
        import traceback

        logger.error(f"Agent run failed: {e}\n{traceback.format_exc()}")
        nvim.async_call(buffer_manager.append_content, [f"\nError: {str(e)}"])
        status = "error"
    finally:
        # Cleanup MCP servers if they were connected
        if "mcp_servers" in locals() and mcp_servers:
            await mcp_manager.disconnect_servers(mcp_servers)

        # Clear current request ID
        if current_request_id_ref['value'] == request_id:
            current_request_id_ref['value'] = None

        # Emit fidget finish event
        emit_event_fn(
            "AgentRequestFinished", {"id": request_id, "status": status}
        )
