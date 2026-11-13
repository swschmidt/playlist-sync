# playlist-sync

Two-way sync between Spotify playlists and YouTube playlists that picks **music videos**, not just audio.

Spotify sets the order. For each playlist you list:

- every Spotify track is matched to the best YouTube video, and the YouTube playlist follows Spotify's order;
- a song added on YouTube that Spotify doesn't have is found with Spotify search and added to Spotify after its YouTube neighbour. If search can't find it, it stays on YouTube and is listed by `playlist-sync review`;
- a song removed on either side is removed from the other on the next run. Removals are detected against the pairs recorded at the end of the last complete run, so nothing is removed on a first run;
- duplicates on YouTube are deleted: a second copy of a video, another upload of a song already there, or a karaoke or cover copy of it. A song Spotify lists twice under different track IDs gets one YouTube copy.

Set `two_way = false` on a playlist for one-way Spotify → YouTube, where anything edited by hand on YouTube is overwritten. Change a pick by pinning it in `config.toml`.

## How a video is picked

Official uploads only (the artist's channel, VEVO, a record label or a trusted channel), ranked by class:

| Class | |
|---|---|
| 5 | official music video |
| 4 | official alternate video (dance, fan or alternate cut) |
| 3 | official lyric video or visualizer |
| 2 | official live, session or TV performance (e.g. *The Midnight Special*) |
| 1 | official audio (Topic channel or "Provided to YouTube" art track) |
| 0 | an unofficial upload, only when nothing official matches |

Remixes, covers, karaoke, sped-up and other different recordings are rejected unless the Spotify track is that recording.

**Nothing is downgraded.** A video already in the YouTube playlist is kept unless a strictly higher class is found; spare daily quota goes to searching for upgrades, lowest class first. Low-confidence picks are **not** added; they're listed by `playlist-sync review` so you can pin one. Pins under `[overrides]` always win.

## Limits you inherit

- **Spotify development mode** (February 2026 rules): the app owner needs Premium, and the API only returns the items of playlists you own or collaborate on.
- **YouTube quota:** 10,000 units a day. A search costs 100 and each insert, move or delete costs 50, so a new playlist fills at roughly 60 tracks a day. Matches are cached, so steady-state runs are cheap. A playlist isn't written until all of its tracks have been matched.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

1. **Spotify app.** In the [Spotify developer dashboard](https://developer.spotify.com/dashboard), create an app using the Web API with redirect URI `http://127.0.0.1:8888/callback`. Set `PLAYLIST_SYNC_SPOTIFY_CLIENT_ID` in the environment or `~/.env.local`. No client secret is needed (PKCE). Sign-in asks to read and modify your playlists; nothing else.
2. **Google OAuth client.** In Google Cloud:
   - create a project and enable **YouTube Data API v3**;
   - configure the OAuth consent screen (External), add the `.../auth/youtube` scope, and **publish it to "In production"**. In "Testing", refresh tokens expire after 7 days and scheduled runs stop. Google shows an "unverified app" warning at sign-in; that's expected for a personal app;
   - create an OAuth client of type **Desktop app** and save its JSON as `.state/google_client.json` (or point `PLAYLIST_SYNC_GOOGLE_CLIENT_FILE` at it).
3. Sign in once each. Refresh tokens are stored in the OS credential store.
   ```
   uv run playlist-sync auth spotify
   uv run playlist-sync auth youtube
   ```
4. `uv run playlist-sync playlists` lists your Spotify playlist IDs. Copy `config.example.toml` to `config.toml` and add the ones to sync.

## Use

```
uv run playlist-sync run --dry-run   # match and show the planned changes; writes nothing to Spotify or YouTube
uv run playlist-sync run             # sync
uv run playlist-sync review          # tracks that need a manual pick
uv run playlist-sync status          # quota used today, cache, playlist mapping
```

Runs from the repo directory, or set `PLAYLIST_SYNC_HOME`. `run --log` also appends output to `.state/logs/`.

### Daily schedule (Windows)

```powershell
$repo = (Get-Location).Path
$uv = (Get-Command uv).Source
$action = New-ScheduledTaskAction -Execute $uv -Argument 'run playlist-sync run --log' -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At 4am
Register-ScheduledTask -TaskName 'playlist-sync' -Action $action -Trigger $trigger -Description 'Spotify and YouTube playlist sync'
```

## Files

| Path | Tracked | Contents |
|---|---|---|
| `config.toml` | no | playlists, pins, trusted channels |
| `.state/matches.json` | no | cached track → video matches |
| `.state/state.json` | no | playlists created by the tool; track → video pairs from the last complete run |
| `.state/quota.json` | no | units spent today (Pacific) |
| `.state/review.md` | no | the latest review list |

## Privacy

playlist-sync runs on your own machine and has no server. It reads and edits the Spotify playlists you list and the matching YouTube playlists on your account. Nothing is sent anywhere else. OAuth refresh tokens are kept in your operating system's credential store, and match results stay in the local `.state/` folder. Revoke access at any time from your [Google account permissions](https://myaccount.google.com/permissions) and [Spotify apps](https://www.spotify.com/account/apps/) pages.

## License

MIT
