"""Tool event handling and display for agent.nvim plugin."""

import json


def handle_tool_event(event, content_bufnr, nvim, logger, append_content_fn):
    """Handle tool-related events and display tool calls.

    Args:
        event: Tool event from the agent
        content_bufnr: Buffer number for content display
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
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
                            tool_result, nvim, logger, append_content_fn, content_bufnr
                        )
                        return

        # Fallback: look for tool indicators in event string
        if any(
            indicator in event_str.lower()
            for indicator in ["tool_call", "tool_call_id", "function_call"]
        ):
            nvim.async_call(
                lambda: append_content_fn(
                    ["\n🔧 **Tool call detected**", f"Event: {event_str[:200]}..."]
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


def display_tool_result(
    tool_result, nvim, logger, append_content_fn, content_bufnr=None
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

        # Truncate very long results
        if len(result_str) > 1000:
            result_str = result_str[:1000] + "..."

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

        if tool_info:
            tool_name = tool_info["tool_name"]
            args = tool_info["args"]
            output_lines.append(f"**  {tool_name}**")
            output_lines.append("````")
            output_lines.append("**Arguments**:")

            if args:
                for key, value in args.items():
                    if isinstance(value, str) and len(value) > 100:
                        value = value[:100] + "..."
                    output_lines.append(f"  - `{key}`: `{value}`")
            else:
                output_lines.append("  (no arguments)")

        output_lines.append("**Result**:")
        output_lines.append("```")
        output_lines.append(result_str)
        output_lines.append("```")
        output_lines.append("````")
        output_lines.append("")

        fold_summary = f"**  {tool_name}**"

        # Append content
        nvim.async_call(lambda: append_content_fn(output_lines))

    except Exception as e:
        logger.error(f"Error displaying tool result: {e}")


def handle_tool_call_delta(data, content_bufnr, nvim, logger, append_content_fn):
    """Handle tool call delta events.

    Args:
        data: Tool call delta data
        content_bufnr: Buffer number for content display
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
    """
    try:
        # Check if this is the start of a tool call (has arguments but no output yet)
        if hasattr(data, "arguments") and data.arguments:
            tool_name = getattr(data, "name", "unknown_tool")
            arguments = data.arguments

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


def handle_tool_call_end(data, content_bufnr, logger):
    """Handle tool call end events.

    Args:
        data: Tool call end data
        content_bufnr: Buffer number for content display
        logger: Logger instance
    """
    try:
        # Tool call completed, ready for result
        pass  # Result will be handled by output events
    except Exception as e:
        logger.error(f"Error handling tool call end: {e}")


def handle_tool_call_output(data, content_bufnr, nvim, logger, append_content_fn):
    """Handle tool call output events.

    Args:
        data: Tool call output data
        content_bufnr: Buffer number for content display
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
    """
    try:
        if hasattr(data, "output") and data.output:
            output = data.output

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


def handle_tool_call_event(event, content_bufnr, nvim, logger, append_content_fn):
    """Handle generic tool call events.

    Args:
        event: Tool call event
        content_bufnr: Buffer number for content display
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
    """
    try:
        event_str = str(event)
        logger.info(f"Tool call event: {event_str}")

        # Try to extract tool information from the event
        if hasattr(event, "data"):
            data = event.data
            tool_name = getattr(data, "name", None) or getattr(data, "tool_name", None)

            if tool_name:
                lines = ["", "🔧 **Tool call event**: `" + tool_name + "`"]
                nvim.async_call(lambda: append_content_fn(lines))

    except Exception as e:
        logger.error(f"Error handling tool call event: {e}")


def handle_tool_item(item, content_bufnr, nvim, logger, append_content_fn):
    """Handle tool call items from result_stream.new_items.

    Args:
        item: Tool item from stream
        content_bufnr: Buffer number for content display
        nvim: Neovim instance
        logger: Logger instance
        append_content_fn: Function to append content to buffer
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
                output_str, nvim, logger, append_content_fn, content_bufnr
            )
        else:
            # This might be a different type of item
            logger.info(f"Item {item_type} has no expected tool attributes")

    except Exception as e:
        logger.error(f"Error handling tool item: {e}")
