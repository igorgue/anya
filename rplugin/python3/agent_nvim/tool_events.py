"""Tool event handling and display for agent.nvim plugin."""

import json
import os


def handle_tool_event(
    event,
    content_bufnr,
    nvim,
    logger,
    append_content_fn,
    emit_event_fn=None,
    request_id=None,
    set_waiting_fn=None,
):
    """Handle tool-related events and display tool calls.

    Args:
        event: Tool event from the agent
        content_bufnr: Buffer number for content display
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
        emit_event_fn: Optional function to emit user events
        request_id: Optional request ID
        set_waiting_fn: Optional function to set waiting state
    """
    try:
        event_str = str(event)
        logger.info(f"Tool event: {event_str}")

        # Check if this is a tool call event
        if hasattr(event, "data"):
            data = event.data

            # Look for tool call information
            if hasattr(data, "__dict__"):
                data_dict = data.__dict__

                # Check for tool name
                tool_name = data_dict.get("name") or data_dict.get("tool_name")
                if tool_name:
                    display_tool_call(
                        tool_name,
                        data_dict,
                        nvim,
                        logger,
                        append_content_fn,
                        content_bufnr,
                    )
                    return

                # Check for tool call results
                if "result" in data_dict or "output" in data_dict:
                    tool_result = data_dict.get("result") or data_dict.get("output")
                    if tool_result:
                        display_tool_result(
                            tool_result,
                            nvim,
                            logger,
                            append_content_fn,
                            content_bufnr,
                            emit_event_fn=emit_event_fn,
                            request_id=request_id,
                            set_waiting_fn=set_waiting_fn,
                        )
                        return

        # Fallback: look for tool indicators in event string
        if any(
            indicator in event_str.lower()
            for indicator in ["tool_call", "tool_call_id", "function_call"]
        ):
            nvim.async_call(
                lambda: append_content_fn(
                    ["", "**Tool call detected**", f"Event: {event_str[:200]}..."]
                )
            )

    except Exception as e:
        logger.error(f"Error handling tool event: {e}")


def display_tool_call(
    tool_name, tool_data, nvim, logger, append_content_fn, content_bufnr=None
):
    """Store tool call info for later combination with result.

    Args:
        tool_name: Name of the tool being called
        tool_data: Dictionary containing tool data
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
        content_bufnr: Buffer number for creating folds
    """
    try:
        # Flush the stream queue first, then pause
        # This ensures any pending text appears BEFORE the tool output
        bufnr = content_bufnr if content_bufnr else -1

        def flush_and_pause():
            nvim.exec_lua(
                """
            local content_bufnr = ...
            -- First, flush all pending stream content immediately
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
                -- Clear the queue after flushing
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
            
            -- Now pause streaming to prevent new text from appearing before tool output
            _G.agent_stream_paused = true
            -- Reset spacing check so it will re-check when streaming resumes after tool output
            _G.agent_stream_spacing_checked = false
            """,
                bufnr,
            )

        nvim.async_call(flush_and_pause)

        # Extract tool arguments
        args = tool_data.get("arguments") or tool_data.get("args") or {}

        # Parse arguments if they're a JSON string
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                # If it's not valid JSON, treat it as a single value
                args = {"arguments": args}

        # Store pending tool call to be combined with result
        if not hasattr(display_tool_call, "_pending"):
            display_tool_call._pending = {}

        display_tool_call._pending[id(tool_data)] = {
            "tool_name": tool_name,
            "args": args,
            "content_bufnr": content_bufnr,
            "append_content_fn": append_content_fn,
            "nvim": nvim,
            "logger": logger,
        }

    except Exception as e:
        logger.error(f"Error storing tool call: {e}")


def _detect_tool_error(tool_name, result_str):
    """Detect if a tool result indicates an error.

    Args:
        tool_name: Name of the tool
        result_str: The result string from the tool

    Returns:
        True if the result indicates an error, False otherwise
    """
    # exec command declined by user
    if "Command execution was declined by user" in result_str:
        return True

    # exec command with non-zero exit code
    if "Exit code:" in result_str:
        # Extract exit code and check if non-zero
        import re

        match = re.search(r"Exit code:\s*(\d+)", result_str)
        if match and int(match.group(1)) != 0:
            return True

    # exec command errors
    if result_str.startswith("Error:") or result_str.startswith("Error executing"):
        return True

    # Timeout errors (both command timeout and tool timeout)
    if "timed out" in result_str:
        return True

    return False


