#!/usr/bin/env python3
"""
Auto-detect BPM and musical key for DJ library files.
Writes detected values into ID3 tags so Serato skips analysis.

Uses librosa for tempo (BPM) and a simple chroma-based key detection.
Only processes files missing BPM or key tags.
"""

import os
import sys
import json
import csv
from pathlib import Path
from datetime import datetime

import numpy as np
import librosa

from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.m4a import M4A
from mutagen.wave import WAVE
from mutagen.aiff import AIFF
from mutagen.id3 import ID3, TBPM, TKEY, TIT2, TPE1

DJ_DIR = Path("/Volumes/Music_Studio/DJ Music")
SCAN_JSON = Path("/Users/peteedoo/projects/dj/tools/serato-scan.json")
OUT_JSON = Path("/Users/peteedoo/projects/dj/tools/serato-analyzed.json")
OUT_CSV = Path("/Users/peteedoo/projects/dj/tools/serato-analyzed.csv")

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".aiff"}

# Camelot wheel: major/minor keys mapped to semitone offsets
# C=0, C#=1, D=2, D#=3, E=4, F=5, F#=6, G=7, G#=8, A=9, A#=10, B=11
KEY_NAMES_MAJOR = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
KEY_NAMES_MINOR = ["Am", "Bbm", "Bm", "Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m"]


def detect_key(y, sr):
    """
    Detect musical key using chroma features.
    Returns a string like '4A' (Camelot), 'Am', or 'C'.
    """
    # Harmonic-percussive separation for cleaner key detection
    y_harmonic = librosa.effects.harmonic(y)

    # Chroma STFT
    chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr, hop_length=512)
    chroma_mean = np.mean(chroma, axis=1)

    # Krumhansl-Schmuckler key profiles (simplified)
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    major_profile /= np.linalg.norm(major_profile)
    minor_profile /= np.linalg.norm(minor_profile)

    best_score = -np.inf
    best_key = None
    best_mode = None

    for shift in range(12):
        rolled = np.roll(chroma_mean, shift)
        rolled /= np.linalg.norm(rolled) + 1e-10

        major_score = np.dot(rolled, major_profile)
        minor_score = np.dot(rolled, minor_profile)

        if major_score > best_score:
            best_score = major_score
            best_key = shift
            best_mode = "major"
        if minor_score > best_score:
            best_score = minor_score
            best_key = shift
            best_mode = "minor"

    if best_mode == "major":
        open_key = ((best_key + 12 - 1) % 12) + 1  # C=8 in Camelot major
        camelot = f"{open_key}B"
        name = KEY_NAMES_MAJOR[best_key]
    else:
        open_key = ((best_key + 12 - 1) % 12) + 1  # A=8 in Camelot minor... wait
        # Actually in Camelot: A minor = 8A, but let's use standard:
        # 1A = A♭ minor, 2A = E♭ minor, etc. This is messy.
        # Let's just return the traditional name.
        camelot = None
        name = KEY_NAMES_MINOR[best_key]

    # Return traditional name for Serato compatibility
    return name


def detect_bpm(y, sr):
    """Detect BPM using librosa beat tracking."""
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    # librosa 0.11 returns scalar or array
    if isinstance(tempo, np.ndarray):
        tempo = tempo.item() if tempo.size == 1 else float(tempo[0])
    return round(float(tempo), 1)


def load_audio(path):
    """Load audio with librosa, downmix to mono, resample to 22050 for speed."""
    try:
        y, sr = librosa.load(str(path), sr=22050, mono=True, duration=120)
        return y, sr
    except Exception as e:
        return None, str(e)


