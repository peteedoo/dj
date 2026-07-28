import subprocess
from pathlib import Path


def duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                            capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def export_slice(source: Path, target: Path, start: float, end: float, fade_ms: int = 5):
    target.parent.mkdir(parents=True, exist_ok=True)
    length = max(0.001, end - start)
    fade = min(fade_ms / 1000, length / 2)
    subprocess.run(["ffmpeg", "-y", "-ss", f"{start:.6f}", "-i", str(source), "-t", f"{length:.6f}",
                    "-af", f"afade=t=in:st=0:d={fade:.6f},afade=t=out:st={max(0, length-fade):.6f}:d={fade:.6f}",
                    "-acodec", "pcm_s16le", str(target)], capture_output=True, check=True)
