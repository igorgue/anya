"""Cryptographic identity for Anya daemon clients.

Each daemon generates an Ed25519 keypair. The public key hash becomes its client_id,
used for WebSocket authentication and pairing with Telegram users.

Flow:
1. Daemon generates keypair -> stores private key locally
2. Daemon connects to Router via WebSocket, signs challenge with private key
3. Router verifies signature using public key -> authenticates connection
4. Pairing codes are signed so only the daemon can claim them
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

CLIENT_ID_PREFIX = "ac"  # "anya client"


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a new Ed25519 keypair.

    Returns:
        (public_key, private_key) as raw bytes
    """
    private_key = ed25519.Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return public_bytes, private_bytes


def public_key_to_client_id(public_key: bytes) -> str:
    """Derive a human-readable client ID from a public key.

    Uses BLAKE2b hash of the public key, base32-encoded with a prefix.
    Example: ac1q0x7f8g9h0j2k3l4z5x6c7v8b9n0m
    """
    raw_hash = hashlib.blake2b(public_key, digest_size=16).digest()
    # base32 encoding (lowercase, no padding)
    b32 = base64.b32hexencode(raw_hash).decode().lower().rstrip("=")
    return f"{CLIENT_ID_PREFIX}{b32[:28]}"


def sign_message(private_key_bytes: bytes, message: bytes) -> bytes:
    """Sign a message with the private key.

    Returns:
        Detached signature (64 bytes)
    """
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    return private_key.sign(message)


def verify_signature(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify a detached signature.

    Args:
        public_key: The Ed25519 public key
        message: The original message
        signature: The 64-byte signature

    Returns:
        True if the signature is valid
    """
    try:
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key)
        pub_key.verify(signature, message)
        return True
    except Exception:
        return False


def generate_pairing_code() -> str:
    """Generate a short, human-readable pairing code.

    Format: XXX-NNN (4 letters, dash, 2 digits)
    Example: X7K-3M9
    """
    import secrets
    import string

    letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(4))
    digits = "".join(secrets.choice(string.digits) for _ in range(2))
    return f"{letters}-{digits}"


def generate_challenge() -> bytes:
    """Generate a random challenge for WebSocket authentication.

    Returns:
        32 random bytes
    """
    return os.urandom(32)
