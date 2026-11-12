"""Spotify Web API: PKCE sign-in and reads of playlists you own.

Development-mode apps (February 2026 rules) need a Premium owner and can only
read the items of playlists the signed-in user owns or collaborates on.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from . import tokens

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-read-private playlist-read-collaborative"
TOKEN_NAME = "spotify_refresh_token"


class SpotifyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artists: tuple[str, ...]
    duration_ms: int

    @property
    def label(self) -> str:
        return f"{self.title} – {', '.join(self.artists)}"


def parse_entry(entry: dict) -> Track | None:
    """One playlist entry to a Track. Reads the 2026 `item` key, falling back to `track`."""
    obj = entry.get("item") or entry.get("track")
    if not obj or obj.get("type", "track") != "track" or obj.get("is_local") or not obj.get("id"):
        return None
    artists = tuple(a["name"] for a in obj.get("artists", []))
    return Track(obj["id"], obj["name"], artists, int(obj.get("duration_ms") or 0))


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _wait_for_callback(timeout: int = 300) -> dict[str, str]:
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in parse_qs(url.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Spotify authorized. You can close this tab.")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 8888), Handler)
    server.timeout = 1
    deadline = time.monotonic() + timeout
    try:
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    return result


def authorize(client_id: str) -> None:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    })
    print(f"Opening the browser for Spotify sign-in. If it doesn't open, visit:\n{url}")
    webbrowser.open(url)

    callback = _wait_for_callback()
    if "error" in callback:
        raise SpotifyError(f"Spotify sign-in failed: {callback['error']}")
    if not callback or callback.get("state") != state:
        raise SpotifyError("no valid callback from Spotify (timed out or state mismatch)")

    r = requests.post(TOKEN_URL, timeout=30, data={
        "grant_type": "authorization_code",
        "code": callback["code"],
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "code_verifier": verifier,
    })
    r.raise_for_status()
    tokens.put(TOKEN_NAME, r.json()["refresh_token"])


def _reason(response: requests.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("reason") or (body.get("error") or {}).get("reason")


class SpotifyClient:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.refresh_token = tokens.get(TOKEN_NAME)
        if not self.refresh_token:
            raise SpotifyError("not signed in to Spotify: run `playlist-sync auth spotify`")
        self.session = requests.Session()
        self._access: str | None = None
        self._expires = 0.0

    def _token(self) -> str:
        if self._access and time.monotonic() < self._expires - 60:
            return self._access
        r = requests.post(TOKEN_URL, timeout=30, data={
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
        })
        if r.status_code == 400:
            raise SpotifyError(f"Spotify token refresh failed ({r.text}); run `playlist-sync auth spotify`")
        r.raise_for_status()
        body = r.json()
        self._access = body["access_token"]
        self._expires = time.monotonic() + body.get("expires_in", 3600)
        # PKCE refresh tokens rotate; keep the newest one.
        if body.get("refresh_token") and body["refresh_token"] != self.refresh_token:
            self.refresh_token = body["refresh_token"]
            tokens.put(TOKEN_NAME, self.refresh_token)
        return self._access

    def get(self, path_or_url: str, params: dict | None = None) -> dict:
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        for _ in range(5):
            r = self.session.get(url, params=params, timeout=30,
                                 headers={"Authorization": f"Bearer {self._token()}"})
            if r.status_code == 429:
                if _reason(r) == "QUOTA_EXCEEDED":
                    raise SpotifyError("Spotify developer quota exceeded")
                time.sleep(min(int(r.headers.get("Retry-After", "5")), 60))
                continue
            if not r.ok:
                raise SpotifyError(f"GET {url}: {r.status_code} {r.text[:300]}")
            return r.json()
        raise SpotifyError(f"still rate limited after retries: {url}")

    def _pages(self, path: str, params: dict):
        page = self.get(path, params)
        while page:
            yield from page.get("items", [])
            page = self.get(page["next"]) if page.get("next") else None

    def my_playlists(self) -> list[dict]:
        out = []
        for p in self._pages("/me/playlists", {"limit": 50}):
            total = (p.get("items") or p.get("tracks") or {}).get("total")
            out.append({"id": p["id"], "name": p["name"], "owner": p["owner"].get("display_name"), "total": total})
        return out

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        tracks = []
        for entry in self._pages(f"/playlists/{playlist_id}/items", {"limit": 50}):
            track = parse_entry(entry)
            if track:
                tracks.append(track)
        return tracks
