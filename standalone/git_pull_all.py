#!/usr/bin/env python3
"""Run git pull --rebase on all subdirectories that are git repositories."""

import os
import subprocess
from pathlib import Path


def is_git_repo(path: Path) -> bool:
    """Check if a directory is a git repository."""
    return (path / ".git").exists()


def run_git_pull_rebase(repo_path: Path) -> tuple[bool, str]:
    """Run git pull --rebase in the given repository path."""
    try:
        result = subprocess.run(
            ["git", "pull", "--rebase"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Timeout expired"
    except Exception as e:
        return False, str(e)


def main():
    current_dir = Path.cwd()
    
    # Get all subdirectories
    subdirs = sorted([d for d in current_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])
    
    # Filter to only git repositories
    git_repos = [d for d in subdirs if is_git_repo(d)]
    
    if not git_repos:
        print("No git repositories found in subdirectories.")
        return
    
    print(f"Found {len(git_repos)} git repositories\n")
    
    success_count = 0
    fail_count = 0
    
    for repo in git_repos:
        print(f"{'='*60}")
        print(f"📦 {repo.name}")
        print(f"{'='*60}")
        
        success, output = run_git_pull_rebase(repo)
        
        if success:
            success_count += 1
            print(f"✅ Success\n")
        else:
            fail_count += 1
            print(f"❌ Failed\n")
        
        if output.strip():
            print(output.strip())
            print()
    
    print(f"\n{'='*60}")
    print(f"Summary: {success_count} succeeded, {fail_count} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
