"""Tool to check and manage background processes started by run_code."""

import os
from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext
from .utils import create_error_handler
from .run_code import (
    get_background_process_status,
    list_background_processes,
    cleanup_completed_processes,
)


@function_tool(failure_error_function=create_error_handler)
async def background_status(
    ctx: RunContextWrapper[NvimPluginContext],
    process_id: str = None,
    action: str = "status",
) -> str:
    """Check status of background processes started with run_code(background=True).

    Args:
        ctx: The RunContextWrapper containing the plugin context.
        process_id: The process ID to check (optional). If not provided, lists all processes.
        action: Action to perform (default "status"):
            - "status": Get current status and output
            - "list": List all background processes
            - "output": Read full output file
            - "cleanup": Remove completed processes older than 24 hours

    Returns:
        str: Status information or output from the background process.
    """
    plugin_context = ctx.context
    cwd = plugin_context.cwd if plugin_context.cwd else os.getcwd()
    
    if action == "cleanup":
        removed = cleanup_completed_processes(max_age_hours=24)
        return f"Cleaned up {removed} completed process(es) from registry."
    
    if action == "list" or not process_id:
        processes = list_background_processes()
        if not processes:
            return "No background processes found."
        
        lines = ["Background processes:"]
        for p in processes:
            pid = p["process_id"]
            status = p.get("status", "unknown")
            title = p.get("title", "untitled")
            start_time = p.get("start_time", "unknown")
            output_file = p.get("output_file", "")
            
            lines.append(f"\n  [{pid}] {title}")
            lines.append(f"    Status: {status}")
            lines.append(f"    Started: {start_time}")
            if output_file:
                lines.append(f"    Output: {output_file}")
        
        return "\n".join(lines)
    
    # Get specific process status
    info = get_background_process_status(process_id)
    if not info:
        return f"Process {process_id} not found. It may have been cleaned up or never existed."
    
    if action == "output":
        output_file = info.get("output_file")
        if not output_file or not os.path.exists(output_file):
            return f"No output file found for process {process_id}."
        
        try:
            with open(output_file, "r") as f:
                content = f.read()
            return content if content.strip() else f"Output file is empty for process {process_id}."
        except Exception as e:
            return f"Error reading output file: {e}"
    
    # Default: status action
    lines = [f"Process {process_id}:"]
    lines.append(f"  Title: {info.get('title', 'untitled')}")
    lines.append(f"  Status: {info.get('status', 'unknown')}")
    lines.append(f"  Started: {info.get('start_time', 'unknown')}")
    
    if info.get('end_time'):
        lines.append(f"  Ended: {info.get('end_time')}")
    if info.get('returncode') is not None:
        lines.append(f"  Exit code: {info.get('returncode')}")
    
    output_file = info.get('output_file')
    if output_file:
        lines.append(f"  Output file: {output_file}")
        
        # Show last few lines of output if file exists
        if os.path.exists(output_file):
            try:
                with open(output_file, "r") as f:
                    content = f.read()
                lines_output = content.strip().split("\n")
                if len(lines_output) > 10:
                    lines.append("\n  Last 10 lines of output:")
                    lines.extend([f"    {line}" for line in lines_output[-10:]])
                else:
                    lines.append("\n  Output:")
                    lines.extend([f"    {line}" for line in lines_output])
            except Exception as e:
                lines.append(f"  (Could not read output: {e})")
    
    return "\n".join(lines)
