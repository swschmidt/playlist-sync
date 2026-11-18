"""Clean up a Spotify playlist that an app keeps appending to (Shazam's "My Shazam Tracks").

Two rules, both optional per playlist:

- drop_owned: a song already in another playlist you own leaves this one.
- dedupe: each song stays once, the earliest copy.

"The same song" is the same track id, or the same full title and first artist; a remix or other
version with a different title is a different song. Spotify's remove call ignores positions and
takes out every copy of a track, so a repeated track is removed and its earliest copy re-added
at its old place (its "added" date becomes the day of the cleanup). Every apply writes a snapshot
of the playlist first, with each entry's position and date.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import matcher
from .config import TidyConfig
from .spotify import SpotifyClient


@dataclass
class Entry:
    pos: int
    added_at: str
    id: str
    name: str
    artist: str

    @property
    def key(self) -> tuple[str, str]:
        return matcher.norm(self.name), matcher.norm(self.artist)

    @property
    def label(self) -> str:
        return f"{self.name} – {self.artist}"


@dataclass
class TidyReport:
    name: str
    entries: int = 0
    remove_owned: list[tuple[Entry, list[str]]] = field(default_factory=list)
    remove_dupes: list[tuple[Entry, Entry]] = field(default_factory=list)
    after: int = 0
    applied: bool = False
    stopped: str = ""
    snapshot: Path | None = None
    errors: list[str] = field(default_factory=list)


def read_entries(sp: SpotifyClient, playlist_id: str) -> list[Entry]:
    out = []
    for pos, e in enumerate(sp._pages(f"/playlists/{playlist_id}/items", {"limit": 50})):
        t = e.get("item") or e.get("track") or {}
        if not t.get("id") or t.get("type", "track") != "track":
            continue
        artist = ((t.get("artists") or [{}])[0]).get("name") or ""
        out.append(Entry(pos, e.get("added_at") or "", t["id"], t.get("name") or "", artist))
    return out


def owned_songs(sp: SpotifyClient, exclude: str) -> dict:
    """Track ids and (title, artist) keys in every playlist the signed-in user owns, except `exclude`."""
    me = sp.get("/me").get("id")
    ids: dict[str, set[str]] = {}
    keys: dict[tuple[str, str], set[str]] = {}
    for p in sp._pages("/me/playlists", {"limit": 50}):
        if p["id"] == exclude or (p.get("owner") or {}).get("id") != me:
            continue
        for e in read_entries(sp, p["id"]):
            ids.setdefault(e.id, set()).add(p["name"])
            keys.setdefault(e.key, set()).add(p["name"])
    return {"ids": ids, "keys": keys}


def plan(entries: list[Entry], owned: dict | None, dedupe: bool) -> tuple[list, list, list[Entry]]:
    """(owned removals, duplicate removals, entries kept in playlist order)."""
    remove_owned, rest = [], []
    for e in entries:
        found = owned and (owned["ids"].get(e.id) or owned["keys"].get(e.key))
        if found:
            remove_owned.append((e, sorted(found)))
        else:
            rest.append(e)
    remove_dupes, first, kept = [], {}, []
    for e in sorted(rest, key=lambda e: (e.added_at, e.pos)):
        if dedupe and e.key in first:
            remove_dupes.append((e, first[e.key]))
            continue
        first.setdefault(e.key, e)
        kept.append(e)
    kept.sort(key=lambda e: e.pos)
    return remove_owned, remove_dupes, kept


def apply_plan(sp: SpotifyClient, playlist_id: str, removed: list[Entry], kept: list[Entry]) -> list[str]:
    """Remove every copy of each removed track, put back the kept copy of any repeated one; the
    playlist's track ids in order afterwards."""
    gone = {e.id for e in removed}
    uris = [f"spotify:track:{i}" for i in sorted(gone)]
    for i in range(0, len(uris), 100):
        sp._call("DELETE", f"/playlists/{playlist_id}/items", body={"items": [{"uri": u} for u in uris[i:i + 100]]})
    for n, e in enumerate(kept):          # ascending, so everything before it is already in place
        if e.id in gone:
            sp.add_track(playlist_id, e.id, n)
    return [e.id for e in read_entries(sp, playlist_id)]


def tidy_playlist(tc: TidyConfig, sp: SpotifyClient, state_dir: Path, dry_run: bool, force: bool = False,
                  owned: dict | None = None) -> TidyReport:
    rep = TidyReport(tc.name)
    entries = read_entries(sp, tc.spotify_id)
    rep.entries = len(entries)
    if tc.drop_owned and owned is None:
        owned = owned_songs(sp, tc.spotify_id)
    rep.remove_owned, rep.remove_dupes, kept = plan(entries, owned if tc.drop_owned else None, tc.dedupe)
    rep.after = len(kept)
    removals = len(rep.remove_owned) + len(rep.remove_dupes)
    if dry_run or not removals:
        return rep
    if removals > tc.max_remove and not force:
        rep.stopped = f"{removals} removals is over max_remove {tc.max_remove}; run `playlist-sync tidy --force`"
        return rep

    snap_dir = state_dir / "tidy"
    snap_dir.mkdir(parents=True, exist_ok=True)
    rep.snapshot = snap_dir / f"{datetime.now():%Y-%m-%d-%H%M%S}-{tc.spotify_id}.json"
    rep.snapshot.write_text(json.dumps({"playlist": tc.spotify_id, "name": tc.name,
                                        "entries": [asdict(e) for e in entries]}, indent=1, ensure_ascii=False),
                            encoding="utf-8")
    after = apply_plan(sp, tc.spotify_id, [e for e, _ in rep.remove_owned] + [e for e, _ in rep.remove_dupes], kept)
    rep.applied = True
    want = [e.id for e in kept]
    if after[:len(want)] != want:         # anything Shazam appended meanwhile sits after the kept entries
        rep.errors.append(f"after cleanup the playlist doesn't match the plan; snapshot {rep.snapshot}")
    return rep


def print_report(rep: TidyReport) -> None:
    removals = len(rep.remove_owned) + len(rep.remove_dupes)
    print(f"\n== tidy: {rep.name} ==")
    if not removals:
        print(f"{rep.entries} entries, nothing to remove")
        return
    verb = "removed" if rep.applied else "would remove"
    print(f"{rep.entries} -> {rep.after} entries: {verb} {len(rep.remove_owned)} already in your playlists, "
          f"{len(rep.remove_dupes)} duplicate(s)")
    for e, where in rep.remove_owned:
        print(f"  in {', '.join(where)}: {e.label}")
    for e, first in rep.remove_dupes:
        print(f"  duplicate (keeping {first.added_at[:10]}): {e.label}")
    if rep.stopped:
        print(f"stopped: {rep.stopped}")
    if rep.snapshot:
        print(f"snapshot: {rep.snapshot}")
    for err in rep.errors:
        print(f"error: {err}")
