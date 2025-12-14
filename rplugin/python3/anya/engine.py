"""Core agent execution engine."""

import asyncio
import json
from datetime import datetime, timezone
import os

from agents import Runner
from .agents import MAIN_AGENT_NAME, CodeAgent
from .agents.context import NvimPluginContext
from openai.types.responses import ResponseTextDeltaEvent
from . import db
from . import fidget
from . import history
from . import ids
from . import markers
from . import ui
from . import utils
from . import tools


DEFAULT_MODEL = os.environ.get("ANYA_MODEL", "gpt-4.1")


async def run_agent_streaming(plugin, text, conversation_id, chat_bufnr, request_id):
    """Run the agent with streaming and write to chat buffer."""

    # Store the request ID for use in tool execution events
    plugin._request_id = request_id

    context = NvimPluginContext(
        nvim=plugin.nvim,
        session_id=plugin.session_id,
        allowed_commands=plugin.allowed_commands,
    )

    # Emit fidget start event
    fidget.emit_user_event(
        plugin.nvim,
        "AnyaRequestStarted",
        {
            "id": request_id,
            "model": DEFAULT_MODEL,
        },
    )

    buffer_content = await ui.get_buffer_content_async(plugin.nvim, chat_bufnr)
    records = history.parse_buffer_content(buffer_content or "")
    llm_history = history.build_llm_history(records)

    # Prepend open buffer context to the last user message
    if llm_history and llm_history[-1]["role"] == "user":
        buffer_context = await ui.get_open_buffers_context_async(plugin.nvim)
        if buffer_context:
            llm_history[-1]["content"] = buffer_context + llm_history[-1]["content"]

    msg_id = ids.new(conversation=conversation_id)
    now = datetime.now(timezone.utc)
    timestamp = (
        now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
    )
    agent_name = MAIN_AGENT_NAME

    header = markers.make_message_marker(msg_id) + "\n"

    plugin.nvim.async_call(ui.append_to_chat_buffer, plugin.nvim, chat_bufnr, header)

    # Ensure DB has a placeholder message row for metadata-based rendering
    if conversation_id:
        try:
            plugin._ensure_db()
            inserted = db.save_message_dict(
                msg_id=msg_id,
                conversation_id=conversation_id,
                role="assistant",
                content="",
                author=agent_name,
                model=DEFAULT_MODEL,
                created_at=timestamp,
                ended_at=None,
                markers=None,
            )
            if not inserted:
                db.update_message(msg_id, content="", ended_at=None, markers=None)
        except Exception:
            pass

    # Initialize tool fold state at start of request
    plugin.nvim.async_call(plugin._set_tool_fold_open, False)

    # Thinking/reasoning state tracking
    thinking_started = False  # Track if we've started outputting thinking content
    thinking_finalized = False  # Track if we've finalized thinking section

    # Collect streamed content for saving
    collected_content: list[str] = []
    thinking_content: list[str] = []  # Collect thinking blocks separately
    parallel_tools: list[dict] = []  # Collect parallel tool calls
    parallel_skip_tools: list[dict] = []  # Track tools that skip output
    pending_tool_outputs: list[str] = []  # Collect outputs for parallel tools
    expected_outputs = 0  # Number of outputs we're waiting for
    tool_was_called = False  # Track if any tool was called (for unclosed folds)
    in_anya_marker = False  # Track if LLM is outputting an anya marker
    needs_blank_before_text = False  # Add blank line before next text after tool
    last_output_was_marker = True  # Track if last output was a marker (header counts)
    last_output_was_tool = False  # Track if last output was a tool header/output

    try:
        # Get the pre-initialized agent
        agent_for_run = await plugin._get_or_initialize_agent()

        result = Runner.run_streamed(
            starting_agent=agent_for_run,
            input=llm_history,
            context=context,
            max_turns=1000,
        )

        async for event in result.stream_events():
            # Check if cancellation was requested
            if plugin._request_cancelled:
                raise asyncio.CancelledError()

            # Detect reasoning/thinking content from streaming events
            is_reasoning_event = False
            reasoning_text: str | None = None
            if event.type == "raw_response_event" and hasattr(event, "data"):
                data = event.data
                data_type = getattr(data, "type", "")

                # Keep the thinking fold open for *all* reasoning-related events.
                if isinstance(data_type, str) and data_type.startswith(
                    "response.reasoning"
                ):
                    is_reasoning_event = True

                    # Text-bearing reasoning events
                    if data_type in (
                        "response.reasoning_summary_text.delta",
                        "response.reasoning_text.delta",
                        "response.reasoning_content.delta",
                    ):
                        reasoning_text = getattr(data, "delta", "")
                    elif data_type in (
                        "response.reasoning_summary_text.done",
                        "response.reasoning_text.done",
                        "response.reasoning_content.done",
                    ):
                        reasoning_text = getattr(data, "text", "")

                # Fallback: check if delta has reasoning_content (e.g. from some providers)
                if not reasoning_text and hasattr(data, "delta"):
                    delta = data.delta
                    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        is_reasoning_event = True
                        reasoning_text = delta.reasoning_content
                    elif hasattr(delta, "reasoning") and delta.reasoning:
                        is_reasoning_event = True
                        reasoning_text = delta.reasoning

            # Handle reasoning/thinking events
            if is_reasoning_event:
                # First reasoning event - output header with fold
                if not thinking_started:
                    thinking_started = True

                    thinking_header = "**thinking**\n"
                    thinking_header += markers.make_marker("fold_start", "thinking")
                    thinking_header += "\n"

                    collected_content.append(thinking_header)
                    thinking_content.append(thinking_header)
                    if not plugin._request_cancelled:
                        plugin.nvim.async_call(
                            ui.stream_text_to_buffer,
                            plugin.nvim,
                            chat_bufnr,
                            thinking_header,
                        )
                    plugin._streaming_started = True
                    last_output_was_marker = True
                    last_output_was_tool = False

                if reasoning_text:
                    thinking_content.append(reasoning_text)
                    collected_content.append(reasoning_text)
                    if not plugin._request_cancelled:
                        plugin.nvim.async_call(
                            ui.stream_text_to_buffer,
                            plugin.nvim,
                            chat_bufnr,
                            reasoning_text,
                        )

                continue  # Skip other processing for reasoning events

            # Handle finalization transition (first non-reasoning event after reasoning started)
            if thinking_started and not thinking_finalized:
                # Finalize thinking section
                thinking_finalized = True

                # Close thinking fold with blank line after
                thinking_footer = "\n" + markers.make_marker("fold_end") + "\n\n"
                collected_content.append(thinking_footer)
                thinking_content.append(thinking_footer)
                if not plugin._request_cancelled:
                    plugin.nvim.async_call(
                        ui.stream_text_to_buffer,
                        plugin.nvim,
                        chat_bufnr,
                        thinking_footer,
                    )

                last_output_was_marker = True
                needs_blank_before_text = False

            # Handle higher-level run item events for tool calls and outputs
            if event.type == "run_item_stream_event":
                item = event.item
                item_type = getattr(item, "type", None)

                # Newer Agents SDK versions surface reasoning as a RunItem.
                # Render it as a "thinking" fold if we haven't already streamed reasoning text.
                if item_type == "reasoning_item" and not thinking_started:
                    raw_item = getattr(item, "raw_item", None)

                    summary_parts = (
                        getattr(raw_item, "summary", None) if raw_item else None
                    )
                    content_parts = (
                        getattr(raw_item, "content", None) if raw_item else None
                    )

                    summary_text = "\n".join(
                        getattr(p, "text", "")
                        for p in (summary_parts or [])
                        if getattr(p, "text", "")
                    )
                    content_text = "\n".join(
                        getattr(p, "text", "")
                        for p in (content_parts or [])
                        if getattr(p, "text", "")
                    )

                    display_text = summary_text or content_text

                    if display_text:
                        thinking_started = True
                        thinking_finalized = True

                        thinking_header = "**thinking**\n"
                        thinking_header += markers.make_marker("fold_start", "thinking")
                        thinking_header += "\n"

                        collected_content.append(thinking_header)
                        thinking_content.append(thinking_header)
                        if not plugin._request_cancelled:
                            plugin.nvim.async_call(
                                ui.stream_text_to_buffer,
                                plugin.nvim,
                                chat_bufnr,
                                thinking_header,
                            )

                        thinking_content.append(display_text)
                        collected_content.append(display_text)
                        if not plugin._request_cancelled:
                            plugin.nvim.async_call(
                                ui.stream_text_to_buffer,
                                plugin.nvim,
                                chat_bufnr,
                                display_text,
                            )

                        thinking_footer = "\n" + markers.make_marker("fold_end") + "\n\n"
                        collected_content.append(thinking_footer)
                        thinking_content.append(thinking_footer)
                        if not plugin._request_cancelled:
                            plugin.nvim.async_call(
                                ui.stream_text_to_buffer,
                                plugin.nvim,
                                chat_bufnr,
                                thinking_footer,
                            )

                        plugin._streaming_started = True
                        last_output_was_marker = True
                        last_output_was_tool = False
                        needs_blank_before_text = False

                    continue

                if item_type == "tool_call_item":
                    raw_item = getattr(item, "raw_item", None)
                    tool_name = (
                        getattr(item, "name", None) or getattr(raw_item, "name", "")
                        if raw_item
                        else ""
                    )
                    tool_args = (
                        getattr(item, "arguments", "")
                        or getattr(raw_item, "arguments", "")
                        if raw_item
                        else ""
                    )
                    if tool_name:
                        tool_was_called = True
                        plugin._streaming_started = True

                        # Check if tool should skip output
                        tool_func = getattr(tools, tool_name, None)
                        skip_output = (
                            getattr(tool_func, "skip_output", False)
                            if tool_func
                            else False
                        )

                        # Emit tool execution event for fidget (still emit even if skip_output for status tracking)
                        fidget.emit_user_event(
                            plugin.nvim,
                            "AnyaToolExecution",
                            {
                                "request_id": plugin._request_id,
                                "tool_name": tool_name,
                            },
                        )

                        # If tool should skip output, don't add to parallel_tools or display header
                        if skip_output:
                            expected_outputs += 1
                            # Add to a separate list for tracking skip_output tools
                            parallel_skip_tools.append(
                                {
                                    "name": tool_name,
                                    "args": tool_args,
                                }
                            )
                        else:
                            # Use edit_pending for edit tool, tool_pending for others
                            status = (
                                "edit_pending"
                                if tool_name == "edit"
                                else "tool_pending"
                            )
                            parallel_tools.append(
                                {
                                    "name": tool_name,
                                    "args": tool_args,
                                    "status": status,
                                }
                            )
                            expected_outputs += 1

                            # Skip header output for edit tool - edit_view handles its own display
                            if tool_name == "edit":
                                last_output_was_tool = (
                                    True  # Track that a tool was just started
                                )
                                pass  # edit_view will render its own header
                            else:
                                # Build combined header with all tools so far
                                tool_headers = [
                                    utils.format_tool_header(t["name"], t["args"])
                                    for t in parallel_tools
                                ]
                                combined_header = " | ".join(tool_headers)

                                if len(parallel_tools) == 1:
                                    # First tool - output header with pending marker
                                    # Ensure blank line before tool if preceded by text (but not another tool)
                                    if not last_output_was_tool:
                                        if not plugin._request_cancelled:
                                            plugin.nvim.async_call(
                                                ui.ensure_blank_line_before_tool,
                                                plugin.nvim,
                                                chat_bufnr,
                                            )
                                        collected_content.append("\n")
                                    # Use the status from the tool (edit_pending or tool_pending)
                                    pending_header = (
                                        combined_header
                                        + "\n"
                                        + markers.make_marker("fold_start", status)
                                        + "\n"
                                    )
                                    collected_content.append(pending_header)
                                    if not plugin._request_cancelled:
                                        plugin.nvim.async_call(
                                            ui.stream_text_to_buffer,
                                            plugin.nvim,
                                            chat_bufnr,
                                            pending_header,
                                        )
                                        # Mark that a tool fold is now open
                                        plugin.nvim.async_call(
                                            plugin._set_tool_fold_open, True
                                        )
                                    last_output_was_marker = True
                                    last_output_was_tool = True
                                else:
                                    # Additional parallel tool - update header line
                                    if not plugin._request_cancelled:
                                        plugin.nvim.async_call(
                                            ui.update_tool_header_line,
                                            plugin.nvim,
                                            chat_bufnr,
                                            combined_header,
                                        )

                elif item_type == "tool_call_output_item":
                    tool_output = getattr(item, "output", "")
                    pending_tool_outputs.append(tool_output)

                    if (
                        len(pending_tool_outputs) >= expected_outputs
                        and expected_outputs > 0
                    ):
                        # Check if this is an edit tool (don't auto-update markers)
                        is_edit_tool = any(t["name"] == "edit" for t in parallel_tools)

                        # Check if any output indicates failure
                        has_failure = any(
                            "error" in o.lower()
                            for o in pending_tool_outputs
                            if isinstance(o, str)
                        )

                        # Update pending markers to success or failure
                        # Skip marker update for edit tool - user will approve/reject
                        # Skip marker update for skip_output tools - no UI was shown
                        if not plugin._request_cancelled and not is_edit_tool:
                            if has_failure:
                                plugin.nvim.async_call(
                                    ui.update_pending_markers_to_failure,
                                    plugin.nvim,
                                    chat_bufnr,
                                )
                            else:
                                plugin.nvim.async_call(
                                    ui.update_pending_markers_to_success,
                                    plugin.nvim,
                                    chat_bufnr,
                                )

                        all_outputs = "\n".join(o for o in pending_tool_outputs if o)

                        # For edit tool, skip rendering - edit tool handles its own UI
                        # The tool output is the result message (EDIT_APPLIED, etc)
                        # For skip_output tools, don't render anything
                        if is_edit_tool or parallel_skip_tools:
                            # Don't render anything - edit tool already rendered via UI
                            # or skip_output tools don't want any output
                            pass
                        elif all_outputs:
                            # Wrap MCP server output with backticks
                            wrapped_output = f"``````\n{all_outputs}\n``````"
                            collected_content.append(wrapped_output)
                            if not plugin._request_cancelled:
                                plugin.nvim.async_call(
                                    ui.stream_text_to_buffer_sync,
                                    plugin.nvim,
                                    chat_bufnr,
                                    wrapped_output,
                                )

                        # Skip fold markers for edit tool - edit_view handles its own display
                        # Skip fold markers for skip_output tools - no UI was shown
                        if not is_edit_tool and not parallel_skip_tools:
                            fold_end_marker = (
                                "\n" + markers.make_marker("fold_end") + "\n"
                            )
                            collected_content.append(fold_end_marker)
                            if not plugin._request_cancelled:
                                plugin.nvim.async_call(
                                    ui.stream_text_to_buffer,
                                    plugin.nvim,
                                    chat_bufnr,
                                    fold_end_marker,
                                )
                                # Mark that the tool fold is now closed
                                plugin.nvim.async_call(
                                    plugin._set_tool_fold_open, False
                                )

                        # Emit tool execution complete event for fidget
                        # Emit for all tools including skip_output (for status tracking)
                        all_tools = parallel_tools + parallel_skip_tools
                        for tool in all_tools:
                            fidget.emit_user_event(
                                plugin.nvim,
                                "AnyaToolExecutionComplete",
                                {
                                    "request_id": plugin._request_id,
                                    "tool_name": tool["name"],
                                },
                            )

                        pending_tool_outputs = []
                        expected_outputs = 0
                        tool_was_called = False
                        parallel_tools = []
                        parallel_skip_tools = []
                        needs_blank_before_text = True
                        last_output_was_marker = True

            if hasattr(event, "data") and isinstance(
                event.data, ResponseTextDeltaEvent
            ):
                delta = event.data.delta
                if delta:
                    # Filter out anya markers from LLM output
                    # Defense in depth: if markers leak into history, filter them here
                    delta, in_anya_marker = utils.filter_anya_markers(
                        delta, in_anya_marker
                    )
                    if not delta:
                        continue

                    # Mark that streaming has started
                    plugin._streaming_started = True

                    # Add blank line before text if we just finished a tool call
                    if needs_blank_before_text:
                        collected_content.append("\n")
                        if not plugin._request_cancelled:
                            plugin.nvim.async_call(
                                ui.stream_text_to_buffer, plugin.nvim, chat_bufnr, "\n"
                            )
                        needs_blank_before_text = False

                    # LLM text output - this is the agent's response
                    collected_content.append(delta)
                    last_output_was_marker = False
                    last_output_was_tool = False

                    # Don't queue text if cancellation is in progress
                    if not plugin._request_cancelled:
                        plugin.nvim.async_call(
                            ui.stream_text_to_buffer, plugin.nvim, chat_bufnr, delta
                        )

        # Ensure thinking block is closed if stream ends
        if thinking_started and not thinking_finalized:
            thinking_finalized = True
            thinking_footer = "\n" + markers.make_marker("fold_end") + "\n"
            collected_content.append(thinking_footer)
            if not plugin._request_cancelled:
                plugin.nvim.async_call(
                    ui.stream_text_to_buffer,
                    plugin.nvim,
                    chat_bufnr,
                    thinking_footer,
                )

        # Flush any remaining parallel tools before message end
        # (Headers already displayed with pending status, just clear the list)
        parallel_tools = []

        now = datetime.now(timezone.utc)
        end_timestamp = (
            now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
        )
        # Close any open tool folds before message end marker
        if tool_was_called:
            fold_end_marker = "\n" + markers.make_marker("fold_end")
            collected_content.append(fold_end_marker)
            plugin.nvim.async_call(
                ui.stream_text_to_buffer, plugin.nvim, chat_bufnr, fold_end_marker
            )
            # Mark that the tool fold is now closed
            plugin.nvim.async_call(plugin._set_tool_fold_open, False)

        message_text = "".join(collected_content)

        # Flush streaming queue and save agent message to database
        # We do this inline after the footer is sent (but before returning)
        # to ensure the buffer has all content and markers are finalized
        def save_after_streaming():
            save_agent_message_to_db(
                plugin,
                chat_bufnr,
                msg_id,
                agent_name,
                conversation_id,
                timestamp,
                end_timestamp,
                message_text,
            )

        plugin.nvim.async_call(save_after_streaming)

        # Emit fidget finish event
        fidget.emit_user_event(
            plugin.nvim,
            "AnyaRequestFinished",
            {
                "id": request_id,
                "status": "success",
            },
        )

    except asyncio.CancelledError:
        # Handle cancellation
        now = datetime.now(timezone.utc)
        end_timestamp = (
            now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
        )

        # Close any open code blocks in the collected content
        original_content = "".join(collected_content)
        fixed_content = utils.close_open_code_blocks(original_content)

        # If closing fences were added, append them to the buffer
        if len(fixed_content) > len(original_content):
            # Extract only what was added (the closing fence)
            original_lines = original_content.split("\n")
            fixed_lines = fixed_content.split("\n")
            if len(fixed_lines) > len(original_lines):
                added_lines = fixed_lines[len(original_lines) :]
                added_content = "\n".join(added_lines)
                plugin.nvim.async_call(
                    ui.append_to_chat_buffer,
                    plugin.nvim,
                    chat_bufnr,
                    added_content + "\n",
                )

        # Add message end marker
        if tool_was_called:
            fold_end_marker = "\n" + markers.make_marker("fold_end")
            collected_content.append(fold_end_marker)
            plugin.nvim.async_call(
                ui.append_to_chat_buffer, plugin.nvim, chat_bufnr, fold_end_marker
            )
            # Mark that the tool fold is now closed
            plugin.nvim.async_call(plugin._set_tool_fold_open, False)

        message_text = "".join(collected_content)

        # Flush streaming queue and save agent message to database
        # We do this inline after the footer is sent (but before returning)
        # to ensure the buffer has all content and markers are finalized
        def save_after_streaming():
            save_agent_message_to_db(
                plugin,
                chat_bufnr,
                msg_id,
                agent_name,
                conversation_id,
                timestamp,
                end_timestamp,
                message_text,
            )

        plugin.nvim.async_call(save_after_streaming)

        # Emit fidget finish event
        fidget.emit_user_event(
            plugin.nvim,
            "AnyaRequestFinished",
            {
                "id": request_id,
                "status": "cancelled",
            },
        )

    except Exception as e:
        plugin.nvim.async_call(
            ui.append_to_chat_buffer, plugin.nvim, chat_bufnr, f"\n\n**Error:** {e}\n"
        )
        plugin.nvim.async_call(plugin.nvim.err_write, f"Agent error: {e}\n")

        # Emit fidget error event
        fidget.emit_user_event(
            plugin.nvim,
            "AnyaRequestFinished",
            {
                "id": request_id,
                "status": "error",
            },
        )
    finally:
        # Always clear the current task reference when done
        plugin._current_task = None

        # Note: We need to set this on plugin instance
        # plugin._request_cancelled is cleared in plugin.send(), but also cleared here in finally in old code
        if hasattr(plugin, "_request_cancelled"):
            plugin._request_cancelled = False

        # Ensure tool fold state is reset
        plugin.nvim.async_call(plugin._set_tool_fold_open, False)


