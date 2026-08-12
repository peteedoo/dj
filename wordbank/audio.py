import subprocess
from pathlib import Path


class AudioToolError(RuntimeError):
    """ffmpeg/ffprobe failed with captured stderr."""


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def duration(path: Path) -> float:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise AudioToolError(
            f"ffprobe failed: {(result.stderr or result.stdout or result.returncode)}"
        )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise AudioToolError(
            f"ffprobe returned non-numeric duration: {result.stdout!r}"
        ) from exc


def export_slice(
    source: Path,
    target: Path,
    start: float,
    end: float,
    fade_ms: int = 5,
) -> None:
    if end <= start:
        raise ValueError(f"Invalid slice window: start={start} end={end}")
    target.parent.mkdir(parents=True, exist_ok=True)
    length = end - start
    fade = min(fade_ms / 1000, length / 2)
    temp = target.with_name(target.name + ".partial.wav")
    try:
        result = _run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start:.6f}",
                "-i",
                str(source),
                "-t",
                f"{length:.6f}",
                "-af",
                (
                    f"afade=t=in:st=0:d={fade:.6f},"
                    f"afade=t=out:st={max(0, length - fade):.6f}:d={fade:.6f}"
                ),
                "-acodec",
                "pcm_s16le",
                str(temp),
            ]
        )
        if result.returncode != 0:
            raise AudioToolError(
                f"ffmpeg failed: {(result.stderr or result.stdout or result.returncode)}"
            )
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
