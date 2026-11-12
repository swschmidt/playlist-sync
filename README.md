# playlist-sync

One-way sync from Spotify playlists to YouTube playlists that picks **music videos**, not just audio.

Spotify is the source of truth. For each playlist you list, every track is matched to the best YouTube video once, and the YouTube playlist is then made to match Spotify's tracks and order exactly. Anything edited by hand on YouTube is overwritten on the next run. Change a pick by pinning it in `config.toml` instead.

## How a video is picked

One search per new track; candidates are ranked:

1. official music video (artist, VEVO or label channel)
2. official live or TV performance (e.g. *The Midnight Special*, *Top of the Pops*)
3. official audio (Topic channel or "Provided to YouTube" art tracks)
4. an unofficial upload, only when nothing official matches

Lyric videos, covers, karaoke, remixes, lessons and wrong lengths are penalized. Low-confidence picks are **not** added; they're listed by `playlist-sync review` so you can pin one. Pins under `[overrides]` always win.

## Limits you inherit

- **Spotify development mode** (February 2026 rules): the app owner needs Premium, and the API only returns the items of playlists you own or collaborate on.
- **YouTube quota:** 10,000 units a day. A search costs 100 and each insert, move or delete costs 50, so a new playlist fills at roughly 60 tracks a day. Matches are cached, so steady-state runs are cheap. A playlist isn't written until all of its tracks have been matched.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

1. **Spotify app.** In the [Spotify developer dashboard](https://developer.spotify.com/dashboard), create an app using the Web API with redirect URI `http://127.0.0.1:8888/callback`. Set `PLAYLIST_SYNC_SPOTIFY_CLIENT_ID` in the environment or `~/.env.local`. No client secret is needed (PKCE).
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
uv run playlist-sync run --dry-run   # match and show the planned changes; writes nothing to YouTube
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
Register-ScheduledTask -TaskName 'playlist-sync' -Action $action -Trigger $trigger -Description 'Spotify to YouTube playlist sync'
```

## Files

| Path | Tracked | Contents |
|---|---|---|
| `config.toml` | no | playlists, pins, trusted channels |
| `.state/matches.json` | no | cached track → video matches |
| `.state/state.json` | no | Spotify → YouTube playlist IDs created by the tool |
| `.state/quota.json` | no | units spent today (Pacific) |
| `.state/review.md` | no | the latest review list |

## Privacy

playlist-sync runs on your own machine and has no server. It reads the Spotify playlists you list and edits the matching YouTube playlists on your account. Nothing is sent anywhere else. OAuth refresh tokens are kept in your operating system's credential store, and match results stay in the local `.state/` folder. Revoke access at any time from your [Google account permissions](https://myaccount.google.com/permissions) and [Spotify apps](https://www.spotify.com/account/apps/) pages.

## License

MIT
