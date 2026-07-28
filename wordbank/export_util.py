import re
from pathlib import Path


INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
WHITESPACE = re.compile(r"\s+")


def sanitize_label_filename(label: str, fallback: str = "sample") -> str:
    """Turn a sample label into a DJ-folder-safe basename (no extension)."""
    cleaned = INVALID_FILENAME.sub("", label).strip()
    cleaned = WHITESPACE.sub("_", cleaned)
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def unique_path(directory: Path, basename: str, suffix: str = ".wav") -> Path:
    candidate = directory / f"{basename}{suffix}"
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = directory / f"{basename}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
