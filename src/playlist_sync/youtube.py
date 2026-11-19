"""YouTube Data API v3 with an installed-app OAuth client and a quota ledger."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from . import tokens
from .quota import Quota, QuotaExhausted

SCOPES = ["https://www.googleapis.com/auth/youtube"]
API = "https://www.googleapis.com/youtube/v3"
TOKEN_NAME = "youtube_refresh_token"
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}


class YouTubeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Video:
    id: str
    title: str
    channel_title: str
    description: str = ""
    duration_s: int = 0
    views: int = 0
    embeddable: bool = True
    available: bool = True


@dataclass(frozen=True)
class Item:
    item_id: str
    video_id: str


def authorize(client_file: Path) -> None:
    if not client_file.exists():
        raise YouTubeError(f"Google OAuth client file not found: {client_file}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise YouTubeError("Google returned no refresh token")
    tokens.put(TOKEN_NAME, creds.refresh_token)


def credentials(client_file: Path) -> Credentials:
    refresh = tokens.get(TOKEN_NAME)
    if not refresh:
        raise YouTubeError("not signed in to YouTube: run `playlist-sync auth youtube`")
    info = json.loads(client_file.read_text())
    info = info.get("installed") or info.get("web")
    return Credentials(None, refresh_token=refresh, token_uri=info["token_uri"],
                       client_id=info["client_id"], client_secret=info["client_secret"], scopes=SCOPES)


_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")


def parse_duration(value: str) -> int:
    m = _DURATION.fullmatch(value or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def video_from_api(v: dict, region: str) -> Video:
    snippet, status, details = v.get("snippet", {}), v.get("status", {}), v.get("contentDetails", {})
    restriction = details.get("regionRestriction", {})
    blocked = region in restriction.get("blocked", []) or (
        "allowed" in restriction and region not in restriction["allowed"])
    available = (status.get("privacyStatus") in ("public", "unlisted")
                 and status.get("uploadStatus", "processed") == "processed"
                 and not blocked)
    return Video(
        id=v["id"],
        title=snippet.get("title", ""),
        channel_title=snippet.get("channelTitle", ""),
        description=snippet.get("description", ""),
        duration_s=parse_duration(details.get("duration", "")),
        views=int(v.get("statistics", {}).get("viewCount", 0)),
        embeddable=status.get("embeddable", True),
        available=available,
    )


def _reason(response) -> str | None:
    try:
        errors = response.json().get("error", {}).get("errors", [])
    except ValueError:
        return None
    return errors[0].get("reason") if errors else None


HINTS = {"manualSortRequired": "set the playlist's ordering to Manual in YouTube (playlist settings), then re-run"}


def _error_text(response) -> str:
    """One line: Google's message and reason, plus a fix when we know one."""
    try:
        err = response.json().get("error", {})
    except ValueError:
        return " ".join(response.text[:300].split())
    reason = _reason(response)
    text = f"{err.get('message', '').strip()} ({reason})" if reason else err.get("message", "").strip()
    return f"{text}: {HINTS[reason]}" if reason in HINTS else text


class YouTubeClient:
    def __init__(self, creds: Credentials, quota: Quota, region: str = "US"):
        self.session = AuthorizedSession(creds)
        self.quota = quota
        self.region = region

    def _call(self, method: str, resource: str, op: str, params: dict, body: dict | None = None) -> dict:
        self.quota.spend(op)
        try:
            r = self.session.request(method, f"{API}/{resource}", params=params, json=body, timeout=30)
        except RefreshError as e:
            # A Testing-mode Google app's refresh token lasts 7 days; a revoked one fails the same way.
            raise YouTubeError(f"YouTube sign-in expired or revoked ({e}); run `playlist-sync auth youtube`") from None
        if r.status_code == 403 and _reason(r) in QUOTA_REASONS:
            self.quota.exhaust()
            raise QuotaExhausted("YouTube reports the daily quota is used up")
        if not r.ok:
            raise YouTubeError(f"{method} {resource}: {r.status_code} {_error_text(r)}")
        return r.json() if r.content else {}

    def search(self, query: str, results: int = 10) -> list[str]:
        data = self._call("GET", "search", "search", {
            "part": "snippet", "q": query, "type": "video", "maxResults": results, "regionCode": self.region})
        return [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]

    def videos(self, ids: list[str]) -> dict[str, Video]:
        """Details for up to any number of IDs, 50 per call. Deleted videos are simply absent."""
        unique = list(dict.fromkeys(ids))
        out: dict[str, Video] = {}
        for start in range(0, len(unique), 50):
            data = self._call("GET", "videos", "videos.list", {
                "part": "snippet,contentDetails,status,statistics", "id": ",".join(unique[start:start + 50])})
            for v in data.get("items", []):
                out[v["id"]] = video_from_api(v, self.region)
        return out

    def my_playlists(self) -> list[dict]:
        out, token = [], None
        while True:
            params = {"part": "snippet,contentDetails,status", "mine": "true", "maxResults": 50}
            if token:
                params["pageToken"] = token
            data = self._call("GET", "playlists", "playlists.list", params)
            out += [{"id": p["id"], "name": p["snippet"]["title"], "total": p["contentDetails"]["itemCount"],
                     "privacy": p["status"]["privacyStatus"]} for p in data.get("items", [])]
            token = data.get("nextPageToken")
            if not token:
                return out

    def playlist_exists(self, playlist_id: str) -> bool:
        data = self._call("GET", "playlists", "playlists.list", {"part": "id", "id": playlist_id})
        return bool(data.get("items"))

    def create_playlist(self, title: str, privacy: str, description: str = "") -> str:
        data = self._call("POST", "playlists", "playlists.insert", {"part": "snippet,status"}, {
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": privacy},
        })
        return data["id"]

    def playlist_items(self, playlist_id: str) -> list[Item]:
        rows: list[tuple[int, Item]] = []
        token = None
        while True:
            params = {"part": "snippet", "playlistId": playlist_id, "maxResults": 50}
            if token:
                params["pageToken"] = token
            data = self._call("GET", "playlistItems", "playlistItems.list", params)
            for i in data.get("items", []):
                s = i["snippet"]
                rows.append((s.get("position", len(rows)), Item(i["id"], s["resourceId"]["videoId"])))
            token = data.get("nextPageToken")
            if not token:
                return [item for _, item in sorted(rows, key=lambda r: r[0])]

    def _snippet(self, playlist_id: str, video_id: str, position: int) -> dict:
        return {"playlistId": playlist_id, "position": position,
                "resourceId": {"kind": "youtube#video", "videoId": video_id}}

    def insert(self, playlist_id: str, video_id: str, position: int) -> str:
        data = self._call("POST", "playlistItems", "playlistItems.insert", {"part": "snippet"},
                          {"snippet": self._snippet(playlist_id, video_id, position)})
        return data["id"]

    def move(self, playlist_id: str, item_id: str, video_id: str, position: int) -> None:
        self._call("PUT", "playlistItems", "playlistItems.update", {"part": "snippet"},
                   {"id": item_id, "snippet": self._snippet(playlist_id, video_id, position)})

    def delete(self, item_id: str) -> None:
        self._call("DELETE", "playlistItems", "playlistItems.delete", {"id": item_id})
