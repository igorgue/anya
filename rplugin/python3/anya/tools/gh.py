import os
import subprocess
from agents import function_tool

from .utils import create_error_handler


@function_tool(failure_error_function=create_error_handler)
async def gh(command: str, cwd: str = None, timeout: int = 30) -> str:
    """Execute GitHub CLI (gh) commands to interact with GitHub repositories.

    The gh CLI provides seamless integration with GitHub from the command line.
    This tool wraps all gh functionality, allowing you to manage repositories,
    issues, pull requests, releases, and more.

    CORE COMMANDS:
      auth:        Authenticate gh and git with GitHub
      browse:      Open the repository in the browser
      codespace:   Connect to and manage codespaces
      gist:        Manage gists
      issue:       Manage issues
      org:         Manage organizations
      pr:          Manage pull requests
      project:     Work with GitHub Projects
      release:     Manage releases
      repo:        Manage repositories

    GITHUB ACTIONS COMMANDS:
      cache:       Manage Github Actions caches
      run:         View details about workflow runs
      workflow:    View details about GitHub Actions workflows

    EXTENSION COMMANDS:
      copilot:     Extension copilot

    ADDITIONAL COMMANDS:
      alias:       Create command shortcuts
      api:         Make an authenticated GitHub API request
      completion:  Generate shell completion scripts
      config:      Manage configuration for gh
      extension:   Manage gh extensions
      gpg-key:     Manage GPG keys
      label:       Manage labels
      ruleset:     View info about repo rulesets
      search:      Search for repositories, issues, and pull requests
      secret:      Manage GitHub secrets
      ssh-key:     Manage SSH keys
      status:      Print information about relevant issues, pull requests, and notifications across repositories
      variable:    Manage GitHub Actions variables

    HELP TOPICS:
      actions:     Learn about working with GitHub Actions
      environment: Environment variables that can be used with gh
      exit-codes:  Exit codes used by gh
      formatting:  Formatting options for JSON data exported from gh
      mintty:      Information about using gh with MinTTY
      reference:   A comprehensive reference of all gh commands

    FLAGS:
      --help      Show help for command
      --version   Show gh version

    EXAMPLES:
      gh issue create
      gh repo clone cli/cli
      gh pr checkout 321
      gh pr list --author "@me"
      gh release create v1.0.0 --notes "Initial release"
      gh search repos --language python --stars ">100"

    LEARN MORE:
      Use `gh <command> <subcommand> --help` for more information about a command.
      Read the manual at https://cli.github.com/manual

    Args:
        command: GitHub CLI command to execute (should start with 'gh')
        cwd: Current working directory for the command (defaults to current directory)
        timeout: Timeout in seconds (default 30)

    Returns:
        Combined output with stdout and stderr, or error message
    """
    # Ensure command starts with 'gh'
    if not command.strip().startswith("gh"):
        command = f"gh {command}"

    if cwd is None:
        cwd = os.getcwd()

    # Use Popen to get full control over stdout/stderr
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        raise Exception(f"GitHub CLI command timed out after {timeout} seconds")

    # Build output with both stdout and stderr
    output_parts = []

    if stdout:
        output_parts.append(f"STDOUT:\n{stdout}")

    if stderr:
        if output_parts:
            output_parts.append("")  # Add blank line separator
        output_parts.append(f"STDERR:\n{stderr}")

    if process.returncode != 0:
        if output_parts:
            output_parts.append("")
        output_parts.append(f"Exit code: {process.returncode}")

    return "\n".join(output_parts) if output_parts else "(no output)"