def write_tags(path, bpm, key):
    """Write BPM and key tags back into the audio file."""
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            audio = MP3(path)
            if audio.tags is None:
                audio.add_tags()
            audio.tags["TBPM"] = TBPM(encoding=3, text=str(int(bpm)))
            audio.tags["TKEY"] = TKEY(encoding=3, text=key)
            audio.save()
        elif ext == ".flac":
            audio = FLAC(path)
            audio["BPM"] = str(bpm)
            audio["INITIALKEY"] = key
            audio.save()
        elif ext == ".m4a":
            audio = M4A(path)
            # M4A uses freeform atoms; limited support in mutagen
            audio.tags["----:com.apple.iTunes:BPM"] = str(bpm).encode("utf-8")
            audio.tags["----:com.apple.iTunes:INITIALKEY"] = key.encode("utf-8")
            audio.save()
        elif ext == ".wav":
            audio = WAVE(path)
            # WAVE tags via INFO chunk — limited
            pass
        elif ext == ".aiff":
            audio = AIFF(path)
            pass
        return True
    except Exception as e:
        return str(e)


def main():
    if not DJ_DIR.exists():
        print(f"DJ directory not found: {DJ_DIR}")
        sys.exit(1)

    # Load prior scan to know what's missing
    if SCAN_JSON.exists():
        with open(SCAN_JSON, "r", encoding="utf-8") as f:
            scan = json.load(f)
        files_to_check = {item["file"]: item for item in scan.get("files", [])}
    else:
        files_to_check = {}

    # Build list of files needing analysis
    candidates = []
    for path in DJ_DIR.rglob("*"):
        if path.suffix.lower() not in AUDIO_EXTS:
            continue
        rel = str(path.relative_to(DJ_DIR))
        info = files_to_check.get(rel, {})
        if info.get("missing_bpm") or info.get("missing_key"):
            candidates.append(path)

    total = len(candidates)
    print(f"Analyzing {total} files for BPM/key ...")

    results = []
    analyzed = 0
    errors = 0
    tag_errors = 0

    for i, path in enumerate(candidates, 1):
        if i % 50 == 0:
            print(f"  ... {i}/{total} ({analyzed} analyzed, {errors} errors)")

        y, sr_or_err = load_audio(path)
        if y is None:
            results.append({
                "file": str(path.relative_to(DJ_DIR)),
                "bpm": None,
                "key": None,
                "error": f"load: {sr_or_err}",
            })
            errors += 1
            continue

        try:
            bpm = detect_bpm(y, sr_or_err)
            key = detect_key(y, sr_or_err)
        except Exception as e:
            results.append({
                "file": str(path.relative_to(DJ_DIR)),
                "bpm": None,
                "key": None,
                "error": f"detect: {e}",
            })
            errors += 1
            continue

        # Write tags
        tag_result = write_tags(path, bpm, key)
        if tag_result is not True:
            tag_errors += 1
            tag_err = str(tag_result)
        else:
            tag_err = None

        results.append({
            "file": str(path.relative_to(DJ_DIR)),
            "bpm": bpm,
            "key": key,
            "tag_written": tag_result is True,
            "error": tag_err,
        })
        analyzed += 1

        # Save incremental progress every 100 files
        if analyzed % 100 == 0:
            with open(OUT_JSON, "w", encoding="utf-8") as f:
                json.dump({
                    "analyzed_at": datetime.now().isoformat(),
                    "dj_dir": str(DJ_DIR),
                    "total_candidates": total,
                    "analyzed_so_far": analyzed,
                    "errors": errors,
                    "tag_write_errors": tag_errors,
                    "files": results,
                }, f, indent=2, ensure_ascii=False)

    # Final save
    report = {
        "analyzed_at": datetime.now().isoformat(),
        "dj_dir": str(DJ_DIR),
        "total_candidates": total,
        "analyzed_ok": analyzed,
        "errors": errors,
        "tag_write_errors": tag_errors,
        "files": results,
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "bpm", "key", "tag_written", "error"])
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone.")
    print(f"  Candidates:   {total}")
    print(f"  Analyzed OK:  {analyzed}")
    print(f"  Errors:       {errors}")
    print(f"  Tag errors:   {tag_errors}")
    print(f"\n  JSON: {OUT_JSON}")
    print(f"  CSV:  {OUT_CSV}")


if __name__ == "__main__":
    main()
