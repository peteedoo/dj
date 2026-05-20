# dj

Personal DJ toolkit — music sync automation, crate tracking, and setlist notes. Built around Serato DJ Pro on a 2TB Music_Studio SSD, pulling from a NAS music library.

## Structure

```
sync/     NAS → SSD copy script + launchd agent
crates/   Crate definitions and track notes
sets/     Setlist logs
tools/    Utility scripts (BPM helpers, playlist exporters, etc.)
```

## Setup

### Music sync

Copies curated hip-hop, electronic, and funk folders from NAS (`/Volumes/homelab/media/music`) to the DJ SSD (`/Volumes/Music_Studio/DJ Music`).

```bash
# Run manually
./sync/dj-music-sync.sh

# Check what's running / log
tail -f /tmp/dj-music-sync.log
```

**Auto-sync (3am nightly via launchd):**

```bash
cp sync/com.iamfaulty.dj-music-sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.iamfaulty.dj-music-sync.plist
```

To add an artist: open `sync/dj-music-sync.sh` and append the folder name to the `FOLDERS` array under the right genre section.

## Crates

See `crates/` — one file per crate. Format is freeform but track → notes works well.

## Sets

See `sets/` — one file per set or session. Date-named (`2026-05-20-practice.md`).
