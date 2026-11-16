"""Command line: auth, playlists, run, status, review."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import config, spotify, youtube
from .config import ConfigError
from .quota import Quota
from .sync import JsonStore, MatchCache, Report, sync_playlist


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            s.write(text)

    def flush(self):
        for s in self.streams:
            s.flush()


def _print_report(rep: Report, dry_run: bool) -> None:
    print(f"\n== {rep.name} ==")
    print(f"tracks {rep.tracks}, matched {rep.matched} (kept from YouTube {rep.adopted}, searches "
          f"{rep.searched}, upgrades {rep.upgraded}), youtube {rep.youtube_id or '-'}")
    if rep.spotify_ops:
        print(f"Spotify: {'would apply' if dry_run else 'applied'} {len(rep.spotify_ops)} change(s):")
        for line in rep.spotify_ops:
            print(f"  {line}")
    if rep.ops:
        verb = "would apply" if dry_run else f"applied {rep.done} of"
        print(f"{verb} {len(rep.ops)} change(s):")
        for line in rep.ops:
            print(f"  {line}")
    elif not rep.spotify_ops and not rep.stopped and not rep.errors:
        print("in sync")
    if rep.stopped:
        print(f"stopped: {rep.stopped}; the next run continues")
    for e in rep.errors:
        print(f"error: {e}")
    if rep.review:
        print(f"{len(rep.review)} track(s) need review (playlist-sync review)")


def _write_review(path: Path, reports: list[Report]) -> None:
    lines = [f"# Review ({datetime.now():%Y-%m-%d %H:%M})", ""]
    for rep in reports:
        if rep.review:
            lines += [f"## {rep.name}", *rep.review, ""]
    if len(lines) == 2:
        lines.append("Nothing to review.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_auth(args) -> int:
    if args.service == "spotify":
        spotify.authorize(config.env("PLAYLIST_SYNC_SPOTIFY_CLIENT_ID"))
    else:
        home = config.home_dir()
        client = config.env("PLAYLIST_SYNC_GOOGLE_CLIENT_FILE", required=False)
        youtube.authorize(Path(client) if client else home / ".state" / "google_client.json")
    print(f"{args.service}: signed in; refresh token saved to the OS credential store")
    return 0


def cmd_playlists(args) -> int:
    sp = spotify.SpotifyClient(config.env("PLAYLIST_SYNC_SPOTIFY_CLIENT_ID"))
    for p in sp.my_playlists():
        print(f"{p['id']}  {p['total'] if p['total'] is not None else '?':>5}  {p['name']}  ({p['owner']})")
    return 0


def cmd_youtube_playlists(args) -> int:
    cfg = config.load()
    quota = Quota(cfg.state_dir / "quota.json", cfg.daily_budget)
    yt = youtube.YouTubeClient(youtube.credentials(cfg.google_client_file), quota, cfg.region)
    for p in yt.my_playlists():
        print(f"{p['id']}  {p['total']:>5}  {p['name']}  ({p['privacy']})")
    return 0


def cmd_run(args) -> int:
    cfg = config.load()
    log = None
    if args.log:
        log_path = cfg.state_dir / "logs" / f"{datetime.now():%Y-%m-%d}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "a", encoding="utf-8")
        sys.stdout = _Tee(sys.__stdout__, log)
    try:
        print(f"playlist-sync {'dry run' if args.dry_run else 'run'} {datetime.now():%Y-%m-%d %H:%M}")
        quota = Quota(cfg.state_dir / "quota.json", cfg.daily_budget)
        sp = spotify.SpotifyClient(config.env("PLAYLIST_SYNC_SPOTIFY_CLIENT_ID"))
        yt = youtube.YouTubeClient(youtube.credentials(cfg.google_client_file), quota, cfg.region)
        cache = MatchCache(cfg.state_dir / "matches.json")
        state = JsonStore(cfg.state_dir / "state.json")

        reports, failed = [], False
        chosen = [pc for pc in cfg.playlists if not args.only or pc.name == args.only]
        # Live playlists first so their daily writes aren't starved; while any playlist is held or
        # hasn't finished its first sync, spare quota goes to its matching rather than to upgrade
        # searches on the others (which run first and would otherwise spend the whole day's budget).
        chosen.sort(key=lambda pc: pc.hold)
        synced = state.data.get("synced", {})
        any_held = any(pc.hold or not synced.get(pc.spotify_id, {}).get("complete") for pc in chosen)
        for pc in chosen:
            dry = args.dry_run or pc.hold
            try:
                rep = sync_playlist(pc, cfg, sp, yt, cache, state, dry, upgrades=not any_held)
            except (spotify.SpotifyError, youtube.YouTubeError) as e:
                rep = Report(pc.name, errors=[str(e)])
            failed |= bool(rep.errors)
            reports.append(rep)
            if pc.hold and not args.dry_run:
                print(f"\n(held: {pc.name} is matched and planned only; set hold = false to sync it)")
            _print_report(rep, dry)

        _write_review(cfg.state_dir / "review.md", reports)
        print(f"\nquota used today: {quota.used} of {quota.budget}")
        return 1 if failed else 0
    finally:
        if log:
            sys.stdout = sys.__stdout__
            log.close()


def cmd_status(args) -> int:
    cfg = config.load()
    quota = Quota(cfg.state_dir / "quota.json", cfg.daily_budget)
    cache = MatchCache(cfg.state_dir / "matches.json")
    state = JsonStore(cfg.state_dir / "state.json")
    counts: dict[str, int] = {}
    for entry in cache.data.values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    print(f"quota today ({quota.day} Pacific): {quota.used} of {quota.budget} units")
    print(f"match cache: {counts or 'empty'}; pinned overrides: {len(cfg.overrides)}")
    for pc in cfg.playlists:
        yt_id = pc.youtube_id or state.data.get("playlists", {}).get(pc.spotify_id, "(not created yet)")
        print(f"- {pc.name}: spotify {pc.spotify_id} -> youtube {yt_id}")
    return 0


def cmd_review(args) -> int:
    path = config.load().state_dir / "review.md"
    print(path.read_text(encoding="utf-8") if path.exists() else "No review file yet; run a sync first.")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252, which can't print emoji playlist names or en dashes.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="playlist-sync", description="One-way Spotify to YouTube playlist sync.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    auth = sub.add_parser("auth", help="sign in and store a refresh token")
    auth.add_argument("service", choices=["spotify", "youtube"])
    sub.add_parser("playlists", help="list your Spotify playlists with their IDs")
    sub.add_parser("youtube-playlists", help="list your YouTube playlists with their IDs")
    run = sub.add_parser("run", help="sync the playlists in config.toml")
    run.add_argument("--dry-run", action="store_true", help="match and plan, but write nothing to YouTube")
    run.add_argument("--only", metavar="NAME", help="sync one playlist by its config name")
    run.add_argument("--log", action="store_true", help="also append output to .state/logs/<date>.log")
    sub.add_parser("status", help="quota, cache and playlist mapping")
    sub.add_parser("review", help="tracks that need a manual pick")
    args = parser.parse_args(argv)

    handlers = {"auth": cmd_auth, "playlists": cmd_playlists, "youtube-playlists": cmd_youtube_playlists,
                "run": cmd_run, "status": cmd_status, "review": cmd_review}
    try:
        return handlers[args.cmd](args)
    except (ConfigError, spotify.SpotifyError, youtube.YouTubeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
