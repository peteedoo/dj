#!/usr/bin/env python3
"""
Serato metadata pre-scanner.
Scans DJ music library and extracts key fields for Serato readiness:
- BPM (beats per minute)
- Key (musical key, e.g. 4A, 5B)
- Energy / Mood (from tags if present)
- File format, bitrate, sample rate
- Missing metadata flags

Outputs: JSON report + CSV for spreadsheet review
"""

import os
import sys
import json
import csv
from pathlib import Path
from datetime import datetime

from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.m4a import M4A
from mutagen.wave import WAVE
from mutagen.aiff import AIFF

DJ_DIR = Path("/Volumes/Music_Studio/DJ Music")
OUT_DIR = Path("/Users/peteedoo/projects/dj/tools")
OUT_JSON = OUT_DIR / "serato-scan.json"
OUT_CSV  = OUT_DIR / "serato-scan.csv"

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".aiff"}


def parse_key(tags):
    """Extract musical key from common tag fields."""
    for field in ("TKEY", "KEY", "INITIALKEY", "TKEY"):
        val = tags.get(field)
        if val:
            return str(val[0]) if hasattr(val, "__getitem__") else str(val)
    return None


def parse_bpm(tags):
    """Extract BPM from common tag fields."""
    for field in ("TBPM", "BPM"):
        val = tags.get(field)
        if val:
            try:
                return float(str(val[0]) if hasattr(val, "__getitem__") else str(val))
            except ValueError:
                pass
    return None


def scan_file(path: Path):
    """Scan a single audio file and return metadata dict."""
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            audio = MP3(path)
        elif ext == ".flac":
            audio = FLAC(path)
        elif ext == ".m4a":
            audio = M4A(path)
        elif ext == ".wav":
            audio = WAVE(path)
        elif ext == ".aiff":
            audio = AIFF(path)
        else:
            return None
    except Exception as e:
        return {
            "file": str(path),
            "error": str(e),
            "missing_bpm": True,
            "missing_key": True,
        }

    tags = audio.tags or {}
    info = audio.info

    bpm = parse_bpm(tags)
    key = parse_key(tags)

    # Title / Artist
    title = None
    artist = None
    for tfield in ("TIT2", "TITLE"):
        if tfield in tags:
            title = str(tags[tfield][0]) if hasattr(tags[tfield], "__getitem__") else str(tags[tfield])
            break
    for afield in ("TPE1", "ARTIST"):
        if afield in tags:
            artist = str(tags[afield][0]) if hasattr(tags[afield], "__getitem__") else str(tags[afield])
            break

    return {
        "file": str(path.relative_to(DJ_DIR)),
        "artist": artist,
        "title": title,
        "format": ext.lstrip(".").upper(),
        "bitrate_kbps": getattr(info, "bitrate", 0) // 1000 if hasattr(info, "bitrate") else None,
        "sample_rate_hz": getattr(info, "sample_rate", None),
        "channels": getattr(info, "channels", None),
        "length_sec": getattr(info, "length", None),
        "bpm": bpm,
        "key": key,
        "missing_bpm": bpm is None,
        "missing_key": key is None,
    }


def main():
    if not DJ_DIR.exists():
        print(f"DJ directory not found: {DJ_DIR}")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    files = [p for p in DJ_DIR.rglob("*") if p.suffix.lower() in AUDIO_EXTS]
    total = len(files)
    print(f"Scanning {total} audio files in {DJ_DIR} ...")

    results = []
    missing_bpm = 0
    missing_key = 0
    errors = 0

    for i, path in enumerate(files, 1):
        if i % 100 == 0:
            print(f"  ... {i}/{total}")
        result = scan_file(path)
        if result is None:
            continue
        results.append(result)
        if result.get("error"):
            errors += 1
        if result.get("missing_bpm"):
            missing_bpm += 1
        if result.get("missing_key"):
            missing_key += 1

    report = {
        "scanned_at": datetime.now().isoformat(),
        "dj_dir": str(DJ_DIR),
        "total_files": total,
        "scanned_ok": len(results) - errors,
        "errors": errors,
        "missing_bpm": missing_bpm,
        "missing_key": missing_key,
        "missing_bpm_pct": round(missing_bpm / total * 100, 1) if total else 0,
        "missing_key_pct": round(missing_key / total * 100, 1) if total else 0,
        "files": results,
    }

    # Write JSON
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Write CSV
    if results:
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "file", "artist", "title", "format", "bitrate_kbps",
                "sample_rate_hz", "channels", "length_sec", "bpm", "key",
                "missing_bpm", "missing_key", "error"
            ])
            writer.writeheader()
            writer.writerows(results)

    print(f"\nDone.")
    print(f"  Total files: {total}")
    print(f"  Scanned OK:  {len(results) - errors}")
    print(f"  Errors:      {errors}")
    print(f"  Missing BPM: {missing_bpm} ({report['missing_bpm_pct']}%)")
    print(f"  Missing Key: {missing_key} ({report['missing_key_pct']}%)")
    print(f"\n  JSON: {OUT_JSON}")
    print(f"  CSV:  {OUT_CSV}")


if __name__ == "__main__":
    main()
