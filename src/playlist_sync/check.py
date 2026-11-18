"""Read a day's run log and say what needs a person, in one line each; nothing when the run was clean.

Used by a morning routine after the nightly run. It reads the last run in `.state/logs/<date>.log`:

- no run logged that day, or a run that never printed its closing quota line (it died early);
- any `error:` line;
- a `stopped:` line, except a quota stop, which the next run continues by itself;
- a held playlist whose matching is finished and whose dry run has changes: it waits for approval.
"""
from __future__ import annotations

import re
from pathlib import Path

RUN = re.compile(r"^playlist-sync (?:dry )?run \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
SECTION = re.compile(r"^== (.+) ==$")
HELD = re.compile(r"^\(held: (.+) is matched and planned only")
CHANGES = re.compile(r"^(?:Spotify: )?would apply (\d+) change")


def last_run(text: str) -> list[str]:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if RUN.match(line)]
    return lines[starts[-1]:] if starts else []


def findings(text: str | None, day: str) -> list[str]:
    if text is None:
        return [f"playlist-sync: no run logged for {day}"]
    run = last_run(text)
    if not run:
        return [f"playlist-sync: no run logged for {day}"]
    out: list[str] = []
    held: set[str] = set()
    section, planned, stopped = "", {}, set()
    for line in run[1:]:
        if m := HELD.match(line):
            held.add(m.group(1))
        elif m := SECTION.match(line):
            section = m.group(1)
        elif line.startswith("error: "):
            where = f" {section}:" if section else ""
            out.append(f"playlist-sync{where} {line[:200]}")
        elif line.startswith("stopped: "):
            stopped.add(section)
            if not line.startswith("stopped: quota"):
                out.append(f"playlist-sync {section}: {line[:200]}")
        elif section and (m := CHANGES.match(line)):
            planned[section] = planned.get(section, 0) + int(m.group(1))
    for name in sorted(held):
        if name not in stopped and planned.get(name):
            out.append(f"playlist-sync: {name} is held and its dry run is ready ({planned[name]} changes); "
                       "approve it to sync")
    if not any(line.startswith("quota used today:") for line in run):
        out.append(f"playlist-sync: the {day} run didn't finish (last line: {run[-1][:120]!r})")
    return out


def check(state_dir: Path, day: str) -> list[str]:
    path = state_dir / "logs" / f"{day}.log"
    return findings(path.read_text(encoding="utf-8") if path.exists() else None, day)