def display_tool_result(
    tool_result,
    nvim,
    logger,
    append_content_fn,
    content_bufnr=None,
    emit_event_fn=None,
    request_id=None,
    set_waiting_fn=None,
):
    """Display tool call and result combined in a code block.

    Args:
        tool_result: The result from the tool call
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
        content_bufnr: Buffer number for creating folds
    """
    try:
        # Format result
        if isinstance(tool_result, str):
            result_str = tool_result
        else:
            result_str = str(tool_result)

        # Get pending tool call if it exists
        tool_info = None
        if hasattr(display_tool_call, "_pending") and display_tool_call._pending:
            # Get the most recent pending tool call
            tool_info = display_tool_call._pending.popitem()[1]
            logger.info(f"Found pending tool call: {tool_info['tool_name']}")
        else:
            logger.info("No pending tool call found")

        # Build combined output (no leading blank line)
        output_lines = []
        is_error = False  # Track if this tool result indicates an error

        if tool_info:
            tool_name = tool_info["tool_name"]

            # Special handling for edit - render as SEARCH/REPLACE blocks
            if tool_name == "edit":
                logger.info("Handling edit tool with SEARCH/REPLACE blocks")

                if hasattr(append_content_fn, "__self__"):
                    if hasattr(append_content_fn.__self__, "render_edit_blocks"):
                        buffer_manager = append_content_fn.__self__

                        # Check for YOLO mode
                        yolo_mode = os.environ.get("AGENT_YOLO", "").lower() in (
                            "1",
                            "true",
                            "yes",
                        )

                        # In YOLO mode, apply edits automatically
                        if yolo_mode:
                            logger.info("YOLO mode: auto-applying edits")

                            # Capture result_str in closure
                            edit_content = result_str

                            # Render the edit blocks first
                            nvim.async_call(
                                lambda: buffer_manager.render_edit_blocks(edit_content)
                            )

                            def apply_yolo_edits():
                                success = buffer_manager.apply_edit_blocks(edit_content)
                                if success:
                                    append_content_fn(
                                        [
                                            "> Edits auto-applied (press **2** to undo)",
                                        ],
                                        content_type="llm",
                                    )
                                    logger.info("YOLO: Edits applied successfully")
                                    try:
                                        nvim.exec_lua(
                                            """
                                            local edit_view = require('agent_nvim.edit_view')
                                            local bufnr = vim.fn.bufnr('chat')
                                            if bufnr ~= -1 then
                                                edit_view.mark_latest_as_applied(bufnr)
                                            end
                                            """
                                        )
                                    except Exception as e:
                                        logger.debug(
                                            f"Could not update edit view state: {e}"
                                        )
                                else:
                                    append_content_fn(
                                        [
                                            ">   Edits failed to apply - LLM will retry",
                                        ],
                                        content_type="llm",
                                    )
                                    logger.warning("YOLO: Edits failed to apply")

                            nvim.async_call(apply_yolo_edits)
                            # YOLO mode: resume streaming after edits applied
                            nvim.exec_lua("_G.agent_stream_paused = false")
                            if emit_event_fn and request_id:
                                emit_event_fn(
                                    "AgentToolCall",
                                    {
                                        "id": request_id,
                                        "message": "thinking",
                                    },
                                )
                        else:
                            # Non-YOLO mode: just render for user review
                            # Keep stream paused - user will confirm/reject
                            nvim.async_call(
                                lambda: buffer_manager.render_edit_blocks(result_str)
                            )
                            # Mark this request as waiting for approval
                            if set_waiting_fn and request_id:
                                logger.info(f"Setting waiting for request {request_id}")
                                set_waiting_fn(request_id, True)
                            else:
                                logger.warning(
                                    f"Cannot set waiting: set_waiting_fn={set_waiting_fn}, request_id={request_id}"
                                )
                            # Emit waiting event to update notification
                            if emit_event_fn and request_id:
                                emit_event_fn(
                                    "AgentWaiting",
                                    {
                                        "id": request_id,
                                        "message": "waiting for edit approval",
                                    },
                                )

                        return
                    else:
                        logger.warning("render_edit_blocks not found on buffer_manager")
                else:
                    logger.warning("append_content_fn does not have __self__")

            args = tool_info["args"]

            # Detect if this is an error result
            is_error = _detect_tool_error(tool_name, result_str)

            # Use different icons based on success/error
            #  for success (checkmark),  for error (x mark)
            icon = "" if is_error else ""

            # Extract first parameter for title
            first_param = None
            if args:
                first_param = next(iter(args.values()), None)

            # Build base title text (without icon - icon will be added via virtual text)
            if first_param:
                param_str = str(first_param)
                if len(param_str) > 80:
                    param_str = param_str[:77] + "..."
                tool_title = f"{tool_name} | `{param_str}`"
            else:
                tool_title = f"{tool_name}"

            output_lines.append(tool_title)
            output_lines.append("``````")
            output_lines.append("**Arguments**:")

            if args:
                for key, value in args.items():
                    if isinstance(value, str) and len(value) > 100:
                        value = value[:100] + "..."
                    output_lines.append(f"  - `{key}`: `{value}`")
            else:
                output_lines.append("  (no arguments)")

        # Detect error for cases where tool_info is None
        if not tool_info:
            is_error = _detect_tool_error("unknown", result_str)

        output_lines.append("**Result**:")
        output_lines.append("```")
        # Truncate very long results for display (but not for patch which is handled separately)
        display_result = result_str
        if len(display_result) > 1000:
            display_result = display_result[:1000] + "..."
        output_lines.append(display_result)
        output_lines.append("```")
        output_lines.append("``````")
        # Add trailing blank line - tool outputs should end with blank line
        # This ensures proper spacing when followed by LLM text
        # The spacing calculation will handle consecutive tools (no extra blank)
        output_lines.append("")

        # Append content and fold it, then resume streaming
        # Pass fold_error=True if this is an error result
        # Use content_type='tool' to let BufferManager handle spacing
        # The tool title is always the first line (index 0) in output_lines
        def append_and_resume():
            try:
                # Pass tool_title_index=0 to indicate the tool title is at index 0 in output_lines
                append_content_fn(
                    output_lines,
                    fold=True,
                    fold_error=is_error,
                    content_type="tool",
                    tool_title_index=0,
                )
            except Exception as e:
                logger.error(f"Error appending tool result to buffer: {e}")
                # Try to append a simple error message
                try:
                    simple_error = [
                        "**Tool Error**:",
                        f"``````",
                        f"Failed to display {tool_name} result: {e}",
                        "``````",
                    ]
                    append_content_fn(
                        simple_error, fold=True, fold_error=True, content_type="tool"
                    )
                except Exception as e2:
                    logger.error(f"Even simple error display failed: {e2}")

            # Reset agent_response_started so next LLM chunk recalculates spacing
            # This is safe because:
            # - If next is LLM text: will see _last_output_type='tool' and add spacing
            # - If next is tool: uses append_content with content_type='tool', not streaming
            if hasattr(append_content_fn, "__self__"):
                buffer_manager = append_content_fn.__self__
                if hasattr(buffer_manager, "_agent_response_started"):
                    buffer_manager._agent_response_started = False

            # Resume streaming after tool output is written
            try:
                nvim.exec_lua("_G.agent_stream_paused = false")
            except Exception as e:
                logger.error(f"Error resuming streaming: {e}")

            # Emit event to reset fidget back to "thinking"
            if emit_event_fn and request_id:
                try:
                    emit_event_fn(
                        "AgentToolCall",
                        {
                            "id": request_id,
                            "message": "thinking",
                        },
                    )
                except Exception as e:
                    logger.error(f"Error emitting tool call event: {e}")

        nvim.async_call(append_and_resume)

    except Exception as e:
        logger.error(f"Error displaying tool result: {e}")


