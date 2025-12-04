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
    cached_agents=None,
    cached_mcp_servers=None,
    skip_header=False,
    is_request_waiting=None,
    set_waiting_fn=None,
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

    status = "success"  # Default to success, will be set to error only if an exception occurs

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
        logger.info(f"Custom run config: {custom_run_config is not None}")

        # If not using custom provider, configure default client
        if not custom_run_config:
            base_url = os.environ.get("AGENT_BASE_URL")
            api_key = os.environ.get("AGENT_API_KEY") or os.environ.get(
                "OPENAI_API_KEY"
            )

            if api_key:
                client_kwargs = {"api_key": api_key}
                if base_url:
                    client_kwargs["base_url"] = base_url

                custom_client = AsyncOpenAI(**client_kwargs)
                set_default_openai_client(custom_client, use_for_tracing=False)

                api_type = os.environ.get("AGENT_API_TYPE", "responses")
                set_default_openai_api(api_type)

                if os.environ.get("AGENT_DISABLE_TRACING", "1") == "1":
                    set_tracing_disabled(True)

        # Load project instructions
        from .utils import load_project_instructions

        project_instructions = load_project_instructions(cached_cwd)

        # Use cached MCP servers if available, otherwise load and connect
        if cached_mcp_servers is not None:
            mcp_servers = cached_mcp_servers
            hosted_tools = []
            logger.info(f"Using {len(mcp_servers)} cached MCP servers")
        else:
            mcp_servers, hosted_tools = mcp_manager.load_servers()
            if mcp_servers:
                mcp_servers = await mcp_manager.connect_servers(mcp_servers)

        # Build tools list
        tools = list(tool_wrappers.values())

        # Log registered tools
        logger.info(f"Registered tools: {list(tool_wrappers.keys())}")
        for tool_name, tool in tool_wrappers.items():
            logger.info(f"  Tool '{tool_name}': {type(tool).__name__}")

        if not tools:
            logger.warning(
                "WARNING: No tools registered! Agents will not have tool access."
            )

        # Add MCP hosted tools if available
        if hosted_tools:
            tools.extend(hosted_tools)
            logger.info(f"Added {len(hosted_tools)} MCP hosted tools")

        # Calculate max_tokens based on session usage
        max_tokens = calculate_max_tokens()
        logger.info(f"Calculated max_tokens: {max_tokens}")

        # Check for YOLO mode - auto-apply edits without stopping
        yolo_mode = os.environ.get("AGENT_YOLO", "").lower() in ("1", "true", "yes")
        if yolo_mode:
            logger.info("YOLO mode enabled - edits will be auto-applied")

        # Create the CodeAgent directly - no handoff routing needed
        # Always create agents fresh each request (don't reuse cached agents - SDK limitation)
        logger.info("Creating CodeAgent fresh per request")

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

        logger.info("CodeAgent created successfully")

        # Display agent header with model name (unless skipped for continuations)
        display_model = model if model else "gpt-4o"

        # Reset the agent response started flag for intelligent spacing
        buffer_manager.reset_agent_response_flag()

        # Add agent header with proper spacing (skip for edit continuations)
        if not skip_header:
            # Header now includes trailing blank line but not leading (spacing handled automatically)
            header_lines = [f"# Agent ({display_model})", ""]
            nvim.async_call(buffer_manager.append_content, header_lines, False, False, 'header')

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

        max_turns = 100
        turn_warning_threshold = (
            90  # Start forcing responses early to ensure we get output
        )
        context_limit_threshold = (
            0.90  # 70% context usage triggers limit (be conservative)
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

        # Configure RunConfig for chat_completions API type
        # nest_handoff_history=False is required for non-OpenAI providers using
        # chat_completions API, as they don't support the nested message format
        from agents import RunConfig

        api_type = os.environ.get("AGENT_API_TYPE", "responses")
        use_nested_history = api_type != "chat_completions"

        if custom_run_config:
            run_kwargs["run_config"] = custom_run_config
        elif not use_nested_history:
            # Only create RunConfig if we need to disable nesting for chat_completions
            run_kwargs["run_config"] = RunConfig(nest_handoff_history=False)

        # Run the agent with streaming and conversation history
        logger.info(
            f"Starting agent run with config: api_type={api_type}, use_nested={use_nested_history}"
        )
        logger.info(f"Run kwargs: {list(run_kwargs.keys())}")
        if "run_config" in run_kwargs:
            logger.info(
                f"RunConfig: nest_handoff_history={run_kwargs['run_config'].nest_handoff_history if hasattr(run_kwargs['run_config'], 'nest_handoff_history') else 'N/A'}"
            )

        result_stream = Runner.run_streamed(agent, **run_kwargs)
        logger.info(f"Runner.run_streamed called successfully, starting event loop")

        # Cache buffer number before async loop
        content_bufnr = (
            buffer_manager.content_buf.handle
            if hasattr(buffer_manager.content_buf, "handle")
            else buffer_manager.content_buf.number
        )

        event_count = 0
        max_events = 50000  # Safety limit to prevent infinite loops

        # Track thinking/reasoning content streaming
        thinking_initialized = False
        thinking_finalizing = False  # Flag to prevent new thinking content after finalization starts
        thinking_finalize_buffer = []  # Buffer for thinking content that arrives during finalization

        logger.info(f"Starting to iterate over stream events")
        logger.info(f"result_stream type: {type(result_stream)}")
        logger.info(f"result_stream.is_complete: {result_stream.is_complete}")
        async for event in result_stream.stream_events():
            event_count += 1
            if event_count <= 10 or event_count % 100 == 0:
                logger.info(f"Event #{event_count} received: {type(event).__name__}")
            # Check for stored exceptions after each event
            if (
                hasattr(result_stream, "_stored_exception")
                and result_stream._stored_exception
            ):
                logger.error(
                    f"Stored exception detected: {result_stream._stored_exception}"
                )
            if event_count > max_events:
                logger.warning(
                    f"Exceeded max events ({max_events}), breaking from event loop"
                )
                break
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

            # Debug: Log all event types (only first 20 to avoid spam)
            if event_count <= 20:
                logger.info(f"Event type: {event_type}")

            if event_type == "RawResponsesStreamEvent":
                data = event.data
                data_type = type(data).__name__

                # Debug: Log all data types in responses
                logger.info(f"Responses data type: {data_type}")
                if hasattr(data, "__dict__"):
                    logger.info(f"Data attributes: {data.__dict__.keys()}")

                # Process text output events
                if data_type == "ResponseTextDeltaEvent":
                    delta = data.delta
                    if delta:
                        # Flush thinking content BEFORE the first LLM text delta
                        # This ensures thinking appears before the response
                        # The fold will be created immediately, which is fine since LLM text hasn't started yet
                        if thinking_initialized:
                            # Set flag IMMEDIATELY to prevent new thinking content
                            thinking_finalizing = True
                            
                            # Capture delta in closure
                            first_delta = delta

                            def finalize_thinking():
                                # Stop timer, flush pending stream content, then finalize thinking section
                                content_bufnr = buffer_manager.content_buf.number if buffer_manager.content_buf and buffer_manager.content_buf.valid else -1
                                
                                def do_finalize():
                                    import time
                                    # Keep writing buffered content until buffer is completely stable
                                    # We need to ensure NO more thinking content arrives
                                    max_iterations = 20
                                    consecutive_empty = 0
                                    required_stable_iterations = 5  # Need 5 consecutive empty checks
                                    
                                    for iteration in range(max_iterations):
                                        # Write any buffered content immediately
                                        if thinking_finalize_buffer:
                                            buffered_text = "".join(thinking_finalize_buffer)
                                            # Write directly to buffer at the current end
                                            current_lines = nvim.api.buf_get_lines(buffer_manager.content_buf, 0, -1, False)
                                            end_line = len(current_lines)
                                            # Split text into lines and append
                                            text_lines = buffered_text.split("\n")
                                            if text_lines:
                                                nvim.api.buf_set_lines(buffer_manager.content_buf, end_line, end_line, False, text_lines)
                                            thinking_finalize_buffer.clear()
                                            consecutive_empty = 0  # Reset counter when we write content
                                        else:
                                            consecutive_empty += 1
                                            if consecutive_empty >= required_stable_iterations:
                                                # Buffer has been empty for required iterations
                                                break
                                        
                                        time.sleep(0.1)  # Wait before next check
                                    
                                    # One final check and write
                                    if thinking_finalize_buffer:
                                        buffered_text = "".join(thinking_finalize_buffer)
                                        current_lines = nvim.api.buf_get_lines(buffer_manager.content_buf, 0, -1, False)
                                        end_line = len(current_lines)
                                        text_lines = buffered_text.split("\n")
                                        if text_lines:
                                            nvim.api.buf_set_lines(buffer_manager.content_buf, end_line, end_line, False, text_lines)
                                        thinking_finalize_buffer.clear()
                                        time.sleep(0.2)  # Wait for final write
                                    
                                    # Keep checking and writing until buffer is truly stable
                                    # Do multiple passes to catch any late-arriving content
                                    for final_pass in range(3):
                                        time.sleep(0.15)
                                        if thinking_finalize_buffer:
                                            # More content arrived, write it
                                            buffered_text = "".join(thinking_finalize_buffer)
                                            current_lines = nvim.api.buf_get_lines(buffer_manager.content_buf, 0, -1, False)
                                            end_line = len(current_lines)
                                            text_lines = buffered_text.split("\n")
                                            if text_lines:
                                                nvim.api.buf_set_lines(buffer_manager.content_buf, end_line, end_line, False, text_lines)
                                            thinking_finalize_buffer.clear()
                                        else:
                                            # Buffer is empty, one more check to be sure
                                            time.sleep(0.1)
                                            if not thinking_finalize_buffer:
                                                # Truly empty, break
                                                break
                                    
                                    # Final safety check - write any remaining content
                                    if thinking_finalize_buffer:
                                        buffered_text = "".join(thinking_finalize_buffer)
                                        current_lines = nvim.api.buf_get_lines(buffer_manager.content_buf, 0, -1, False)
                                        end_line = len(current_lines)
                                        text_lines = buffered_text.split("\n")
                                        if text_lines:
                                            nvim.api.buf_set_lines(buffer_manager.content_buf, end_line, end_line, False, text_lines)
                                        thinking_finalize_buffer.clear()
                                        time.sleep(0.2)
                                    
                                    # Capture thinking end line AFTER all thinking content is flushed
                                    # but BEFORE any LLM content is written
                                    # This ensures the fold ends at the correct location
                                    current_lines = nvim.api.buf_get_lines(buffer_manager.content_buf, 0, -1, False)
                                    buffer_manager._thinking_end_line = len(current_lines)  # 0-indexed
                                    logger.debug(f"Captured thinking end line after flushing: {buffer_manager._thinking_end_line} (0-indexed)")
                                    
                                    # Now finalize - capture end line RIGHT before adding closing fence
                                    # Add a small delay to ensure all writes are complete
                                    time.sleep(0.1)
                                    buffer_manager.finalize_thinking_section(content_bufnr)
                                
                                # Stop timer, flush content, and poll until queue is truly empty
                                nvim.exec_lua(
                                    f"""
                                    local content_bufnr = {content_bufnr}
                                    
                                    -- Stop the streaming timer first to prevent new writes
                                    if _G.agent_stream_timer then
                                        _G.agent_stream_timer:stop()
                                        _G.agent_stream_timer = nil
                                    end
                                    
                                    -- Function to flush queue
                                    local function flush_queue()
                                        if _G.agent_stream_queue then
                                            for _, item in ipairs(_G.agent_stream_queue) do
                                                if vim.api.nvim_buf_is_valid(item.bufnr) and item.text ~= "" then
                                                    local line_count = vim.api.nvim_buf_line_count(item.bufnr)
                                                    local last_line_idx = line_count - 1
                                                    local last_line = vim.api.nvim_buf_get_lines(item.bufnr, last_line_idx, last_line_idx + 1, false)
                                                    local last_column = #(last_line[1] or "")
                                                    local lines = vim.split(item.text, "\\n", {{plain = true}})
                                                    vim.api.nvim_buf_set_text(item.bufnr, last_line_idx, last_column, last_line_idx, last_column, lines)
                                                end
                                            end
                                            _G.agent_stream_queue = {{}}
                                        end
                                    end
                                    
                                    -- Flush queue initially
                                    flush_queue()
                                    
                                    -- Add blank line after LLM text if it doesn't already end with one
                                    if vim.api.nvim_buf_is_valid(content_bufnr) then
                                        local line_count = vim.api.nvim_buf_line_count(content_bufnr)
                                        if line_count > 0 then
                                            local last_line = vim.api.nvim_buf_get_lines(content_bufnr, line_count - 1, line_count, false)
                                            if #last_line > 0 and last_line[1] and last_line[1]:gsub("%s", "") ~= "" then
                                                vim.api.nvim_buf_set_lines(content_bufnr, line_count, line_count, false, {{""}})
                                            end
                                        end
                                    end
                                    
                                    _G.agent_stream_paused = true
                                    
                                    -- Poll until queue is empty and stable
                                    local poll_count = 0
                                    local max_polls = 20  -- 20 polls * 50ms = 1 second max wait
                                    local function check_and_finalize()
                                        poll_count = poll_count + 1
                                        
                                        -- Flush any new content that arrived
                                        flush_queue()
                                        
                                        -- Check if queue is empty
                                        if not _G.agent_stream_queue or #_G.agent_stream_queue == 0 then
                                            -- Queue is empty, wait one more cycle to ensure stability
                                            if poll_count >= 3 then
                                                -- Queue has been empty for at least 3 cycles, safe to finalize
                                                _G.agent_thinking_ready_to_finalize = true
                                            else
                                                -- Check again after a short delay
                                                vim.defer_fn(check_and_finalize, 50)
                                            end
                                        else
                                            -- Queue still has content, check again
                                            if poll_count < max_polls then
                                                vim.defer_fn(check_and_finalize, 50)
                                            else
                                                -- Timeout - finalize anyway
                                                _G.agent_thinking_ready_to_finalize = true
                                            end
                                        end
                                    end
                                    
                                    -- Start polling
                                    vim.defer_fn(check_and_finalize, 100)
                                    """,
                                )
                                
                                # Schedule finalization with polling-based delay
                                import threading
                                def delayed_finalize():
                                    import time
                                    # Wait longer to match Lua polling (up to 1 second)
                                    time.sleep(1.0)
                                    # Call finalization - this has its own delays for stability checks
                                    nvim.async_call(do_finalize)
                                    # Wait longer for finalization to complete (do_finalize has up to 2+ seconds of delays)
                                    time.sleep(2.5)  # Ensure all stability checks and writes complete
                                    def resume_streaming():
                                        buffer_manager._agent_response_started = False
                                        nvim.exec_lua("_G.agent_stream_paused = false")
                                        buffer_manager.append_stream_lua_direct(first_delta, content_bufnr)
                                    nvim.async_call(resume_streaming)
                                threading.Thread(target=delayed_finalize, daemon=True).start()
                                # Don't resume streaming here - wait for finalization to complete

                            nvim.async_call(finalize_thinking)
                            thinking_initialized = False
                        else:
                            # No thinking content, append LLM text normally
                            buffer_manager.append_stream_lua_direct(delta, content_bufnr)

                # Process reasoning/thinking text events (for reasoning models like o1, glm-4, etc.)
                elif data_type in [
                    "ResponseReasoningSummaryTextDeltaEvent",
                    "ResponseReasoningTextDeltaEvent",
                ]:
                    delta = data.delta
                    if delta:
                        # If finalizing, buffer content instead of streaming it
                        if thinking_finalizing:
                            logger.debug("Buffering thinking delta - finalization in progress")
                            thinking_finalize_buffer.append(delta)
                            continue
                        
                        # Initialize thinking section on first delta
                        if not thinking_initialized:
                            content_bufnr = (
                                buffer_manager.content_buf.handle
                                if hasattr(buffer_manager.content_buf, "handle")
                                else buffer_manager.content_buf.number
                            )
                            buffer_manager.init_thinking_section(content_bufnr)
                            thinking_initialized = True
                        
                        # Stream the delta immediately
                        content_bufnr = (
                            buffer_manager.content_buf.handle
                            if hasattr(buffer_manager.content_buf, "handle")
                            else buffer_manager.content_buf.number
                        )
                        buffer_manager.stream_thinking_content(delta, content_bufnr)

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
                        set_waiting_fn=set_waiting_fn,
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
                        set_waiting_fn=set_waiting_fn,
                    )
                else:
                    # Log unknown types for debugging
                    logger.debug(f"Other responses event: {data_type}")

            # NOTE: RawChatCompletionsStreamEvent does not exist in the SDK.
            # The SDK internally converts chat completions events to Responses API
            # format via ChatCmplStreamHandler, so all events come through
            # RawResponsesStreamEvent regardless of api_type setting.

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
                            set_waiting_fn=set_waiting_fn,
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
                    set_waiting_fn=set_waiting_fn,
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
                    set_waiting_fn=set_waiting_fn,
                )
            else:
                # Log other event types for debugging
                logger.debug(f"Other event type: {event_type}")

        # Finalize any remaining thinking content
        if thinking_initialized and not thinking_finalizing:
            thinking_finalizing = True  # Set flag to prevent new thinking content
            def finalize_thinking():
                # Flush any buffered thinking content that arrived during finalization
                content_bufnr = buffer_manager.content_buf.number if buffer_manager.content_buf and buffer_manager.content_buf.valid else -1
                if thinking_finalize_buffer:
                    buffered_text = "".join(thinking_finalize_buffer)
                    # Write directly to buffer at the current end
                    current_lines = nvim.api.buf_get_lines(buffer_manager.content_buf, 0, -1, False)
                    end_line = len(current_lines)
                    # Split text into lines and append
                    text_lines = buffered_text.split("\n")
                    if text_lines:
                        nvim.api.buf_set_lines(buffer_manager.content_buf, end_line, end_line, False, text_lines)
                    thinking_finalize_buffer.clear()
                    # Small delay to ensure write completes
                    import time
                    time.sleep(0.1)
                
                buffer_manager.finalize_thinking_section(content_bufnr)
                # Stop timer, flush pending stream content, then finalize thinking section
                content_bufnr = buffer_manager.content_buf.number if buffer_manager.content_buf and buffer_manager.content_buf.valid else -1
                nvim.exec_lua(
                    """
                    local content_bufnr = ...
                    -- Stop the streaming timer first to prevent new writes
                    if _G.agent_stream_timer then
                        _G.agent_stream_timer:stop()
                        _G.agent_stream_timer = nil
                    end
                    
                    -- Flush all remaining content from the queue immediately
                    if _G.agent_stream_queue then
                        for _, item in ipairs(_G.agent_stream_queue) do
                            if vim.api.nvim_buf_is_valid(item.bufnr) and item.text ~= "" then
                                local line_count = vim.api.nvim_buf_line_count(item.bufnr)
                                local last_line_idx = line_count - 1
                                local last_line = vim.api.nvim_buf_get_lines(item.bufnr, last_line_idx, last_line_idx + 1, false)
                                local last_column = #(last_line[1] or "")
                                local lines = vim.split(item.text, "\\n", {plain = true})
                                vim.api.nvim_buf_set_text(item.bufnr, last_line_idx, last_column, last_line_idx, last_column, lines)
                            end
                        end
                        _G.agent_stream_queue = {}
                    end
                    
                    -- Add blank line after LLM text if it doesn't already end with one
                    if vim.api.nvim_buf_is_valid(content_bufnr) then
                        local line_count = vim.api.nvim_buf_line_count(content_bufnr)
                        if line_count > 0 then
                            local last_line = vim.api.nvim_buf_get_lines(content_bufnr, line_count - 1, line_count, false)
                            -- Check if last line is not empty (has any content, including just a dot)
                            if #last_line > 0 and last_line[1] and last_line[1]:gsub("%s", "") ~= "" then
                                -- Last line has content, add blank line
                                vim.api.nvim_buf_set_lines(content_bufnr, line_count, line_count, false, {""})
                            end
                        end
                    end
                    """,
                    content_bufnr,
                )
                # Now finalize thinking section (add closing fence and create fold)
                buffer_manager.finalize_thinking_section(content_bufnr)
                # Reset agent_response_started in case more LLM text follows
                buffer_manager._agent_response_started = False

            nvim.async_call(finalize_thinking)

        # Log stream completion details
        logger.info(f"Stream loop ended after {event_count} events")
        logger.info(f"result_stream.is_complete: {result_stream.is_complete}")
        if hasattr(result_stream, "final_output"):
            logger.info(f"final_output type: {type(result_stream.final_output)}")
            logger.info(
                f"final_output: {str(result_stream.final_output)[:500] if result_stream.final_output else 'None'}"
            )

        # Ensure LLM text ends with a blank line when streaming completes
        # Flush any remaining stream content and add blank line if needed
        def ensure_llm_ends_with_blank():
            try:
                # Flush any remaining stream content
                nvim.exec_lua(
                    """
                    if _G.agent_stream_queue then
                        for _, item in ipairs(_G.agent_stream_queue) do
                            if vim.api.nvim_buf_is_valid(item.bufnr) and item.text ~= "" then
                                local line_count = vim.api.nvim_buf_line_count(item.bufnr)
                                local last_line_idx = line_count - 1
                                local last_line = vim.api.nvim_buf_get_lines(item.bufnr, last_line_idx, last_line_idx + 1, false)
                                local last_column = #(last_line[1] or "")
                                local lines = vim.split(item.text, "\\n", {plain = true})
                                vim.api.nvim_buf_set_text(item.bufnr, last_line_idx, last_column, last_line_idx, last_column, lines)
                            end
                        end
                        _G.agent_stream_queue = {}
                    end
                    """
                )
                
                # Add blank line after LLM text if it doesn't already end with one
                if buffer_manager.content_buf and buffer_manager.content_buf.valid:
                    content_bufnr = buffer_manager.content_buf.number
                    nvim.exec_lua(
                        """
                        local bufnr = ...
                        if vim.api.nvim_buf_is_valid(bufnr) then
                            local line_count = vim.api.nvim_buf_line_count(bufnr)
                            if line_count > 0 then
                                local last_line = vim.api.nvim_buf_get_lines(bufnr, line_count - 1, line_count, false)
                                -- Check if last line is not empty (has any content, including just a dot)
                                if #last_line > 0 and last_line[1] and last_line[1]:gsub("%s", "") ~= "" then
                                    -- Last line has content, add blank line
                                    vim.api.nvim_buf_set_lines(bufnr, line_count, line_count, false, {""})
                                end
                            end
                        end
                        """,
                        content_bufnr,
                    )
            except Exception as e:
                logger.debug(f"Error ensuring LLM ends with blank: {e}")

        nvim.async_call(ensure_llm_ends_with_blank)

        # Get the final output and add it to conversation history
        if hasattr(result_stream, "final_output") and result_stream.final_output:
            final_output = str(result_stream.final_output)
            conversation_history.append({"role": "assistant", "content": final_output})

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

        # Mark as cancelled if user cancelled the request
        if cancel_flag_getter():
            status = "cancelled"

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

        # Check if this request is waiting for user action
        is_waiting = False
        if is_request_waiting:
            is_waiting = is_request_waiting(request_id)

        # Clear current request ID only if not waiting for user action
        # If waiting, we need to preserve the request_id for AgentWaitingDone event
        if current_request_id_ref["value"] == request_id and not is_waiting:
            current_request_id_ref["value"] = None

        # Emit fidget finish event
        emit_event_fn("AgentRequestFinished", {"id": request_id, "status": status})

        # Send desktop notification on Linux when agent turn is complete
        # Skip notification if waiting for user approval (edit or command)
        import sys
        import subprocess

        logger.info(
            f"Notification check: is_waiting={is_waiting}, request_id={request_id}, status={status}"
        )

        if sys.platform == "linux" and not is_waiting:
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
