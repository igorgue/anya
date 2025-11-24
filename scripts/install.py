import sys
import os
import subprocess
import venv
from pathlib import Path

def install():
    # Define paths
    script_path = Path(__file__).resolve()
    
    # Find repo root by looking for requirements.txt
    plugin_root = None
    current = script_path.parent
    while current != current.parent:
        if (current / "requirements.txt").exists() and (current / "pyproject.toml").exists():
            plugin_root = current
            break
        current = current.parent
        
    if plugin_root is None:
        # Fallback to hardcoded assumption if walk fails (unlikely)
        print("Could not find requirements.txt by walking up. defaulting to script.parent.parent")
        plugin_root = script_path.parent.parent

    venv_dir = os.path.expanduser("~/.local/share/agent.nvim/venv")
    req_file = plugin_root / "requirements.txt"

    print(f"Script path: {script_path}")
    print(f"Plugin root: {plugin_root}")
    print(f"Requirements file: {req_file}")
    print(f"Venv dir: {venv_dir}")

    # Create venv
    if not os.path.exists(venv_dir):
        print("Creating virtual environment...")
        venv.create(venv_dir, with_pip=True)

    # Install requirements
    if not os.path.exists(req_file):
        print(f"Error: requirements.txt not found at {req_file}")
        return

    pip_exe = os.path.join(venv_dir, "bin", "pip")
    
    print("Installing requirements...")
    try:
        subprocess.check_call([pip_exe, "install", "-r", req_file])
        print("Dependencies installed successfully!")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install dependencies: {e}")

if __name__ == "__main__":
    install()
