"""YouTube clip fetch via yt-dlp + ffmpeg."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .audio import AudioToolError, duration

TIMESTAMP = re.compile(
    r"^(?:"
    r"(?:(?P<hours>\d+):)?(?P<minutes>\d+):(?P<seconds>\d+(?:\.\d+)?)"
    r"|"
    r"(?P<plain>\d+(?:\.\d+)?)"
    r")$"
)

YOUTUBE_HINT = re.compile(
    r"(youtube\.com|youtu\.be|youtube-nocookie\.com)",
    re.IGNORECASE,
)

AUDIO_SUFFIXES = {".wav", ".m4a", ".mp3", ".webm", ".opus", ".ogg", ".flac", ".aac"}

DEFAULT_CLIP_SECONDS = 30.0
MAX_CLIP_SECONDS = 30 * 60
DOWNLOAD_TIMEOUT = 300
DURATION_TOLERANCE = 2.5


def parse_timestamp(value: str | float | int | None) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    match = TIMESTAMP.match(text)
    if match is None:
        raise ValueError(
            f"Invalid timestamp {value!r}; use seconds or MM:SS / HH:MM:SS"
        )
    if match.group("plain") is not None:
        return float(match.group("plain"))
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = float(match.group("seconds"))
    return hours * 3600 + minutes * 60 + seconds


def format_section(start: float, end: float) -> str:
    return f"*{start:.3f}-{end:.3f}"


def resolve_window(
    start: str | float | int | None = None,
    end: str | float | int | None = None,
    duration: str | float | int | None = None,
    default_duration: float = DEFAULT_CLIP_SECONDS,
) -> tuple[float, float]:
    if end is not None and end != "" and duration is not None and duration != "":
        raise ValueError("Provide end or duration, not both")
    start_s = parse_timestamp(start) or 0.0
    if start_s < 0:
        raise ValueError("start must be >= 0")
    end_s = parse_timestamp(end)
    duration_s = parse_timestamp(duration)
    if end_s is None and duration_s is None:
        end_s = start_s + default_duration
    elif end_s is None:
        end_s = start_s + float(duration_s)
    if end_s <= start_s:
        raise ValueError("end must be after start")
    length = end_s - start_s
    if length > MAX_CLIP_SECONDS:
        raise ValueError(
            f"Clip window {length:.1f}s exceeds {MAX_CLIP_SECONDS:.0f}s max"
        )
    return start_s, end_s


def looks_like_youtube(url: str) -> bool:
    return bool(YOUTUBE_HINT.search(url))


def _run(
    command: list[str],
    timeout: int = DOWNLOAD_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _pick_audio(temp_dir: Path) -> Path:
    preferred = list(temp_dir.glob("clip.wav"))
    if preferred:
        return preferred[0]
    candidates = [
        path
        for path in sorted(temp_dir.glob("clip.*"))
        if path.suffix.lower() in AUDIO_SUFFIXES and not path.name.endswith(".part")
    ]
    if not candidates:
        raise RuntimeError("yt-dlp produced no audio file")
    return candidates[0]


def fetch_youtube_audio(
    url: str,
    destination: Path,
    start: float,
    end: float,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, str | float]:
    """Download [start, end) audio from a YouTube URL into destination WAV."""
    if not looks_like_youtube(url):
        raise ValueError("URL does not look like a YouTube link")
    run = runner or _run
    if shutil.which("yt-dlp") is None and runner is None:
        raise RuntimeError(
            "yt-dlp not found on PATH; install with `pip install yt-dlp`"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wordbank-yt-") as temp_name:
        temp_dir = Path(temp_name)
        output_template = str(temp_dir / "clip.%(ext)s")
        info = run(
            [
                "yt-dlp",
                "--no-playlist",
                "--print",
                "%(.{id,title,webpage_url})j",
                url,
            ]
        )
        title = "youtube"
        video_id = "clip"
        webpage = url
        if info.returncode == 0 and info.stdout.strip():
            try:
                meta = json.loads(info.stdout.strip().splitlines()[0])
                title = str(meta.get("title") or title)
                video_id = str(meta.get("id") or video_id)
                webpage = str(meta.get("webpage_url") or webpage)
            except json.JSONDecodeError:
                pass

        try:
            download = run(
                [
                    "yt-dlp",
                    "--no-playlist",
                    "-f",
                    "bestaudio/best",
                    "-x",
                    "--audio-format",
                    "wav",
                    "--audio-quality",
                    "0",
                    "--download-sections",
                    format_section(start, end),
                    "--force-keyframes-at-cuts",
                    "-o",
                    output_template,
                    url,
                ]
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("yt-dlp timed out") from exc
        if download.returncode != 0:
            detail = (download.stderr or download.stdout or "").strip()
            raise RuntimeError(f"yt-dlp failed: {detail or download.returncode}")

        raw = _pick_audio(temp_dir)
        try:
            convert = run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(raw),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-acodec",
                    "pcm_s16le",
                    str(destination),
                ],
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffmpeg convert timed out") from exc
        if convert.returncode != 0:
            detail = (convert.stderr or convert.stdout or "").strip()
            raise RuntimeError(f"ffmpeg convert failed: {detail or convert.returncode}")

    try:
        actual = duration(destination)
    except AudioToolError as exc:
        raise RuntimeError(str(exc)) from exc
    expected = end - start
    if abs(actual - expected) > DURATION_TOLERANCE:
        raise RuntimeError(
            f"Downloaded clip is {actual:.2f}s but requested {expected:.2f}s "
            f"(start={start}, end={end}); try different cut points"
        )

    safe_title = re.sub(r"[^\w\s\-.]+", "", title).strip() or video_id
    filename = f"{safe_title}_{start:.0f}-{end:.0f}.wav"
    return {
        "title": title,
        "video_id": video_id,
        "url": webpage,
        "filename": filename,
        "start": start,
        "end": end,
        "actual_duration": actual,
    }
