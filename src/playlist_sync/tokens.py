"""Refresh tokens live in the OS credential store (Windows Credential Manager, macOS Keychain)."""
from __future__ import annotations

import keyring

SERVICE = "playlist-sync"


def get(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def put(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)
