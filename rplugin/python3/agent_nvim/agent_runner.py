"""Agent execution and streaming for agent.nvim plugin."""

import os
import sys
from . import tool_events
from .token_tracker import (
    format_placeholder_text,
    calculate_max_tokens,
    update_session_tokens,
    get_context_window,
    calculate_usage_percentage,
)
from .agents import CodeAgent


async def run_agent(
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
    emit_event_fn,
    skip_header=False,
):
    """Run the agent with streaming output.

    Args:
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
        skip_header: If True, skip the "# Agent (model)" header
    """
    model = os.environ.get("AGENT_MODEL", "gpt-5.1")

    # Emit fidget start event
    emit_event_fn(
        "AgentRequestStarted",
        {"id": request_id, "model": model, "message": "thinking"},
    )

    status = "error"  # Default to error, will be set to success if completion succeeds

    try:
        # Import from current Python environment
        try:
            from agents import (
                Runner,
                set_default_openai_client,
                set_default_openai_api,
                set_tracing_disabled,
            )
            from agents.exceptions import MaxTurnsExceeded
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
            emit_event_fn("AgentRequestFinished", {"id": request_id, "status": "error"})
            return

        # Get custom run config for OpenRouter models with '/' in name
        from .model_provider import get_custom_run_config

        custom_run_config = get_custom_run_config()

        # If not using custom provider, configure default client (for responses API)
        if not custom_run_config:
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

                custom_client = AsyncOpenAI(**client_kwargs)
                set_default_openai_client(custom_client, use_for_tracing=False)

                api_type = os.environ.get("AGENT_API_TYPE", "responses")
                set_default_openai_api(api_type)

                if os.environ.get("AGENT_DISABLE_TRACING", "1") == "1":
                    set_tracing_disabled(True)

        # Load project instructions
        from .utils import load_project_instructions

        project_instructions = load_project_instructions(cached_cwd)

        # Load MCP servers
        mcp_servers, hosted_tools = mcp_manager.load_servers()

        # Initialize and connect MCP servers if available
        if mcp_servers:
            mcp_servers = await mcp_manager.connect_servers(mcp_servers)

        # Build tools list
        tools = list(tool_wrappers.values())

        # Log registered tools
        logger.info(f"Registered tools: {list(tool_wrappers.keys())}")
        for tool_name, tool in tool_wrappers.items():
            logger.info(f"  Tool '{tool_name}': {type(tool).__name__}")

        # Add MCP hosted tools if available
        if hosted_tools:
            tools.extend(hosted_tools)
            logger.info(f"Added {len(hosted_tools)} MCP hosted tools")

        # Calculate max_tokens based on session usage
        max_tokens = calculate_max_tokens()
        logger.info(f"Calculated max_tokens: {max_tokens}")

        # Check for YOLO mode - auto-apply patches without stopping
        yolo_mode = os.environ.get("AGENT_YOLO", "").lower() in ("1", "true", "yes")
        if yolo_mode:
            logger.info("YOLO mode enabled - patches will be auto-applied")

        # Create agent using CodeAgent
        code_agent = CodeAgent(
            model=model,
            logger=logger,
            tools=tools,
            mcp_servers=mcp_servers if mcp_servers else None,
            project_instructions=project_instructions,
            yolo_mode=yolo_mode,
        )
        agent = code_agent.create_agent()

        if not agent:
            nvim.async_call(
                buffer_manager.append_content,
                ["Error: Failed to create agent. Run :AgentInstall."],
            )
            emit_event_fn("AgentRequestFinished", {"id": request_id, "status": "error"})
            return

        # Display agent header with model name (unless skipped for continuations)
        display_model = model if model else "gpt-4o"

        # Reset the agent response started flag for intelligent spacing
        buffer_manager.reset_agent_response_flag()

        # Add agent header with proper spacing (skip for patch continuations)
        if not skip_header:
            header_lines = ["", f"# Agent ({display_model})", ""]
            nvim.async_call(buffer_manager.append_content, header_lines)

        # Track the line number where agent response will start
        # Used to capture only the LLM response if cancelled
        agent_response_start_line = None
        try:
            agent_response_start_line = len(
                nvim.api.buf_get_lines(buffer_manager.content_buf, 0, -1, False)
            )
        except Exception:
            pass

        # Build input from conversation history
        input_messages = conversation_history.copy()

        # Create hooks to manage max turns and context limits
        from agents import RunHooks

        max_turns = 25
        turn_warning_threshold = (
            10  # Start forcing responses early to ensure we get output
        )
        context_limit_threshold = (
            0.70  # 70% context usage triggers limit (be conservative)
        )

        class LimitHooks(RunHooks):
            """Hooks to manage behavior as we approach turn and context limits."""

            def __init__(self):
                self.context_limit_hit = False
                self.tools_disabled = False
                self.context_window = get_context_window(model)
                self.original_instructions = None
                self.original_tools = None

            async def on_agent_start(self, ctx, agent):
                """Called before the agent is invoked."""
                # Save original state on first call
                if self.original_instructions is None:
                    self.original_instructions = agent.instructions
                    self.original_tools = list(agent.tools) if agent.tools else []

                # ctx.usage.requests counts completed requests, so +1 for current turn
                completed_turns = (
                    ctx.usage.requests if hasattr(ctx.usage, "requests") else 0
                )
                current_turn = completed_turns + 1
                remaining_turns = max_turns - completed_turns

                logger.info(
                    f"Agent turn {current_turn}/{max_turns} (remaining: {remaining_turns})"
                )

                # If we already disabled tools, keep them disabled with strong instructions
                if self.tools_disabled:
                    agent.tools = []
                    agent.instructions = self.original_instructions + (
                        "\n\n=== CRITICAL INSTRUCTION ===\n"
                        "You have reached your exploration limit. Tools are NO LONGER AVAILABLE.\n"
                        "You MUST NOW provide your complete final answer based on everything you learned.\n"
                        "Summarize your findings comprehensively. DO NOT mention needing to read more files.\n"
                        "=== END CRITICAL INSTRUCTION ==="
                    )
                    return

                # Check if we're approaching the turn limit (use current_turn, not completed)
                if current_turn >= turn_warning_threshold:
                    self.tools_disabled = True
                    agent.tools = []
                    agent.instructions = self.original_instructions + (
                        f"\n\n=== CRITICAL INSTRUCTION ===\n"
                        f"You have used {completed_turns} turns exploring. You have {remaining_turns} turns remaining.\n"
                        f"Tools are now DISABLED. You MUST provide your complete final answer NOW.\n"
                        f"Synthesize and summarize all information you have gathered.\n"
                        f"Be comprehensive but do not say you need to read more - work with what you have.\n"
                        f"=== END CRITICAL INSTRUCTION ==="
                    )
                    logger.info(
                        f"Disabled tools at turn {current_turn} to force final response"
                    )

            async def on_llm_end(self, ctx, agent, response):
                """Called after the LLM call returns - check context usage here."""
                if self.tools_disabled:
                    return  # Already handled

                usage = ctx.usage
                if usage and hasattr(usage, "total_tokens") and usage.total_tokens:
                    context_percentage, _ = calculate_usage_percentage(
                        usage.total_tokens, model
                    )
                    logger.info(f"Context usage after LLM: {context_percentage:.1f}%")

                    if context_percentage >= context_limit_threshold * 100:
                        self.context_limit_hit = True
                        self.tools_disabled = True
                        logger.info(
                            f"Context limit hit ({context_percentage:.1f}%) - will disable tools on next turn"
                        )

        hooks = LimitHooks()

        # Build run kwargs
        run_kwargs = {
            "input": input_messages,
            "max_turns": max_turns,
            "hooks": hooks,
        }

        # Use custom model provider if configured (for OpenRouter, etc.)
        if custom_run_config:
            run_kwargs["run_config"] = custom_run_config

        # Run the agent with streaming and conversation history
        result_stream = Runner.run_streamed(agent, **run_kwargs)

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
                # Capture partial output before cancelling
                if (
                    hasattr(result_stream, "final_output")
                    and result_stream.final_output
                ):
                    conversation_history.append(
                        {
                            "role": "assistant",
                            "content": f"[Cancelled]\n{str(result_stream.final_output)}",
                        }
                    )
                elif (
                    agent_response_start_line is not None
                    and buffer_manager.content_buf
                    and buffer_manager.content_buf.valid
                ):
                    # If no final output yet, capture only the LLM response from where it started
                    try:
                        all_lines = buffer_manager.nvim.api.buf_get_lines(
                            buffer_manager.content_buf, 0, -1, False
                        )
                        # Extract only the lines after the agent header (which is at agent_response_start_line)
                        response_lines = all_lines[agent_response_start_line:]
                        response_content = "\n".join(response_lines).strip()
                        if response_content:
                            conversation_history.append(
                                {
                                    "role": "assistant",
                                    "content": f"[Cancelled]\n{response_content}",
                                }
                            )
                    except Exception as e:
                        logger.debug(
                            f"Could not capture cancelled output from buffer: {e}"
                        )
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
                        event,
                        content_bufnr,
                        nvim,
                        logger,
                        buffer_manager.append_content,
                        emit_event_fn=emit_event_fn,
                        request_id=request_id,
                    )

                # Look for other potential tool-related events
                elif data_type in [
                    "ResponseToolCallEvent",
                    "ResponseToolCallDeltaEvent",
                    "ResponseToolCallOutputEvent",
                ]:
                    logger.info(f"Found tool-related event: {data_type}")
                    tool_events.handle_tool_event(
                        event,
                        content_bufnr,
                        nvim,
                        logger,
                        buffer_manager.append_content,
                        emit_event_fn=emit_event_fn,
                        request_id=request_id,
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
                        data,
                        content_bufnr,
                        nvim,
                        logger,
                        buffer_manager.append_content,
                        emit_event_fn=emit_event_fn,
                        request_id=request_id,
                    )
                elif data_type == "ChatCompletionsToolCallEndEvent":
                    logger.info(f"Tool call end: {data}")
                    tool_events.handle_tool_call_end(
                        data,
                        content_bufnr,
                        logger,
                        emit_event_fn=emit_event_fn,
                        request_id=request_id,
                    )
                elif data_type == "ChatCompletionsToolCallOutputEvent":
                    logger.info(f"Tool call output: {data}")
                    tool_events.handle_tool_call_output(
                        data,
                        content_bufnr,
                        nvim,
                        logger,
                        buffer_manager.append_content,
                        emit_event_fn=emit_event_fn,
                        request_id=request_id,
                    )
                else:
                    # Log unknown event types for debugging
                    logger.info(f"Unhandled chat completion event: {data_type}")
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
                            buffer_manager.append_content,
                            emit_event_fn=emit_event_fn,
                            request_id=request_id,
                        )

            # Handle other tool-related events
            elif "ToolCall" in event_type:
                logger.info(f"ToolCall event: {event_type}")
                tool_events.handle_tool_call_event(
                    event,
                    content_bufnr,
                    nvim,
                    logger,
                    buffer_manager.append_content,
                    emit_event_fn=emit_event_fn,
                    request_id=request_id,
                )
            elif "Tool" in event_type:
                logger.info(f"Tool event: {event_type}")
                tool_events.handle_tool_event(
                    event,
                    content_bufnr,
                    nvim,
                    logger,
                    buffer_manager.append_content,
                    emit_event_fn=emit_event_fn,
                    request_id=request_id,
                )
            else:
                # Log other event types for debugging
                logger.debug(f"Other event type: {event_type}")

            if result_stream.is_complete:
                break

        # Get the final output and add it to conversation history
        if hasattr(result_stream, "final_output") and result_stream.final_output:
            final_output = str(result_stream.final_output)
            conversation_history.append({"role": "assistant", "content": final_output})

            # If agent stopped due to patch tool (StopAtTools), show hint
            # The patch is displayed as a diff block - user presses 1 to apply, 2 to reject
            if "diff --git" in final_output or final_output.startswith("---"):
                nvim.async_call(
                    buffer_manager.append_content,
                    [
                        "",
                        "> Press **1** to apply, **2** to reject, and **za** to open the diff on top of the fold.",
                    ],
                )

        # Track and display token usage
        try:
            if (
                hasattr(result_stream, "context_wrapper")
                and result_stream.context_wrapper
            ):
                usage = result_stream.context_wrapper.usage
                if usage and hasattr(usage, "total_tokens"):
                    total_tokens = usage.total_tokens

                    # Update session token counter
                    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                    update_session_tokens(prompt_tokens, completion_tokens)
                    logger.info(
                        f"Updated session tokens: +{prompt_tokens}p +{completion_tokens}c"
                    )

                    placeholder_text, highlight_group = format_placeholder_text(
                        total_tokens=total_tokens,
                        input_tokens=prompt_tokens,
                        output_tokens=completion_tokens,
                        model=model,
                    )
                    logger.info(
                        f"Token usage: {placeholder_text} (highlight: {highlight_group})"
                    )

                    # Update placeholder text via Lua with highlight group
                    nvim.async_call(
                        lambda: nvim.exec_lua(
                            f'_G.AgentSetPlaceholder("{placeholder_text}", "{highlight_group}")',
                            None,
                        )
                    )
        except Exception as e:
            logger.debug(f"Error tracking token usage: {e}")

        # Agent completed successfully (unless cancelled)
        if not cancel_flag_getter():
            status = "success"

    except MaxTurnsExceeded as e:
        # Handle max turns gracefully - this isn't really an error
        logger.info(f"Agent reached max turns limit: {e}")
        nvim.async_call(
            buffer_manager.append_content,
            [
                "",
                "---",
                "*Reached maximum turns limit. The agent has provided the best answer it could within the turn budget.*",
                "",
            ],
        )
        # Add final output to conversation history even though we hit a limit
        if hasattr(result_stream, "final_output") and result_stream.final_output:
            conversation_history.append(
                {"role": "assistant", "content": str(result_stream.final_output)}
            )
        status = "success"  # Treat as success since we got partial output
    except Exception as e:
        import traceback

        error_str = str(e).lower()
        error_message = str(e)

        # Check for context length exceeded errors from the API
        if "context" in error_str and (
            "length" in error_str or "exceeded" in error_str or "limit" in error_str
        ):
            logger.info(f"Context length exceeded: {e}")
            nvim.async_call(
                buffer_manager.append_content,
                [
                    "",
                    "---",
                    "*Context limit reached. The conversation has grown too long. Please use `/clear` to start fresh.*",
                    "",
                ],
            )
            # Add error to conversation history for continuity
            conversation_history.append(
                {
                    "role": "assistant",
                    "content": f"[Error: Context limit reached. {error_message}]",
                }
            )
            status = "error"  # This is an error since we couldn't complete
        elif "maximum" in error_str and "token" in error_str:
            logger.info(f"Token limit exceeded: {e}")
            nvim.async_call(
                buffer_manager.append_content,
                [
                    "",
                    "---",
                    "*Token limit reached. The conversation has grown too long. Please use `/clear` to start fresh.*",
                    "",
                ],
            )
            # Add error to conversation history for continuity
            conversation_history.append(
                {
                    "role": "assistant",
                    "content": f"[Error: Token limit reached. {error_message}]",
                }
            )
            status = "error"
        else:
            logger.error(f"Agent run failed: {e}\n{traceback.format_exc()}")
            nvim.async_call(buffer_manager.append_content, [f"\nError: {str(e)}"])
            # Add error to conversation history for continuity
            conversation_history.append(
                {"role": "assistant", "content": f"[Error: {error_message}]"}
            )
            status = "error"
    finally:
        # Cleanup MCP servers if they were connected
        # if "mcp_servers" in locals() and mcp_servers:
        #     await mcp_manager.disconnect_servers(mcp_servers)

        # Clear current request ID
        if current_request_id_ref["value"] == request_id:
            current_request_id_ref["value"] = None

        # Emit fidget finish event
        emit_event_fn("AgentRequestFinished", {"id": request_id, "status": status})

        # Send desktop notification on Linux when agent turn is complete
        import sys
        import subprocess

        if sys.platform == "linux":
            try:
                title = "Agent.nvim"
                if status == "success":
                    message = "Agent finished responding"
                elif status == "cancelled":
                    message = "Agent request was cancelled"
                else:
                    message = f"Agent finished with status: {status}"
                subprocess.run(
                    ["notify-send", title, message],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass  # Silently fail if notify-send is not available
