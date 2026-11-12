"""Sync one Spotify playlist into one YouTube playlist."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import diff, matcher
from .config import Config, PlaylistConfig
from .quota import QuotaExhausted
from .spotify import SpotifyClient, Track
from .youtube import Video, YouTubeClient, YouTubeError

RETRY_AFTER = timedelta(days=14)  # how long a failed or low-confidence match waits before another search
ADOPT_TITLE_HIT = 0.8             # an existing playlist video is kept when its title names the track this well


class JsonStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")


class MatchCache(JsonStore):
    """Spotify track id to the chosen video, so each track is searched once."""

    def get(self, track_id: str) -> dict | None:
        return self.data.get(track_id)

    def put(self, track_id: str, match: matcher.Match, today: date, status: str | None = None) -> None:
        if status is None:
            status = "matched" if match.confidence in ("high", "medium") else ("low" if match.video_id else "unmatched")
        self.data[track_id] = {"status": status, "video_id": match.video_id, "confidence": match.confidence,
                               "tier": match.tier, "title": match.title, "channel": match.channel,
                               "checked": today.isoformat()}

    def drop(self, track_id: str) -> None:
        self.data.pop(track_id, None)

    @staticmethod
    def due(entry: dict, today: date) -> bool:
        return date.fromisoformat(entry["checked"]) + RETRY_AFTER <= today


@dataclass
class Report:
    name: str
    tracks: int = 0
    matched: int = 0
    adopted: int = 0
    searched: int = 0
    youtube_id: str | None = None
    ops: list[str] = field(default_factory=list)
    done: int = 0
    stopped: str | None = None
    review: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _review_line(track: Track, entry: dict) -> str:
    line = f"- {track.label} (Spotify `{track.id}`): {entry['status']}"
    if entry.get("video_id"):
        line += f"; best guess https://youtu.be/{entry['video_id']} \"{entry['title']}\" / {entry['channel']}"
    return line + f'. Pin with `"{track.id}" = "<videoId>"` under [overrides].'


def _describe(op: diff.Op, labels: dict[str, str]) -> str:
    what = labels.get(op.video_id, f"https://youtu.be/{op.video_id}")
    if op.kind == "delete":
        return f"delete {what}"
    return f"{op.kind} {what} at {op.position + 1}"


def adopt(track: Track, videos: list[Video], trusted: tuple[str, ...]) -> matcher.Match | None:
    """The video already in the YouTube playlist that is this track, if any.

    Looser than a search match on purpose: a live cut or lyric video someone put
    there by hand is still that song, and keeping it costs no quota.
    """
    scored = [m for v in videos if (m := matcher.score(track, v, trusted)) and m.title_hit >= ADOPT_TITLE_HIT]
    return max(scored, key=lambda m: m.score) if scored else None


def sync_playlist(pc: PlaylistConfig, cfg: Config, sp: SpotifyClient, yt: YouTubeClient,
                  cache: MatchCache, state: JsonStore, dry_run: bool, today: date | None = None) -> Report:
    today = today or date.today()
    rep = Report(pc.name)
    tracks = sp.playlist_tracks(pc.spotify_id)
    rep.tracks = len(tracks)
    by_id = {t.id: t for t in tracks}
    chosen: dict[str, str] = {}

    try:
        playlist_id = pc.youtube_id or state.data.get("playlists", {}).get(pc.spotify_id)
        if playlist_id and not yt.playlist_exists(playlist_id):
            if pc.youtube_id:
                rep.errors.append(f"configured YouTube playlist {pc.youtube_id} not found")
                return rep
            playlist_id = None
        current = [(i.item_id, i.video_id) for i in yt.playlist_items(playlist_id)] if playlist_id else []

        # Pinned picks, cached picks and what's already in the playlist: one cheap videos.list check.
        pinned = {tid: cfg.overrides[tid] for tid in by_id if tid in cfg.overrides}
        cached = {tid: e["video_id"] for tid in by_id
                  if tid not in pinned and (e := cache.get(tid)) and e["status"] == "matched"}
        info = yt.videos(list(pinned.values()) + list(cached.values()) + [vid for _, vid in current])
    except QuotaExhausted:
        rep.stopped = "quota (reading YouTube)"
        return rep

    for tid, vid in pinned.items():
        if (v := info.get(vid)) and v.available:
            chosen[tid] = vid
        else:
            rep.review.append(f"- {by_id[tid].label}: pinned video {vid} is unavailable; update [overrides].")
    for tid, vid in cached.items():
        if (v := info.get(vid)) and v.available:
            chosen[tid] = vid
        else:
            cache.drop(tid)  # deleted, made private or region-blocked: search again

    # Keep videos already in the playlist that are one of the Spotify tracks.
    unclaimed = {vid: info[vid] for _, vid in current if vid in info and vid not in chosen.values()}
    for tid, track in by_id.items():
        if tid in chosen or tid in pinned or not unclaimed:
            continue
        if match := adopt(track, list(unclaimed.values()), cfg.trusted_channels):
            chosen[tid] = match.video_id
            cache.put(tid, match, today, status="matched")
            unclaimed.pop(match.video_id)
            rep.adopted += 1

    # Everything else gets a search, budget permitting.
    for tid, track in by_id.items():
        if tid in chosen or tid in pinned:
            continue
        entry = cache.get(tid)
        if entry and entry["status"] != "matched" and not cache.due(entry, today):
            rep.review.append(_review_line(track, entry))
            continue
        try:
            ids = yt.search(matcher.search_query(track))
            match = matcher.best(track, list(yt.videos(ids).values()), cfg.trusted_channels)
        except QuotaExhausted:
            rep.stopped = "quota (matching)"
            break
        rep.searched += 1
        cache.put(tid, match, today)
        if cache.get(tid)["status"] == "matched":
            chosen[tid] = match.video_id
        else:
            rep.review.append(_review_line(track, cache.get(tid)))
    cache.save()

    desired = [chosen[t.id] for t in tracks if t.id in chosen]
    rep.matched = len(desired)
    labels = {vid: f"\"{v.title}\" ({v.channel_title})" for vid, v in info.items()}
    labels.update({chosen[t.id]: t.label for t in tracks if t.id in chosen})
    if rep.stopped:
        # A partial match list would delete or misplace items; finish matching on a later run first.
        return rep

    try:
        if not playlist_id and not dry_run:
            playlist_id = yt.create_playlist(pc.name, pc.privacy, "Synced from Spotify by playlist-sync.")
            state.data.setdefault("playlists", {})[pc.spotify_id] = playlist_id
            state.save()
        rep.youtube_id = playlist_id or "(created on the first real run)"

        ops = diff.plan(current, desired)
        rep.ops = [_describe(op, labels) for op in ops]
        if dry_run:
            return rep
        for op in ops:
            if op.kind == "delete":
                yt.delete(op.item_id)
            elif op.kind == "insert":
                yt.insert(playlist_id, op.video_id, op.position)
            else:
                yt.move(playlist_id, op.item_id, op.video_id, op.position)
            rep.done += 1
    except QuotaExhausted:
        rep.stopped = "quota (writing)"
    except YouTubeError as e:
        # Later positions assume this op succeeded; the next run replans from the real state.
        rep.errors.append(str(e))
    return rep