def handle_tool_call_delta(
    data,
    content_bufnr,
    nvim,
    logger,
    append_content_fn,
    emit_event_fn=None,
    request_id=None,
):
    """Handle tool call delta events.

    Args:
        data: Tool call delta data
        content_bufnr: Buffer number for content display
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
        emit_event_fn: Function to emit fidget events
        request_id: Request ID for fidget updates
    """
    try:
        # Check if this is the start of a tool call (has arguments but no output yet)
        if hasattr(data, "arguments") and data.arguments:
            tool_name = getattr(data, "name", "unknown_tool")
            arguments = data.arguments

            # Emit fidget status update
            if emit_event_fn and request_id:
                emit_event_fn(
                    "AgentToolCall",
                    {"id": request_id, "message": f"{tool_name}", "tool": tool_name},
                )

            # Display the tool call
            lines = ["```", f"  {tool_name}"]

            if arguments:
                lines.append("**Arguments**:")
                # Arguments might be JSON or plain text
                try:
                    if isinstance(arguments, str):
                        parsed_args = json.loads(arguments)
                    else:
                        parsed_args = arguments

                    for key, value in parsed_args.items():
                        if isinstance(value, str) and len(value) > 100:
                            value = value[:100] + "..."
                        lines.append(f"  - `{key}`: `{value}`")
                except:
                    # Fallback to displaying raw arguments
                    if len(str(arguments)) > 200:
                        args_str = str(arguments)[:200] + "..."
                    else:
                        args_str = str(arguments)
                    lines.append(f"  - `arguments`: `{args_str}`")

            lines.append("")
            nvim.async_call(lambda: append_content_fn(lines))

    except Exception as e:
        logger.error(f"Error handling tool call delta: {e}")


