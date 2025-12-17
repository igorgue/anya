from agents import function_tool

from .utils import create_error_handler
from .read_file import read_file_impl


@function_tool(failure_error_function=create_error_handler)
async def read_many_files(files: list[str], cwd: str = None) -> str:
    """Reads multiple files in a single call, supporting line ranges.

    Each file in the list can include optional @range specification:
        "filename.py"              - Read first 100 lines (default)
        "filename.py@start-end"    - Read entire file
        "filename.py@32-234"       - Read lines 32-234
        "path/to/file.py@1-50"     - Read lines 1-50

    Args:
        files: List of file paths with optional @range specifications
        cwd: Current working directory for relative path resolution

    Returns:
        Combined content from all files with metadata, or error messages
    """
    if not files:
        raise Exception("No files specified.")

    if not isinstance(files, list):
        raise Exception(f"Expected list of files, got {type(files).__name__}")

    results = []
    file_count = 0
    error_count = 0

    for file_spec in files:
        if not isinstance(file_spec, str):
            results.append(
                f"[Skipped: Invalid file spec type {type(file_spec).__name__}]"
            )
            error_count += 1
            continue

        file_spec = file_spec.strip()
        if not file_spec:
            continue

        # Call the underlying read_file implementation directly
        try:
            content = read_file_impl(file_spec, cwd)
        except Exception as e:
            content = f"Error reading {file_spec}: {str(e)}"

        # Add separator between files
        results.append(content)
        results.append("\n" + "=" * 70 + "\n")
        file_count += 1

    # Build header with summary
    header = f"Reading {file_count} file(s)...\n{'=' * 70}\n\n"

    return header + "\n".join(results)
