"""Sync one Spotify playlist with one YouTube playlist.

Spotify sets the order. Two-way playlists also take songs added on YouTube
into Spotify, and carry removals across in both directions; removals are
detected against the pairs recorded at the end of the last complete run.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import diff, matcher
from .config import Config, PlaylistConfig
from .quota import QuotaExhausted
from .spotify import SpotifyClient, SpotifyError, Track
from .youtube import Video, YouTubeClient, YouTubeError

RETRY_AFTER = timedelta(days=14)  # how long a failed or low-confidence match waits before another search
ADOPT_TITLE_HIT = 0.8             # an existing playlist video is kept when its title names the track this well
MIN_UPGRADE = 3                   # lyric video or better
RULES = 2                         # cache entries searched under the quality-class rules
SEARCH_COST = 101                 # search.list + videos.list
WRITE_COST = 50


class JsonStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")


class MatchCache(JsonStore):
    """Spotify track id to its best search result, so each track is searched once."""

    def get(self, track_id: str) -> dict | None:
        return self.data.get(track_id)

    def put(self, track_id: str, match: matcher.Match, today: date) -> None:
        status = "matched" if match.confidence in ("high", "medium") else ("low" if match.video_id else "unmatched")
        self.data[track_id] = {"status": status, "video_id": match.video_id, "confidence": match.confidence,
                               "tier": match.tier, "class": match.cls, "title": match.title,
                               "channel": match.channel, "checked": today.isoformat(), "rules": RULES}

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
    upgraded: int = 0
    youtube_id: str | None = None
    ops: list[str] = field(default_factory=list)
    spotify_ops: list[str] = field(default_factory=list)
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
    """The best video already in the YouTube playlist that is this track, if any.

    Looser than a search match on purpose: a live cut or lyric video someone put
    there by hand is still that song, and keeping it costs no quota.
    """
    scored = [m for v in videos if (m := matcher.score(track, v, trusted)) and m.title_hit >= ADOPT_TITLE_HIT]
    return max(scored, key=matcher.rank) if scored else None


def _segments(title: str) -> list[set[str]]:
    title = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", title)
    parts = re.split(r"\s[-–|:]\s|\|", title)
    return [set(matcher.norm(p).split()) for p in parts if matcher.norm(p)]


def youtube_query(video: Video) -> str:
    """A Spotify search query for a YouTube video title."""
    title = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", video.title)
    title = re.sub(r"(?i)\s(ft|feat|featuring)\.?\s.*$", " ", title)
    title = re.sub(r"\|.*$", " ", title).replace('"', " ")
    if not re.search(r"\s[-–]\s", title):
        channel = re.sub(r"(?i)(\s*-\s*topic$|vevo$|official)", "", video.channel_title).strip()
        title = f"{channel} {title}"
    if matcher.has(matcher.norm(video.title), "remix"):
        title += " remix"  # otherwise the original crowds the remix out of the 10 results
    return re.sub(r"\s+", " ", re.sub(r"\s[-–]\s", " ", title)).strip()


KARAOKE = ("karaoke", "cover", "covers", "instrumental", "backing track")


def knockoff_of(tracks: list[Track], video: Video) -> Track | None:
    """A karaoke or cover upload of a song the playlist already has: a duplicate, not a new song."""
    vt, ch = matcher.norm(video.title), matcher.norm(video.channel_title)
    if not any(matcher.has(vt, k) or matcher.has(ch, k) for k in KARAOKE):
        return None
    tokens = set(vt.split())
    for t in tracks:
        want = matcher.norm(matcher.core_title(t.title)).split()
        if want and all(w in tokens for w in want) and any(
                matcher.has(vt, f) for f in matcher.artist_forms(t.artists)):
            return t
    return None


def spotify_match(sp: SpotifyClient, video: Video, trusted: tuple[str, ...], market: str = "US") -> Track | None:
    """The Spotify track a YouTube video is, if search finds it confidently."""
    best: tuple | None = None
    for track in sp.search_tracks(youtube_query(video), market):
        m = matcher.score(track, video, trusted)
        if not m or m.title_hit < ADOPT_TITLE_HIT or m.confidence == "low":
            continue
        want = set(matcher.norm(matcher.core_title(track.title)).split())
        coverage = max((len(seg & want) / len(seg) for seg in _segments(video.title)), default=0.0)
        if coverage < 0.6:
            continue
        key = (coverage, matcher.rank(m))
        if best is None or key > best[0]:
            best = (key, track)
    return best[1] if best else None


def _search(yt: YouTubeClient, track: Track, trusted: tuple[str, ...]) -> matcher.Match:
    ids = yt.search(matcher.search_query(track))
    return matcher.best(track, list(yt.videos(ids).values()), trusted)


def merge(tracks: list[Track], adds: dict[str, Track], keeps: set[str],
          current: list[tuple[str, str]], chosen: dict[str, str]) -> list[tuple[str, object]]:
    """Spotify order, with YouTube-only songs placed after the song they follow on YouTube.

    Entries are ("track", Track), ("add", Track) for a song to add to Spotify,
    or ("keep", video_id) for a YouTube-only song Spotify doesn't have.
    """
    seq: list[tuple[str, object]] = [("track", t) for t in tracks]
    anchor = -1
    placed: set[str] = set()
    for _, vid in current:
        if vid in placed:
            continue
        if vid in adds or vid in keeps:
            anchor += 1
            seq.insert(anchor, ("add", adds[vid]) if vid in adds else ("keep", vid))
            placed.add(vid)
            continue
        for i, (kind, x) in enumerate(seq):
            if kind == "track" and chosen.get(x.id) == vid:
                anchor = i
                break
    return seq


def sync_playlist(pc: PlaylistConfig, cfg: Config, sp: SpotifyClient, yt: YouTubeClient,
                  cache: MatchCache, state: JsonStore, dry_run: bool, today: date | None = None,
                  upgrades: bool = True) -> Report:
    today = today or date.today()
    rep = Report(pc.name)
    trusted = cfg.trusted_channels
    tracks = sp.playlist_tracks(pc.spotify_id)
    rep.tracks = len(tracks)
    prior = state.data.get("synced", {}).get(pc.spotify_id, {})
    pairs: dict[str, str] = prior.get("pairs", {})

    try:
        playlist_id = pc.youtube_id or state.data.get("playlists", {}).get(pc.spotify_id)
        if playlist_id and not yt.playlist_exists(playlist_id):
            if pc.youtube_id:
                rep.errors.append(f"configured YouTube playlist {pc.youtube_id} not found")
                return rep
            playlist_id = None
        current = [(i.item_id, i.video_id) for i in yt.playlist_items(playlist_id)] if playlist_id else []

        # Pinned picks, cached picks and what's already in the playlist: one cheap videos.list check.
        pinned = {t.id: cfg.overrides[t.id] for t in tracks if t.id in cfg.overrides}
        cached = {t.id: e["video_id"] for t in tracks
                  if t.id not in pinned and (e := cache.get(t.id)) and e["status"] == "matched"}
        info = yt.videos(list(pinned.values()) + list(cached.values()) + [vid for _, vid in current])
    except QuotaExhausted:
        rep.stopped = "quota (reading YouTube)"
        return rep

    in_playlist = {vid for _, vid in current}
    existing = [info[vid] for vid in dict.fromkeys(vid for _, vid in current)
                if vid in info and info[vid].available]

    # Removed on YouTube since the last complete run: remove from Spotify too.
    removed: list[Track] = []
    if pc.two_way and prior.get("complete"):
        for t in tracks:
            if t.id in pairs and pairs[t.id] not in in_playlist and not adopt(t, existing, trusted):
                removed.append(t)
                rep.spotify_ops.append(f"remove {t.label} (removed on YouTube)")
    gone = {t.id for t in removed}
    tracks = [t for t in tracks if t.id not in gone]
    by_id = {t.id: t for t in tracks}

    # Each track: its pin, else what's in the playlist unless a cached pick is a strictly better class.
    chosen: dict[str, str] = {}
    picks: dict[str, matcher.Match] = {}
    for t in tracks:
        if t.id in pinned:
            vid = pinned[t.id]
            if (v := info.get(vid)) and v.available:
                chosen[t.id] = vid
            else:
                rep.review.append(f"- {t.label}: pinned video {vid} is unavailable; update [overrides].")
            continue
        have = adopt(t, existing, trusted)
        cand = None
        if t.id in cached:
            v = info.get(cached[t.id])
            if v and v.available:
                cand = matcher.score(t, v, trusted)
                cand = cand if cand and cand.confidence != "low" else None
            else:
                cache.drop(t.id)  # deleted, made private or region-blocked: search again
        pick = have if have and (not cand or cand.cls <= have.cls) else cand
        if pick:
            chosen[t.id], picks[t.id] = pick.video_id, pick
            rep.adopted += pick is have

    # The same song on Spotify under two track ids (single and album): one YouTube copy serves both.
    groups: dict[tuple[str, str], list[Track]] = {}
    for t in tracks:
        groups.setdefault((matcher.norm(t.title), matcher.norm(t.artists[0] if t.artists else "")), []).append(t)
    twins = [ts for ts in groups.values() if len(ts) > 1]
    for ts in twins:
        rep.review.append(f"- On Spotify {len(ts)} times under different track ids: {ts[0].label} "
                          "(YouTube keeps one copy)")

    def share() -> None:
        for ts in twins:
            pin = next((t for t in ts if t.id in pinned and t.id in chosen), None)
            best = picks.get(pin.id) if pin else max((picks[t.id] for t in ts if t.id in picks),
                                                     key=matcher.rank, default=None)
            vid = chosen[pin.id] if pin else best.video_id if best else None
            for t in ts:
                if vid:
                    chosen[t.id] = vid
                    if best:
                        picks[t.id] = best

    share()

    # Videos nobody chose: a duplicate of a track, removed on Spotify, or new on YouTube.
    adds: dict[str, Track] = {}   # video id -> Spotify track to add
    keeps: set[str] = set()       # YouTube-only songs Spotify doesn't have
    claimed = set(chosen.values())
    for v in existing:
        if v.id in claimed or any(adopt(t, [v], trusted) for t in tracks):
            continue
        if not pc.two_way or v.id in pairs.values():
            continue  # one-way, or its track was removed on Spotify: the diff deletes it
        if knockoff_of(tracks, v):
            continue  # karaoke or cover of a song already here: the diff deletes it
        found = spotify_match(sp, v, trusted, cfg.region)
        if found and found.id in by_id:
            continue  # the same song under another title: a duplicate
        if found and found.id not in {a.id for a in adds.values()}:
            adds[v.id] = found
        elif not found:
            keeps.add(v.id)
            rep.review.append(f"- YouTube only, not found on Spotify (kept): https://youtu.be/{v.id} "
                              f"\"{v.title}\" / {v.channel_title}")

    # Search for tracks with nothing in the playlist, budget permitting.
    for t in tracks:
        if t.id in chosen or t.id in pinned:
            continue
        share()
        if t.id in chosen:
            continue
        entry = cache.get(t.id)
        if entry and entry["status"] != "matched" and not cache.due(entry, today):
            rep.review.append(_review_line(t, entry))
            continue
        try:
            match = _search(yt, t, trusted)
        except QuotaExhausted:
            rep.stopped = "quota (matching)"
            break
        rep.searched += 1
        cache.put(t.id, match, today)
        if cache.get(t.id)["status"] == "matched":
            chosen[t.id], picks[t.id] = match.video_id, match
        else:
            rep.review.append(_review_line(t, cache.get(t.id)))
    cache.save()
    if rep.stopped:
        # A partial match list would delete or misplace items; finish matching on a later run first.
        return rep

    sequence = merge(tracks, adds, keeps, current, chosen)
    video_of_add = {id(t): vid for vid, t in adds.items()}

    def desired() -> list[str]:
        out: list[str] = []
        for kind, x in sequence:
            vid = x if kind == "keep" else video_of_add[id(x)] if kind == "add" else chosen.get(x.id)
            if vid and vid not in out:  # each video once, even when Spotify lists the song twice
                out.append(vid)
        return out

    # Spare quota goes to looking for better official videos, worst class first.
    spare = yt.quota.remaining - WRITE_COST * len(diff.plan(current, desired())) - 200
    upgradable = sorted((t for t in tracks if t.id in picks and picks[t.id].cls < 5
                         and (cache.get(t.id) or {}).get("rules", 1) < RULES),
                        key=lambda t: picks[t.id].cls)
    for t in upgradable if upgrades else ():
        if spare < SEARCH_COST + 2 * WRITE_COST:
            break
        try:
            match = _search(yt, t, trusted)
        except QuotaExhausted:
            break
        spare -= SEARCH_COST
        rep.searched += 1
        cache.put(t.id, match, today)
        # Never swap in a live or audio-only clip: that changes the recording, so it isn't an upgrade
        # even over an unofficial upload of the studio video.
        if match.video_id and match.confidence != "low" and match.cls > picks[t.id].cls and match.cls >= MIN_UPGRADE:
            info[match.video_id] = Video(match.video_id, match.title, match.channel)
            chosen[t.id], picks[t.id] = match.video_id, match
            rep.upgraded += 1
            spare -= 2 * WRITE_COST
    share()
    cache.save()

    spotify_order = [(k, x) for k, x in sequence if k in ("track", "add")]
    for pos, (kind, x) in enumerate(spotify_order):
        if kind == "add":
            rep.spotify_ops.append(f"add {x.label} at {pos + 1} (on YouTube)")
    want = desired()
    rep.matched = sum(1 for k, x in spotify_order if k == "track" and x.id in chosen)
    labels = {vid: f"\"{v.title}\" ({v.channel_title})" for vid, v in info.items()}
    labels.update({chosen[t.id]: t.label for t in tracks if t.id in chosen})
    labels.update({vid: t.label for vid, t in adds.items()})

    try:
        if not playlist_id and not dry_run:
            playlist_id = yt.create_playlist(pc.name, pc.privacy, "Synced from Spotify by playlist-sync.")
            state.data.setdefault("playlists", {})[pc.spotify_id] = playlist_id
            state.save()
        rep.youtube_id = playlist_id or "(created on the first real run)"

        ops = diff.plan(current, want)
        rep.ops = [_describe(op, labels) for op in ops]
        if dry_run:
            return rep

        for t in removed:
            sp.remove_track(pc.spotify_id, t.id)
        for pos, (kind, x) in enumerate(spotify_order):
            if kind == "add":
                sp.add_track(pc.spotify_id, x.id, pos)
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
    except (YouTubeError, SpotifyError) as e:
        # Later positions assume this op succeeded; the next run replans from the real state.
        rep.errors.append(str(e))

    record = state.data.setdefault("synced", {})
    if not rep.stopped and not rep.errors:
        new_pairs = {t.id: chosen[t.id] for t in tracks if t.id in chosen}
        new_pairs.update({t.id: vid for vid, t in adds.items()})
        record[pc.spotify_id] = {"complete": True, "pairs": new_pairs, "at": today.isoformat()}
    else:
        record[pc.spotify_id] = {**prior, "complete": False}
    state.save()
    return rep
