"""Configuration: config.toml in the home directory, secrets from the environment."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PRIVACY = ("public", "unlisted", "private")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlaylistConfig:
    name: str
    spotify_id: str
    youtube_id: str | None = None
    privacy: str = "private"


@dataclass(frozen=True)
class Config:
    home: Path
    playlists: list[PlaylistConfig]
    overrides: dict[str, str] = field(default_factory=dict)
    trusted_channels: tuple[str, ...] = ()
    region: str = "US"
    daily_budget: int = 9000

    @property
    def state_dir(self) -> Path:
        return self.home / ".state"

    @property
    def google_client_file(self) -> Path:
        configured = env("PLAYLIST_SYNC_GOOGLE_CLIENT_FILE", required=False)
        return Path(configured) if configured else self.state_dir / "google_client.json"


def home_dir() -> Path:
    """PLAYLIST_SYNC_HOME, else the working directory (the repo checkout)."""
    return Path(env("PLAYLIST_SYNC_HOME", required=False) or Path.cwd())


def _dotenv() -> dict[str, str]:
    path = Path.home() / ".env.local"
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def env(name: str, required: bool = True) -> str | None:
    """Read from the environment, falling back to ~/.env.local."""
    value = os.environ.get(name) or _dotenv().get(name)
    if required and not value:
        raise ConfigError(f"{name} is not set (environment or ~/.env.local)")
    return value


def load(home: Path | None = None) -> Config:
    home = home or home_dir()
    path = home / "config.toml"
    if not path.exists():
        raise ConfigError(f"no config.toml in {home}; copy config.example.toml")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    playlists = []
    for entry in raw.get("playlist", []):
        privacy = entry.get("privacy", "private")
        if privacy not in PRIVACY:
            raise ConfigError(f"playlist {entry.get('name')!r}: privacy must be one of {PRIVACY}")
        try:
            playlists.append(PlaylistConfig(entry["name"], entry["spotify_id"], entry.get("youtube_id"), privacy))
        except KeyError as missing:
            raise ConfigError(f"playlist entry is missing {missing}") from None

    return Config(
        home=home,
        playlists=playlists,
        overrides={str(k): str(v) for k, v in raw.get("overrides", {}).items()},
        trusted_channels=tuple(raw.get("trusted_channels", ())),
        region=raw.get("region", "US"),
        daily_budget=int(raw.get("daily_budget", 9000)),
    )
