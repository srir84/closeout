"""Key files (design 3.6). `make keys` creates them; `make reset` makes new ones.

- approval_ed25519.key  private signing key: the web app only
- approval_ed25519.pub  public key: worker and sender
- hmac.key              address and suppression HMACs: web, worker, sender
- session.key           demo sign-in cookies and CSRF tokens: web only

Compose mounts each file on its own, so the worker container can't read the private key.
"""

from __future__ import annotations

import os
import secrets
import sys
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from closeout.settings import get_settings

FILES = ("approval_ed25519.key", "approval_ed25519.pub", "hmac.key", "session.key")


def keys_dir() -> Path:
    return Path(os.environ.get("CLOSEOUT_KEYS_DIR", str(get_settings().closeout_keys_dir)))


def create(directory: Path, force: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if not force and all((directory / f).exists() for f in FILES):
        return
    priv = Ed25519PrivateKey.generate()
    (directory / "approval_ed25519.key").write_bytes(
        priv.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    (directory / "approval_ed25519.pub").write_bytes(
        priv.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    (directory / "hmac.key").write_text(secrets.token_hex(32))
    (directory / "session.key").write_text(secrets.token_hex(32))
    for f in FILES:
        (directory / f).chmod(0o644)  # read by the container user; the folder itself is git-ignored


@lru_cache
def private_key() -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(
        (keys_dir() / "approval_ed25519.key").read_bytes(), password=None
    )
    assert isinstance(key, Ed25519PrivateKey)
    return key


@lru_cache
def public_key() -> Ed25519PublicKey:
    key = serialization.load_pem_public_key((keys_dir() / "approval_ed25519.pub").read_bytes())
    assert isinstance(key, Ed25519PublicKey)
    return key


@lru_cache
def hmac_key() -> bytes:
    return bytes.fromhex((keys_dir() / "hmac.key").read_text().strip())


@lru_cache
def session_key() -> bytes:
    return bytes.fromhex((keys_dir() / "session.key").read_text().strip())


def clear_cache() -> None:
    for f in (private_key, public_key, hmac_key, session_key):
        f.cache_clear()


if __name__ == "__main__":
    force = "--force" in sys.argv
    create(keys_dir(), force=force)
    print(f"keys ready in {keys_dir()}")
