"""Pick the best YouTube video for a Spotify track.

Every candidate gets a quality class, best first:

5  official music video
4  official alternate video (dance, alternate, fan or "unofficial" video on the artist's channel)
3  official lyric video or visualizer
2  official live, TV or session performance of a studio track
1  official audio (Topic art track, "Official Audio")
0  anything from a channel that isn't the artist's

"Official" means the artist's own channel, its VEVO or Topic channel, or a
channel listed in `trusted_channels`. A different recording (remix, cover,
karaoke, sped up, instrumental...) is not the track at all. Score breaks ties
within a class; a video already in the playlist is only replaced by a strictly
better class (see sync.py).
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
                "soul train", "bandstand", "whistle test", "rockpalast", "tv", "acoustic", "unplugged",
                "stripped", "session", "sessions", "in studio", "crowd sings", "snl", "rehearsal", "demo")
AUDIO_MARKERS = ("audio", "vinyl")
LYRIC_MARKERS = ("lyric", "lyrics", "visualizer", "visualiser")
ALTERNATE_MARKERS = ("alternate", "alt video", "dance video", "fan video", "unofficial video", "vertical video")
# A different recording or not the recording at all: never this track unless the track says so too.
OTHER_RECORDING = ("remix", "cover", "covers", "karaoke", "reaction", "tutorial", "lesson", "how to play",
                   "learn", "8d", "slowed", "sped up", "instrumental", "nightcore", "full album", "play along",
                   "chords", "backing track", "mashup", "tribute", "parody")
# Not bare "music": fan lyric channels use it too (Vibe Music). Labels named that way go in trusted_channels.
LABEL_SUFFIXES = ("records", "recordings", "music group", "musicgroup", "entertainment", "nashville")
# Record labels proper post their catalogue under bare titles (Epitaph Records: The Offspring - "Self Esteem"),
# so these need only the artist named; the looser suffixes above also need "official" in the title.
RECORD_LABEL = ("records", "recordings")
# Instrument-in-title noise (guitar lessons, drum covers): a penalty, since band names can contain them.
JUNK = ("guitar", "drum", "drums", "bass", "piano")
CLASS_TIER = {5: "video", 4: "video", 3: "lyric", 2: "live", 1: "audio", 0: "upload"}

_BRACKETS = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_SUFFIX = re.compile(r"\s+-\s+.*$")  # " - 2004 Remaster", " - Single Version", " - Live"
# Words a video title may carry beside the song's own title and artists.
TITLE_NOISE = {"official", "music", "video", "audio", "lyric", "lyrics", "visualizer", "version", "hd", "hq",
               "4k", "remaster", "remastered", "explicit", "clean", "clip", "mv", "feat", "ft", "featuring"}
MIN_TITLE_FIT = 0.6  # "You Belong With Me" is half of ME!'s words with "with" as noise


def title_fit(track: Track, video_title: str) -> float:
    """How much of the video's title part is this song, rather than a longer title that contains it.

    Coverage alone lets a one-word title ride inside another song: "ME!" is all there in
    "Look What You Made Me Do" (TayTay, 2026-11-23). The best part of the video title that
    holds a title word must be mostly words of the track's title, its artists or upload noise,
    or start with the song's title ("Tennessee Whiskey / Drink You Away", "Confessions, Pt. II").
    """
    order = norm(core_title(track.title)).split()
    want = set(order)
    artist_words = {w for a in track.artists for w in norm(a).split()}
    allowed = set(norm(track.title).split()) | artist_words | TITLE_NOISE
    title = _BRACKETS.sub(" ", video_title)
    best = 0.0
    for part in re.split(r"\s[-–—|:~]\s|\|", title):
        part = re.sub(r"(?i)\s(ft|feat|featuring)\.?\s.*$", " ", f" {part}")
        words = norm(part).split()
        if not (words and want & set(words)):
            continue
        lead = 0
        while lead < len(words) and words[lead] in (artist_words | TITLE_NOISE) - want:
            lead += 1
        if order and words[lead:lead + len(order)] == order:
            return 1.0
        # A year on an old upload ("Set A Drift On Memory Bliss 1991") is noise too.
        best = max(best, sum(w in allowed or re.fullmatch(r"(19|20)\d\d", w) is not None
                             for w in words) / len(words))
    return best


@dataclass(frozen=True)
class Match:
    video_id: str | None
    confidence: str  # high | medium | low | none
    tier: str = ""
    score: float = 0.0
    title: str = ""
    channel: str = ""
    title_hit: float = 0.0
    cls: int = -1


def norm(text: str) -> str:
    text = re.sub(r"['‘’`.]", "", text)  # Ain't/Ain’t -> aint, I.D. -> ID, U.S.A. -> USA
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
        if ch_ns in {f, f + "vevo", "the" + f + "vevo", "official" + f, f + "official", f + "topic", f + "videos"}:
            return True
        # "Official Sweet Channel", "Deep Purple Official": only for names long enough not to collide.
        if len(f) >= 5 and f in ch_ns and ("official" in ch_ns or "vevo" in ch_ns or ch.endswith(" topic")):
            return True
    return False


def _marked(text: str, markers: tuple[str, ...]) -> bool:
    return any(has(text, m) for m in markers)


def quality(track: Track, video: Video, official: bool) -> int:
    if not official:
        return 0
    vt, tt = norm(video.title), norm(track.title)
    art_track = video.description.startswith("Provided to YouTube by") or norm(video.channel_title).endswith(" topic")
    variant = _marked(vt, LIVE_MARKERS) != _marked(tt, LIVE_MARKERS)
    if art_track or (_marked(vt, AUDIO_MARKERS) and not _marked(vt, VIDEO_MARKERS)):
        return 1
    if variant:
        return 2
    if _marked(vt, LYRIC_MARKERS):
        return 3
    if _marked(vt, ALTERNATE_MARKERS):
        return 4
    return 5


def score(track: Track, video: Video, trusted: tuple[str, ...] = ()) -> Match | None:
    """Score one candidate, or None when it isn't this recording at all."""
    if not video.available or not video.embeddable:
        return None
    want = norm(core_title(track.title)).split()
    vt = norm(video.title)
    tt = norm(track.title)
    tokens = set(vt.split())
    hit = sum(w in tokens for w in want) / len(want) if want else 0.0
    named = any(has(vt, form) for form in artist_forms(track.artists))
    # VEVO channels only carry label uploads, so one naming the artist is official even when
    # its handle is abbreviated (FlaGeorgiaLineVEVO, RusselledVEVO).
    # Record-label channels (Atlantic Records, Curb Records, NettwerkMusic) post the official video
    # under the label's name: count one that names the artist and calls itself official.
    ch = norm(video.channel_title)
    ch_ns = ch.replace(" ", "")
    label = ch_ns.endswith(RECORD_LABEL) or (has(vt, "official") and ch_ns.endswith(LABEL_SUFFIXES))
    official = is_official(video.channel_title, track.artists, trusted) or (
        named and (ch_ns.endswith("vevo") or label))
    if hit < 0.6 or not (official or named) or title_fit(track, video.title) < MIN_TITLE_FIT:
        return None
    if any(has(vt, o) != has(tt, o) for o in OTHER_RECORDING if has(vt, o) or o == "remix"):
        return None

    cls = quality(track, video, official)
    junk = [j for j in JUNK if has(vt, j) and not has(tt, j)]
    duration_ok = True
    if track.duration_ms and video.duration_s:
        ratio = video.duration_s * 1000 / track.duration_ms
        low, high = {5: (0.75, 2.0), 4: (0.75, 2.0), 2: (0.6, 2.5)}.get(cls, (0.75, 1.4))
        duration_ok = low <= ratio <= high

    points = (4 * hit + (2 if named else 0) + 0.3 * math.log10(video.views + 1)
              - 3 * len(junk) - (0 if duration_ok else 3)
              # Between two official uploads, the one that says "Official Music Video" over a bare title.
              + (0.5 if cls >= 4 and _marked(vt, VIDEO_MARKERS) else 0))

    if hit < 0.8 or junk or (not official and not duration_ok):
        confidence = "low"
    elif official:
        confidence = "high"
    elif named:
        confidence = "medium"
    else:
        confidence = "low"
    return Match(video.id, confidence, CLASS_TIER[cls], round(points, 2), video.title, video.channel_title,
                 round(hit, 2), cls)


def rank(m: Match) -> tuple:
    """Sort key: a confident match first, then class, then score."""
    return (m.confidence in ("high", "medium"), m.cls, m.score)


def best(track: Track, candidates: list[Video], trusted: tuple[str, ...] = ()) -> Match:
    scored = [m for v in candidates if (m := score(track, v, trusted))]
    return max(scored, key=rank) if scored else Match(None, "none")


def search_query(track: Track) -> str:
    artist = track.artists[0] if track.artists else ""
    return f"{artist} {core_title(track.title)}".strip()