def save_agent_message_to_db(
    plugin,
    chat_bufnr,
    msg_id,
    agent_name,
    conversation_id,
    timestamp,
    end_timestamp,
    message_text,
):
    """Save agent message to database reading final buffer content with correct markers.

    This runs on the main thread via async_call, ensuring all Neovim API calls
    are properly synchronized. Ensures pending markers have been updated to
    success/failure before extracting and saving the message.
    """
    # Flush the streaming queue to ensure all buffer updates are written
    plugin.nvim.exec_lua("require('anya.text').flush_queue()")

    # Initialize database if needed
    plugin._ensure_db()

    if not conversation_id:
        plugin.nvim.err_write(
            f"Warning: Missing conversation_id for message {msg_id}\n"
        )
        return

    # Read the message content slice from the buffer so tool-rendered output (e.g., edit UI)
    # is included, without duplicating earlier messages.
    message_text_from_buffer = None
    if plugin.nvim.api.buf_is_valid(chat_bufnr):
        lines = plugin.nvim.api.buf_get_lines(chat_bufnr, 0, -1, False)
        message_markers: list[tuple[int, str]] = []

        def parse_message_id(line: str) -> str | None:
            prefix = markers.MESSAGE_PREFIX
            suffix = markers.MESSAGE_SUFFIX
            if not line.startswith(prefix) or not line.endswith(suffix):
                return None
            return line[len(prefix) : -len(suffix)].strip()

        for idx, line in enumerate(lines):
            msg_marker_id = parse_message_id(line)
            if msg_marker_id:
                message_markers.append((idx, msg_marker_id))

        # Find current message bounds
        start_idx = None
        end_idx = len(lines)
        for i, (idx, marker_id) in enumerate(message_markers):
            if marker_id == msg_id:
                start_idx = idx + 1
                if i + 1 < len(message_markers):
                    end_idx = message_markers[i + 1][0]
                break

        if start_idx is not None and start_idx <= end_idx:
            message_slice = lines[start_idx:end_idx]
            while message_slice and message_slice[0] == "":
                message_slice.pop(0)  # drop leading blank separators
            message_text_from_buffer = "\n".join(message_slice).rstrip("\n")

    if message_text_from_buffer:
        message_text = message_text_from_buffer
    elif not message_text:
        plugin.nvim.err_write(f"Warning: Empty message content for {msg_id}\n")
        return

    cleaned_content, markers_json = history.extract_markers_from_content(message_text)

    updated = db.update_message(
        msg_id,
        content=cleaned_content,
        ended_at=end_timestamp,
        markers=markers_json,
    )

    if not updated:
        db.save_message_dict(
            msg_id=msg_id,
            conversation_id=conversation_id,
            role="assistant",
            content=cleaned_content,
            author=agent_name,
            model=DEFAULT_MODEL,
            created_at=timestamp,
            ended_at=end_timestamp,
            markers=markers_json,
        )

    # Update conversation timestamp
    if conversation_id:
        db.update_conversation_timestamp(conversation_id, end_timestamp)

    # Reprocess markers to render duration and other metadata
    ui.process_markers(plugin.nvim, chat_bufnr)


def flush_parallel_tools(
    plugin,
    tools_list: list[dict],
    collected_content: list[str],
    chat_bufnr: int,
):
    """Flush collected parallel tool calls as a single combined output block.

    Args:
        plugin: Plugin instance
        tools_list: List of {name, args, status} dicts
        collected_content: Content list to append to
        chat_bufnr: Buffer number for streaming
    """
    if not tools_list:
        return

    # Format all tools on same line with pipe separators
    tool_headers = []
    for tool in tools_list:
        formatted = utils.format_tool_header(tool["name"], tool["args"])
        tool_headers.append(formatted)

    # Create single combined output with all tools on same line
    combined = " | ".join(tool_headers)
    # Use the status from the first tool (all should be same for parallel execution)
    status = tools_list[0]["status"]

    # Add fold_start marker after the tool headers
    output = combined + "\n" + markers.make_marker("fold_start", status) + "\n"
    collected_content.append(output)

    # Stream to buffer
    # Note: need to check cancelled status from outside or pass it in?
    if not getattr(plugin, "_request_cancelled", False):
        plugin.nvim.async_call(
            ui.stream_text_to_buffer,
            plugin.nvim,
            chat_bufnr,
            output,
        )