def handle_tool_call_end(
    data, content_bufnr, logger, emit_event_fn=None, request_id=None
):
    """Handle tool call end events.

    Args:
        data: Tool call end data
        content_bufnr: Buffer number for content display
        logger: Logger instance
        emit_event_fn: Function to emit fidget events
        request_id: Request ID for fidget updates
    """
    try:
        # Tool call completed, ready for result
        pass  # Result will be handled by output events
    except Exception as e:
        logger.error(f"Error handling tool call end: {e}")


def handle_tool_call_output(
    data,
    content_bufnr,
    nvim,
    logger,
    append_content_fn,
    emit_event_fn=None,
    request_id=None,
):
    """Handle tool call output events.

    Args:
        data: Tool call output data
        content_bufnr: Buffer number for content display
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
        emit_event_fn: Function to emit fidget events
        request_id: Request ID for fidget updates
    """
    try:
        if hasattr(data, "output") and data.output:
            output = data.output

            # Emit fidget status update
            if emit_event_fn and request_id:
                emit_event_fn(
                    "AgentToolResult",
                    {
                        "id": request_id,
                        "message": "processing result...",
                    },
                )

            # Format the output
            if isinstance(output, str):
                output_str = output
            else:
                output_str = str(output)

            # Truncate long outputs
            if len(output_str) > 1000:
                output_str = output_str[:1000] + "..."

            lines = ["**Result**:", output_str, "```", ""]

            # Append content
            nvim.async_call(lambda: append_content_fn(lines))

    except Exception as e:
        logger.error(f"Error handling tool call output: {e}")


def handle_tool_call_event(
    event,
    content_bufnr,
    nvim,
    logger,
    append_content_fn,
    emit_event_fn=None,
    request_id=None,
):
    """Handle generic tool call events.

    Args:
        event: Tool call event
        content_bufnr: Buffer number for content display
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
        emit_event_fn: Function to emit fidget events
        request_id: Request ID for fidget updates
    """
    try:
        event_str = str(event)
        logger.info(f"Tool call event: {event_str}")

        # Try to extract tool information from the event
        if hasattr(event, "data"):
            data = event.data
            tool_name = getattr(data, "name", None) or getattr(data, "tool_name", None)

            if tool_name:
                # Emit fidget status update
                if emit_event_fn and request_id:
                    emit_event_fn(
                        "AgentToolCall",
                        {
                            "id": request_id,
                            "message": f"{tool_name}",
                            "tool": tool_name,
                        },
                    )
                lines = ["", "**Tool call event**: `" + tool_name + "`"]
                nvim.async_call(lambda: append_content_fn(lines))

    except Exception as e:
        logger.error(f"Error handling tool call event: {e}")


