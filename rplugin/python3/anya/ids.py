from hashids import Hashids


def new(salt: str, n: int) -> str:
    hashids = Hashids(salt=salt, min_length=6)

    return hashids.encode(n)


def salt() -> str:
    """Load or generate a completely random salt that is stored at ~/.local/share/anya/salt.txt"""
    import pathlib
    import secrets

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
