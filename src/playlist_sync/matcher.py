"""Pick the best YouTube video for a Spotify track.

Preference order: an official music video, then an official live or TV
performance, then official audio. "Official" means the artist's own channel,
its VEVO or Topic channel, or a channel listed in `trusted_channels`.
An unofficial upload only wins when nothing official matches.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from .spotify import Track
from .youtube import Video

VIDEO_MARKERS = ("official video", "official music video", "music video", "promo", "promo clip",
                 "official hd video", "videoclip")
LIVE_MARKERS = ("live", "midnight special", "top of the pops", "beat club", "in concert", "performance",
                "soul train", "bandstand", "whistle test", "rockpalast", "tv")
AUDIO_MARKERS = ("audio", "visualizer", "visualiser", "vinyl")
JUNK = ("lyric", "lyrics", "cover", "karaoke", "reaction", "tutorial", "lesson", "how to play", "learn",
        "remix", "8d", "slowed", "sped up", "instrumental", "nightcore", "full album", "guitar", "drum",
        "bass", "piano", "play along", "chords", "backing track")
TIER_WEIGHT = {"video": 3.0, "live": 2.0, "audio": 1.0, "upload": 0.5}

_BRACKETS = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_SUFFIX = re.compile(r"\s+-\s+.*$")  # " - 2004 Remaster", " - Single Version", " - Live"


@dataclass(frozen=True)
class Match:
    video_id: str | None
    confidence: str  # high | medium | low | none
    tier: str = ""
    score: float = 0.0
    title: str = ""
    channel: str = ""


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def core_title(title: str) -> str:
    return _SUFFIX.sub("", _BRACKETS.sub("", title)).strip() or title


def has(text: str, phrase: str) -> bool:
    return f" {phrase} " in f" {text} "


def artist_forms(artists: tuple[str, ...]) -> set[str]:
    forms = set()
    for artist in artists:
        n = norm(artist)
        forms.add(n)
        if n.startswith("the "):
            forms.add(n[4:])
    return forms


def is_official(channel: str, artists: tuple[str, ...], trusted: tuple[str, ...] = ()) -> bool:
    ch = norm(channel)
    ch_ns = ch.replace(" ", "")
    if ch in {norm(t) for t in trusted}:
        return True
    for form in artist_forms(artists):
        f = form.replace(" ", "")
        if ch_ns in {f, f + "vevo", "the" + f + "vevo", "official" + f, f + "official", f + "topic"}:
            return True
        # "Official Sweet Channel", "Deep Purple Official": only for names long enough not to collide.
        if len(f) >= 5 and f in ch_ns and ("official" in ch_ns or "vevo" in ch_ns or ch.endswith(" topic")):
            return True
    return False


def score(track: Track, video: Video, trusted: tuple[str, ...] = ()) -> Match | None:
    """Score one candidate, or None when it isn't this song at all."""
    if not video.available or not video.embeddable:
        return None
    want = norm(core_title(track.title)).split()
    vt = norm(video.title)
    tokens = set(vt.split())
    hit = sum(w in tokens for w in want) / len(want) if want else 0.0
    official = is_official(video.channel_title, track.artists, trusted)
    named = any(has(vt, form) for form in artist_forms(track.artists))
    if hit < 0.6 or not (official or named):
        return None

    track_text = norm(track.title)
    junk = [j for j in JUNK if has(vt, j) and not has(track_text, j)]
    art_track = video.description.startswith("Provided to YouTube by") or norm(video.channel_title).endswith(" topic")
    if art_track:
        tier = "audio"
    elif any(has(vt, m) for m in VIDEO_MARKERS):
        tier = "video"
    elif any(has(vt, m) for m in LIVE_MARKERS):
        tier = "live"
    elif any(has(vt, m) for m in AUDIO_MARKERS):
        tier = "audio"
    else:
        # A plainly titled upload on the artist's own channel is usually the video.
        tier = "video" if official else "upload"

    duration_ok = True
    if track.duration_ms and video.duration_s:
        ratio = video.duration_s * 1000 / track.duration_ms
        low, high = (0.6, 2.5) if tier == "live" else (0.75, 1.4)
        duration_ok = low <= ratio <= high

    points = (4 * hit + (2 if named else 0) + (5 if official else 0) + TIER_WEIGHT[tier]
              + 0.3 * math.log10(video.views + 1) - 3 * len(junk) - (0 if duration_ok else 3))

    if junk or not duration_ok or hit < 0.8:
        confidence = "low"
    elif official:
        confidence = "high"
    elif named:
        confidence = "medium"
    else:
        confidence = "low"
    return Match(video.id, confidence, tier, round(points, 2), video.title, video.channel_title)


def best(track: Track, candidates: list[Video], trusted: tuple[str, ...] = ()) -> Match:
    scored = [m for v in candidates if (m := score(track, v, trusted))]
    return max(scored, key=lambda m: m.score) if scored else Match(None, "none")


def search_query(track: Track) -> str:
    artist = track.artists[0] if track.artists else ""
    return f"{artist} {core_title(track.title)}".strip()