def handle_tool_item(
    item,
    content_bufnr,
    nvim,
    logger,
    append_content_fn,
    emit_event_fn=None,
    request_id=None,
    set_waiting_fn=None,
):
    """Handle tool call items from result_stream.new_items.

    Args:
        item: Tool item from stream
        content_bufnr: Buffer number for content display
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
        emit_event_fn: Function to emit fidget events
        request_id: Request ID for fidget updates
        set_waiting_fn: Optional function to set waiting state
    """
    try:
        item_type = type(item).__name__
        logger.info(f"Processing tool item: {item_type}")

        # Log all attributes to understand the structure
        if hasattr(item, "__dict__"):
            logger.info(f"  All attributes: {list(item.__dict__.keys())}")
            for attr_name, attr_value in item.__dict__.items():
                if attr_name in [
                    "tool_call",
                    "function_call",
                    "tool_name",
                    "name",
                    "output",
                ]:
                    logger.info(
                        f"    {attr_name}: {type(attr_value).__name__} = {str(attr_value)[:100]}..."
                    )

        if item_type == "ToolCallItem":
            # This is a tool call item - check different attribute names
            tool_name = None
            arguments = None

            # Prefer extracting from raw_item (what the SDK wraps)
            raw = getattr(item, "raw_item", None)
            try:
                if raw is not None:
                    logger.info(f"  raw_item type: {type(raw).__name__}")
                    # OpenAI Agents Python: raw.function.name, raw.function.arguments
                    func = getattr(raw, "function", None)
                    if func is None and isinstance(raw, dict):
                        func = raw.get("function")
                    if func is not None:
                        tool_name = (
                            getattr(func, "name", None)
                            if not isinstance(func, dict)
                            else func.get("name")
                        )
                        arguments = (
                            getattr(func, "arguments", None)
                            if not isinstance(func, dict)
                            else func.get("arguments")
                        )
                    # Fallbacks: raw has name/arguments directly
                    if not tool_name:
                        if isinstance(raw, dict):
                            tool_name = raw.get("name")
                            arguments = arguments or raw.get("arguments")
                        else:
                            tool_name = getattr(raw, "name", None)
                            arguments = arguments or getattr(raw, "arguments", None)
            except Exception as e:
                logger.info(f"  Failed to parse raw_item: {e}")

            # If still unknown, try other possible attribute names on the wrapper
            if not tool_name:
                for attr in ["tool_call", "function_call", "tool_name", "name"]:
                    val = getattr(item, attr, None)
                    if val is None:
                        continue
                    if attr == "name" and isinstance(val, str):
                        tool_name = val
                        break
                    if hasattr(val, "name"):
                        tool_name = getattr(val, "name")
                    if hasattr(val, "arguments"):
                        arguments = getattr(val, "arguments")
                    if isinstance(val, dict):
                        tool_name = tool_name or val.get("name")
                        arguments = arguments or val.get("arguments")
                    if tool_name:
                        break

            if tool_name:
                # Emit fidget status update
                if emit_event_fn and request_id:
                    emit_event_fn(
                        "AgentToolCall",
                        {
                            "id": request_id,
                            "message": f"{tool_name}",
                            "tool": tool_name,
                        },
                    )
                logger.info(f"Displaying tool call for: {tool_name}")
                tool_data = {"arguments": arguments} if arguments else {}
                display_tool_call(
                    tool_name, tool_data, nvim, logger, append_content_fn, content_bufnr
                )
            else:
                logger.warning("ToolCallItem found but could not extract tool name")
        elif hasattr(item, "output") or item_type == "ToolCallOutputItem":
            # This is a tool result item
            output = getattr(item, "output", None)
            if output is None:
                # Try other possible output attributes
                for attr in ["result", "content", "text"]:
                    if hasattr(item, attr):
                        output = getattr(item, attr)
                        break

            output_str = str(output) if output else "No output"
            display_tool_result(
                output_str,
                nvim,
                logger,
                append_content_fn,
                content_bufnr,
                emit_event_fn=emit_event_fn,
                request_id=request_id,
                set_waiting_fn=set_waiting_fn,
            )
        else:
            # This might be a different type of item
            logger.info(f"Item {item_type} has no expected tool attributes")

    except Exception as e:
        logger.error(f"Error handling tool item: {e}")
