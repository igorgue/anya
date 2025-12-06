import json
import os
import pathlib
import secrets

from hashids import Hashids

data_dir = os.environ.get("XDG_DATA_HOME")
generated_ids: dict[str, int] = dict()


def new(salt: str) -> str:
    """Generate a new unique hashid for a conversation or message identified by the given salt."""
    if len(generated_ids) == 0:
        # Load existing state if available
        global generated_ids
        generated_ids = load()

    hashids = Hashids(salt=salt, min_length=6)

    n = generated_ids.get(salt, 0) + 1
    generated_ids[salt] = n

    save()

    return hashids.encode(n)


def salt() -> str:
    """Load or generate a completely random salt that is stored at XDG_DATA_HOME/anya/salt.txt

    Uses XDG_DATA_HOME environment variable if set, otherwise defaults to ~/.local/share
    """
    if data_dir:
        salt_file = pathlib.Path(data_dir) / "anya" / "salt.txt"
    else:
        salt_file = pathlib.Path.home() / ".local" / "share" / "anya" / "salt.txt"

    salt_file.parent.mkdir(parents=True, exist_ok=True)

    if salt_file.exists():
        with open(salt_file, "r") as f:
            return f.read().strip()
    else:
        new_salt = secrets.token_hex(16)
        with open(salt_file, "w") as f:
            f.write(new_salt)
        return new_salt


def save() -> None:
    """saves the current state of generated_ids to a file for persistence across sessions."""
    state_file = _get_state_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)

    with open(state_file, "w") as f:
        json.dump(generated_ids, f)


def load() -> dict[str, int]:
    """loads the state of generated_ids from a file if it exists."""
    state_file = _get_state_file()

    if state_file.exists():
        with open(state_file, "r") as f:
            return json.load(f)
    else:
        return {}


def _get_state_file():
    """Helper function to get the state file path."""
    if data_dir:
        return pathlib.Path(data_dir) / "anya" / "ids.json"
    else:
        return pathlib.Path.home() / ".local" / "share" / "anya" / "ids.json"
